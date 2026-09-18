-- MarsLab GTM account intelligence schema (Postgres/Supabase; SQLite-compatible)
-- Provenance on every row: source, retrieved_at, verification_status. Exportable CSV/JSON/CRM.

CREATE TABLE IF NOT EXISTS accounts (
  account_id TEXT PRIMARY KEY,
  company_name TEXT NOT NULL,
  domain TEXT,
  hq_city TEXT, hq_state TEXT, regions TEXT,
  industry TEXT, sub_vertical TEXT,
  business_model TEXT,
  revenue_inr_cr TEXT, employee_band TEXT,
  field_reps_est INTEGER, dealers_est INTEGER, depots INTEGER,
  system_env TEXT,
  entity_confidence TEXT,
  fit_tier TEXT,
  fit_reasons TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signals (
  signal_id TEXT PRIMARY KEY,
  account_id TEXT REFERENCES accounts(account_id),
  signal_class TEXT,
  description TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_2_url TEXT,
  event_date DATE,
  retrieved_at TEXT DEFAULT (datetime('now')),
  strength INTEGER CHECK (strength BETWEEN 1 AND 5),
  verification_status TEXT DEFAULT 'unverified',
  verifier TEXT, verified_at TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS people (
  person_id TEXT PRIMARY KEY,
  account_id TEXT REFERENCES accounts(account_id),
  full_name TEXT NOT NULL,
  title TEXT, department TEXT,
  role_map TEXT,
  role_evidence TEXT,
  linkedin_url TEXT, source_url TEXT,
  recent_activity TEXT,
  is_current INTEGER DEFAULT 1,
  confidence TEXT,
  retrieved_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contacts (
  contact_id TEXT PRIMARY KEY,
  person_id TEXT REFERENCES people(person_id),
  channel TEXT,
  value TEXT NOT NULL,
  provider TEXT,
  verification_provider TEXT,
  verification_status TEXT DEFAULT 'unverified',
  verified_at TEXT,
  conflict_flag INTEGER DEFAULT 0,
  retrieved_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS qualification (
  account_id TEXT PRIMARY KEY REFERENCES accounts(account_id),
  force_f TEXT, force_o TEXT, force_r TEXT, force_c TEXT, force_e TEXT,
  force_evidence TEXT,
  score_icp INTEGER, score_problem INTEGER, score_trigger INTEGER, score_pain INTEGER,
  score_buyer INTEGER, score_solution INTEGER, score_pilot INTEGER, score_engage INTEGER, score_commercial INTEGER,
  total_score INTEGER,
  lifecycle TEXT,
  kill_reason TEXT,
  reviewer TEXT, reviewed_at TEXT,
  next_action TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outreach_ready (
  account_id TEXT PRIMARY KEY REFERENCES accounts(account_id),
  person_id TEXT REFERENCES people(person_id),
  observation TEXT NOT NULL,
  workflow_hypothesis TEXT NOT NULL,
  proof_point TEXT,
  opener TEXT NOT NULL,
  alt_angle TEXT,
  evidence_refs TEXT,
  approved INTEGER DEFAULT 0,
  approved_by TEXT, approved_at TEXT
);

CREATE TABLE IF NOT EXISTS experiments (
  run_id TEXT, account_id TEXT, stage TEXT,
  cost_model REAL, cost_data REAL, cost_tool REAL,
  human_minutes REAL,
  outcome TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS evidence_ledger (
  claim_id TEXT PRIMARY KEY,
  claim TEXT NOT NULL,
  label TEXT NOT NULL,
  sources TEXT,
  retrieved_at TEXT DEFAULT (datetime('now')),
  notes TEXT
);