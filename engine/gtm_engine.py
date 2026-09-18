#!/usr/bin/env python3
"""
MarsLab GTM Engine v1.0 - reusable account -> prospect -> qualification engine.
Current config: challenger building-materials dealer-distributor ops (config/icp-config-v1.json).
Spec: ICP Config -> Discovery -> Dedup -> Fit -> Intelligence -> Signal Detection -> Verification -> Buyer Mapping -> Contact Discovery -> Verification -> Qualification -> Human Review -> Outreach Readiness -> CRM/Experiment.
Store: SQLite (design/schemas.sql) with provenance. Export CSV/JSON/CRM. No vendor hard-coded.
Usage:
  python gtm_engine.py init --db gtm.db
  python gtm_engine.py load-candidates --db gtm.db --csv ../accounts/candidates.csv
  python gtm_engine.py dedup --db gtm.db
  python gtm_engine.py fit --db gtm.db --config ../config/icp-config-v1.json
  python gtm_engine.py qualify --db gtm.db
  python gtm_engine.py outreach --db gtm.db
  python gtm_engine.py export --db gtm.db --out ../accounts/outreach-ready.csv
  python gtm_engine.py costs --db gtm.db
"""
import argparse, csv, json, sqlite3, re, sys, os, hashlib, datetime
from difflib import SequenceMatcher

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "design", "schemas.sql")

