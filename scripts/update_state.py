# Updater for MASTER-STATE.md — run after EVERY change.
# Regenerates AUTO sections (pipeline snapshot, file index, benchmark) from live
# DB + filesystem, preserving manual sections + changelog.
# Usage: python scripts/update_state.py [--note "what changed"] [--actor name]

import argparse, csv, datetime, hashlib, os, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "MASTER-STATE.md")
DB = os.path.join(ROOT, "engine", "gtm.db")
GOLD = os.path.join(ROOT, "eval", "gold-set.csv")

AUTO_START = "<!-- MASTER-AUTO-START -->"
AUTO_END = "<!-- MASTER-AUTO-END -->"
CHANGELOG_MARK = "<!-- MASTER-CHANGELOG-APPEND -->"

def db_stats():
    if not os.path.exists(DB):
        return {"db": "MISSING", "tiers": {}, "qual": [], "counts": {}}
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        tiers = {r["fit_tier"]: r["c"] for r in con.execute(
            "SELECT fit_tier, COUNT(*) c FROM accounts GROUP BY fit_tier")}
        qual = [(r["account_id"], r["total_score"], r["lifecycle"])
                for r in con.execute(
                    "SELECT account_id, total_score, lifecycle FROM qualification ORDER BY total_score DESC")]
        counts = {}
        for t in ["accounts", "signals", "people", "contacts", "qualification", "outreach_ready", "experiments", "evidence_ledger"]:
            try:
                counts[t] = con.execute("SELECT COUNT(*) c FROM " + t).fetchone()["c"]
            except Exception:
                counts[t] = "ERR"
        return {"db": DB, "tiers": tiers, "qual": qual, "counts": counts}
    finally:
        con.close()

def file_index():
    rows = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "node_modules")]
        for fn in sorted(filenames):
            if fn in ("gtm.db", "gtm.db-journal"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, ROOT).replace(os.sep, "/")
            try:
                st = os.stat(fp)
                h = hashlib.md5(open(fp, "rb").read()).hexdigest()[:8]
                rows.append((rel, st.st_size,
                             datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"), h))
            except Exception as e:
                rows.append((rel, "ERR", "", str(e)[:40]))
    return sorted(rows)

def gold_benchmark():
    if not os.path.exists(GOLD) or not os.path.exists(DB):
        return ["gold benchmark: SKIPPED (missing gold-set or db)"]
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        pred = {r["account_id"]: r["fit_tier"]
                for r in con.execute("SELECT account_id, fit_tier FROM accounts")}
    finally:
        con.close()
    out, tp, fp, fn, n = [], 0, 0, 0, 0
    with open(GOLD, encoding="utf-8") as f:
        for g in csv.DictReader(f):
            p = pred.get(g["account_id"], "MISSING")
            ok = (p == g["human_fit"])
            n += 1
            out.append(g["account_id"] + ": gold=" + g["human_fit"] + " pred=" + p + " " + ("MATCH" if ok else "MISS"))
            if g["human_fit"] == "Tier-1" and p == "Tier-1":
                tp += 1
            if g["human_fit"] != "Tier-1" and p == "Tier-1":
                fp += 1
            if g["human_fit"] == "Tier-1" and p != "Tier-1":
                fn += 1
    prec = round(tp / (tp + fp), 2) if (tp + fp) else 0
    rec = round(tp / (tp + fn), 2) if (tp + fn) else 0
    head = "gold Tier-match: %d/%d | Tier-1 precision %.2f recall %.2f (seeded smoke; live re-verify required)" % (sum(1 for l in out if "MATCH" in l), n, prec, rec)
    return [head] + out

def build_auto():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    s = db_stats()
    L = []
    L.append("## AUTO SNAPSHOT (regenerated " + now + " — do not hand-edit; run scripts/update_state.py)")
    L.append("")
    L.append("### Pipeline store: " + str(s["db"]))
    L.append("")
    L.append("Table counts: " + ", ".join(k + "=" + str(v) for k, v in s["counts"].items()))
    L.append("")
    L.append("Tier distribution: " + (", ".join(k + "=" + str(v) for k, v in sorted(s["tiers"].items())) if s["tiers"] else "none"))
    L.append("")
    L.append("Qualification (score desc):")
    L.append("")
    if s["qual"]:
        for aid, sc, lc in s["qual"]:
            L.append("- " + aid + ": " + str(sc) + " " + str(lc))
    else:
        L.append("- none")
    L.append("")
    L.append("### Gold benchmark (live recompute)")
    L.append("")
    for line in gold_benchmark():
        L.append("- " + line)
    L.append("")
    L.append("### File index (path | bytes | mtime | md5-8)")
    L.append("")
    L.append("| path | bytes | mtime | md5-8 |")
    L.append("|---|---|---|---|")
    for rel, size, mt, h in file_index():
        L.append("| " + rel + " | " + str(size) + " | " + mt + " | " + h + " |")
    return "\n".join(L) + "\n"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", default="")
    ap.add_argument("--actor", default="agent")
    a = ap.parse_args()
    if not os.path.exists(STATE):
        print("STATE MISSING: " + STATE + " — create it first.")
        sys.exit(1)
    text = open(STATE, encoding="utf-8").read()
    if AUTO_START not in text or AUTO_END not in text:
        print("AUTO markers missing in MASTER-STATE.md")
        sys.exit(1)
    auto = build_auto()
    pre, rest = text.split(AUTO_START, 1)
    _, post = rest.split(AUTO_END, 1)
    new = pre + AUTO_START + "\n" + auto + AUTO_END + post
    if a.note:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = "| " + stamp + " | " + a.actor + " | " + a.note.replace("|", "/") + " | AUTO snapshot refreshed |"
        if CHANGELOG_MARK in new:
            new = new.replace(CHANGELOG_MARK, entry + "\n" + CHANGELOG_MARK)
        else:
            new = new.rstrip() + "\n\n" + entry + "\n"
    open(STATE, "w", encoding="utf-8").write(new)
    print("MASTER-STATE.md updated" + (" — " + a.note if a.note else ""))

if __name__ == "__main__":
    main()