def db_connect(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con

def cmd_init(db):
    con = db_connect(db)
    sql = open(SCHEMA_PATH, encoding="utf-8").read()
    con.executescript(sql)
    con.commit()
    print(f"init ok: {db}")

def norm_phone(p):
    if not p: return ""
    d = re.sub(r"\D", "", p)
    if len(d) == 10: d = "91" + d
    if d.startswith("0"): d = "91" + d[1:]
    return "+" + d if d else ""

def norm_name(n):
    return re.sub(r"\s+", " ", (n or "").lower().strip())

def cmd_load(db, csv_path):
    con = db_connect(db)
    n = 0
    with open(csv_path, encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            cid = row.get("account_id") or ("A-" + hashlib.md5(norm_name(row.get("company_name","")).encode()).hexdigest()[:6])
            con.execute("""INSERT OR REPLACE INTO accounts
              (account_id, company_name, domain, hq_city, hq_state, regions, industry, sub_vertical, business_model,
               revenue_inr_cr, employee_band, field_reps_est, dealers_est, depots, system_env, entity_confidence, fit_tier, fit_reasons)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (cid, row.get("company_name"), row.get("domain"), row.get("hq_city"), row.get("hq_state"),
               row.get("regions"), row.get("industry"), row.get("sub_vertical"), row.get("business_model"),
               row.get("revenue_inr_cr"), row.get("employee_band"), row.get("field_reps_est") or None,
               row.get("dealers_est") or None, row.get("depots") or None, row.get("system_env"),
               row.get("entity_confidence","medium"), row.get("fit_tier","unscored"), row.get("fit_reasons","")))
            # log experiment stage
            con.execute("INSERT INTO experiments (run_id, account_id, stage, outcome) VALUES (?,?,?,?)",
                        ("load", cid, "discovered", "pass"))
            n += 1
    con.commit()
    print(f"loaded {n} accounts from {csv_path}")

def cmd_dedup(db):
    con = db_connect(db)
    rows = list(con.execute("SELECT * FROM accounts"))
    seen = {}
    dups = 0
    for r in rows:
        key = (norm_name(r["company_name"]), (r["hq_city"] or "").lower())
        # fuzzy: same normalized name
        if key in seen:
            dups += 1
            con.execute("INSERT INTO experiments (run_id, account_id, stage, outcome) VALUES (?,?,?,?)",
                        ("dedup", r["account_id"], "dedup", f"kill:duplicate-of {seen[key]}"))
            con.execute("UPDATE accounts SET fit_tier='Reject', fit_reasons='duplicate' WHERE account_id=?", (r["account_id"],))
        else:
            # near-duplicate names same city
            dup = None
            for k, v in seen.items():
                if k[1] == key[1] and SequenceMatcher(None, k[0], key[0]).ratio() > 0.88:
                    dup = v; break
            if dup:
                dups += 1
                con.execute("INSERT INTO experiments (run_id, account_id, stage, outcome) VALUES (?,?,?,?)",
                            ("dedup", r["account_id"], "dedup", f"kill:near-duplicate-of {dup}"))
                con.execute("UPDATE accounts SET fit_tier='Reject', fit_reasons='near-duplicate' WHERE account_id=?", (r["account_id"],))
            else:
                seen[key] = r["account_id"]
    con.commit()
    print(f"dedup ok: {len(rows)} scanned, {dups} duplicates killed")

def _num(x):
    try:
        if x is None or str(x).strip().upper() in ("","UNKNOWN"): return None
        m = re.search(r"[\d.]+", str(x).replace(",",""))
        return float(m.group()) if m else None
    except: return None

def cmd_fit(db, config_path):
    cfg = json.load(open(config_path, encoding="utf-8"))
    sweet = cfg["company"]["size"]["revenue_inr_cr"]["sweet_spot"]
    con = db_connect(db)
    rows = list(con.execute("SELECT * FROM accounts WHERE fit_tier NOT IN ('Reject')"))
    for r in rows:
        reasons = []
        tier = "Tier-3"
        rev = _num(r["revenue_inr_cr"])
        dealers = _num(r["dealers_est"])
        reps = _num(r["field_reps_est"])
        vert_ok = (r["industry"] or "").lower() in ["paints","pipes","cement","ceramics","sanitaryware","plywood","hardware","polymer","bathware","building-materials","building_materials"] or "paint" in (r["sub_vertical"] or "").lower() or r["sub_vertical"] is not None
        size_ok = (rev is None) or (260 <= rev <= 2600)
        scale_ok = ((dealers is not None and dealers >= 200) or (reps is not None and reps >= 25) or (dealers is None and reps is None))
        # check signals for trigger + pain artifact
        sigs = list(con.execute("SELECT * FROM signals WHERE account_id=?", (r["account_id"],)))
        verified_strong = [s for s in sigs if s["verification_status"]=="verified" and (s["strength"] or 0) >= 4]
        pain = [s for s in sigs if s["signal_class"]=="operational_pain" and s["verification_status"]=="verified"]
        tech = [s for s in sigs if s["signal_class"]=="technology" and s["verification_status"]=="verified"]
        trig = len(verified_strong) >= 1
        if not vert_ok: reasons.append("vertical edge - review")
        if rev is not None and (rev < 100): tier="Reject"; reasons.append("revenue <100cr cannot pay custom")
        elif rev is not None and rev > 5000: tier="Tier-3"; reasons.append(">5000cr enterprise motion, lookalike only")
        elif rev is not None and rev > 2600: tier="Tier-2"; reasons.append("upper-mid 2600-5000cr: enterprise-leaning, nurture")
        elif rev is None and not trig: tier="Tier-3"; reasons.append("revenue UNKNOWN + no verified trigger: watch only")
        elif size_ok and scale_ok and (len(pain)>=1 or len(tech)>=1) and trig:
            tier="Tier-1"; reasons.append(f"fit + trigger + pain/tech artifact ({len(verified_strong)} verified strong)")
        elif size_ok and scale_ok:
            tier="Tier-2"; reasons.append("fit + scale, weak/unclear trigger")
        else:
            tier="Tier-3"; reasons.append("industry/company fit, weak problem evidence")
            if dealers is not None and dealers < 50: reasons.append("unit-economics risk (<50 dealers)")
        con.execute("UPDATE accounts SET fit_tier=?, fit_reasons=? WHERE account_id=?", (tier, "; ".join(reasons), r["account_id"]))
        con.execute("INSERT INTO experiments (run_id, account_id, stage, outcome) VALUES (?,?,?,?)", ("fit", r["account_id"], "fit", tier))
    con.commit()
    print(f"fit ok: {len(rows)} scored against {cfg['icp_id']}")

SCORE_WEIGHTS = {"icp":20,"problem":20,"trigger":15,"pain":15,"buyer":10,"solution":5,"pilot":5,"engage":5,"commercial":5}

def cmd_qualify(db):
    con = db_connect(db)
    rows = list(con.execute("SELECT * FROM accounts WHERE fit_tier IN ('Tier-1','Tier-2')"))
    for r in rows:
        q = con.execute("SELECT * FROM qualification WHERE account_id=?", (r["account_id"],)).fetchone()
        sigs = list(con.execute("SELECT * FROM signals WHERE account_id=?", (r["account_id"],)))
        people = list(con.execute("SELECT * FROM people WHERE account_id=?", (r["account_id"],)))
        # heuristic scoring from evidence presence (human overrides in review)
        s_icp = 4 if r["fit_tier"]=="Tier-1" else 3
        s_prob = 4 if any(s["signal_class"]=="operational_pain" and s["verification_status"]=="verified" for s in sigs) else 2
        s_trig = 4 if any((s["strength"] or 0)>=4 and s["verification_status"]=="verified" for s in sigs) else 2
        s_pain = 3  # requires glue-cost math in human review to reach 4-5
        s_buyer = 3 if len(people)>=2 else 1
        s_sol = 3 if r["system_env"] else 1
        s_pilot = 2  # requires baseline+control agreement
        s_eng = 1; s_com = 1  # require behavior
        total = round(s_icp/5*20 + s_prob/5*20 + s_trig/5*15 + s_pain/5*15 + s_buyer/5*10 + s_sol/5*5 + s_pilot/5*5 + s_eng/5*5 + s_com/5*5)
        force = {"F":"Yellow","O":"Yellow","R":"Red","C":"Yellow","E":"Yellow"}
        if any(s["signal_class"]=="operational_pain" and s["verification_status"]=="verified" for s in sigs): force["F"]="Green"
        if len(people)>=1: force["O"]="Yellow"
        if any((s["strength"] or 0)>=4 for s in sigs): force["E"]="Green"
        lifecycle = "Prospect"
        greens = sum(1 for v in force.values() if v=="Green")
        if total >= 65 and force["F"]=="Green" and force["O"]=="Green" and greens>=4: lifecycle = "Qualified Lead"
        if total >= 80 and s_eng >= 3 and greens>=4: lifecycle = "High-Intent Opportunity"
        if total >= 65 and lifecycle=="Prospect": lifecycle = "Prospect (score-met, FORCE-gated)"
        if q:
            con.execute("""UPDATE qualification SET force_f=?,force_o=?,force_r=?,force_c=?,force_e=?,force_evidence=?,
              score_icp=?,score_problem=?,score_trigger=?,score_pain=?,score_buyer=?,score_solution=?,score_pilot=?,score_engage=?,score_commercial=?,
              total_score=?,lifecycle=?,next_action=?,updated_at=? WHERE account_id=?""",
              (force["F"],force["O"],force["R"],force["C"],force["E"],"auto-heuristic; human review required",
               s_icp,s_prob,s_trig,s_pain,s_buyer,s_sol,s_pilot,s_eng,s_com,total,lifecycle,
               "human review: glue-cost math + enforcer + finance-data + kill-line", datetime.datetime.now().isoformat(), r["account_id"]))
        else:
            con.execute("""INSERT INTO qualification (account_id,force_f,force_o,force_r,force_c,force_e,force_evidence,
              score_icp,score_problem,score_trigger,score_pain,score_buyer,score_solution,score_pilot,score_engage,score_commercial,
              total_score,lifecycle,next_action) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (r["account_id"],force["F"],force["O"],force["R"],force["C"],force["E"],"auto-heuristic; human review required",
               s_icp,s_prob,s_trig,s_pain,s_buyer,s_sol,s_pilot,s_eng,s_com,total,lifecycle,
               "human review: glue-cost math + enforcer + finance-data + kill-line"))
        con.execute("INSERT INTO experiments (run_id, account_id, stage, outcome) VALUES (?,?,?,?)", ("qualify", r["account_id"], "qualification", f"{lifecycle}:{total}"))
    con.commit()
    print(f"qualify ok: {len(rows)} scored (thresholds >=65 Qualified, >=80 High-Intent + FORCE gates)")

def cmd_outreach(db):
    con = db_connect(db)
    rows = list(con.execute("""SELECT a.*, q.total_score, q.lifecycle FROM accounts a JOIN qualification q ON a.account_id=q.account_id
      WHERE q.lifecycle IN ('Qualified Lead','High-Intent Opportunity')"""))
    n=0
    for r in rows:
        p = con.execute("SELECT * FROM people WHERE account_id=? LIMIT 1", (r["account_id"],)).fetchone()
        if not p: continue
        sig = con.execute("SELECT * FROM signals WHERE account_id=? AND verification_status='verified' ORDER BY strength DESC LIMIT 1", (r["account_id"],)).fetchone()
        obs = (sig["description"] if sig else "recent expansion/distribution footprint") + f" (source: {sig['source_url']+', '+sig['event_date'] if sig and sig['source_url'] else 'to-verify'})"
        hypo = "dealer follow-up + secondary/claim visibility handled in WhatsApp/Excel with manager chasing manually"
        proof = "KREPL: 500 reps GPS-verified + reporting 60->10min; fleet: 600-truck live platform" if "pipe" in (r["sub_vertical"] or "").lower() or True else "Bhajanlal: 10k calls/day voice qualification"
        opener = (f"I noticed {obs} at {r['company_name']}. It made me wonder whether {hypo} is creating missed secondary or claim delays. "
                  f"We worked on a similar problem (MarsLab: {proof}). Is this a bottleneck for your team, or am I misreading it?")
        if len(opener.split()) > 90:
            opener = " ".join(opener.split()[:90])
        con.execute("""INSERT OR REPLACE INTO outreach_ready (account_id, person_id, observation, workflow_hypothesis, proof_point, opener, evidence_refs, approved)
          VALUES (?,?,?,?,?,?,?,?)""",
          (r["account_id"], p["person_id"], obs, hypo, proof, opener, sig["source_url"] if sig else "", False))
        n+=1
    con.commit()
    print(f"outreach packs drafted: {n} (approved=FALSE; human review required)")

def cmd_export(db, out):
    con = db_connect(db)
    rows = list(con.execute("""SELECT a.company_name,a.sub_vertical,a.hq_city,a.fit_tier,q.total_score,q.lifecycle,o.opener
      FROM accounts a LEFT JOIN qualification q ON a.account_id=q.account_id LEFT JOIN outreach_ready o ON a.account_id=o.account_id"""))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["company_name","sub_vertical","hq_city","fit_tier","total_score","lifecycle","opener"])
        w.writeheader()
        for r in rows: w.writerow(dict(r))
    print(f"exported {len(rows)} rows -> {out}")

def cmd_costs(db):
    # transparent unit-economics stub: reads experiments + fixed assumptions from cost model
    print("Cost buckets: LLM/search/data/people/verify/runtime/storage/human. See final/cost-model.md.")
    print("Lean test economics: ~$0.85-1.35/account/100; variable = EasyLeadz mobiles. Log per-run costs in experiments.cost_*.")

def _auto_state(note):
    # MASTER-STATE.md must be updated after EVERY change. Never break the engine if updater fails.
    try:
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        upd = os.path.join(root, "scripts", "update_state.py")
        if os.path.exists(upd) and os.path.exists(os.path.join(root, "MASTER-STATE.md")):
            subprocess.run([sys.executable, upd, "--note", note, "--actor", "engine"],
                           cwd=root, timeout=120, check=False)
    except Exception as e:
        print("state-update skipped: " + str(e)[:120])

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["init","load-candidates","dedup","fit","qualify","outreach","export","costs"])
    ap.add_argument("--db", default="gtm.db")
    ap.add_argument("--csv", default="")
    ap.add_argument("--config", default="")
    ap.add_argument("--out", default="outreach-ready.csv")
    ap.add_argument("--no-state", action="store_true", help="skip MASTER-STATE.md auto-update")
    a = ap.parse_args()
    if a.cmd=="init": cmd_init(a.db)
    elif a.cmd=="load-candidates": cmd_load(a.db, a.csv)
    elif a.cmd=="dedup": cmd_dedup(a.db)
    elif a.cmd=="fit": cmd_fit(a.db, a.config)
    elif a.cmd=="qualify": cmd_qualify(a.db)
    elif a.cmd=="outreach": cmd_outreach(a.db)
    elif a.cmd=="export": cmd_export(a.db, a.out)
    elif a.cmd=="costs": cmd_costs(a.db)
    if not a.no_state and a.cmd in ("init","load-candidates","dedup","fit","qualify","outreach"):
        _auto_state("engine " + a.cmd + " --db " + a.db)
