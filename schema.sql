-- Plutus SQLite schema (Phase 1). All comments in English.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 1) Email processing log: email-level dedup + incremental cursor audit.
CREATE TABLE IF NOT EXISTS emails (
  gmail_msg_id   TEXT PRIMARY KEY,        -- Gmail message id, naturally unique
  thread_id      TEXT,
  sender         TEXT,
  subject        TEXT,
  internal_date  INTEGER,                 -- epoch ms
  email_type     TEXT,                    -- credit_daily/debit_event/credit_statement/hk_statement/marketing/other
  status         TEXT,                    -- parsed/skipped/error
  error          TEXT,
  fetched_at     INTEGER,
  parsed_at      INTEGER
);

-- 2) Transactions: the core ledger.
CREATE TABLE IF NOT EXISTS transactions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint    TEXT UNIQUE,             -- sha1(card_last4|txn_time|amount|merchant_raw|action)
  source_msg_id  TEXT REFERENCES emails(gmail_msg_id),
  card_last4     TEXT,                    -- 1234 / 5678
  card_type      TEXT,                    -- debit / credit
  txn_time       TEXT,                    -- ISO8601 local time
  amount         REAL,                    -- negative = refund / reversal
  currency       TEXT,                    -- CNY / HKD
  direction      TEXT,                    -- expense/income/refund/repayment/fee
  action         TEXT,                    -- raw action word (消费/退货/扣款/入账/还款...)
  merchant_raw   TEXT,
  merchant_key   TEXT,                    -- normalized merchant used for rule lookup
  channel        TEXT,                    -- 支付宝/财付通/银联/直连
  balance        REAL,                    -- debit balance if present
  avail_credit   REAL,                    -- credit available limit if present
  points         INTEGER,
  category       TEXT REFERENCES categories(name),
  category_src   TEXT,                    -- rule/hermes/manual
  confidence     REAL,
  note           TEXT,
  status         TEXT,                    -- pending/confirmed/auto
  notify_status  TEXT,                    -- none/sent/failed (hermes WeChat push)
  notify_channel TEXT,                    -- e.g. weixin
  notified_at    INTEGER,                 -- when the push went out
  voided         INTEGER DEFAULT 0,       -- 1 = offset pair / split parent / merged-away
  offset_of      INTEGER,                 -- the txn id this one cancels (mutual)
  effective_month TEXT,                   -- override which YYYY-MM this counts in (cross-month merges)
  merged_into    INTEGER,                 -- id of the merged txn that replaced this one
  created_at     INTEGER,                 -- when this row was ingested
  updated_at     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_txn_time     ON transactions(txn_time);
CREATE INDEX IF NOT EXISTS idx_txn_status   ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_txn_card     ON transactions(card_type);
CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_txn_mkey     ON transactions(merchant_key);

-- Classification is fully AI-driven (AI suggestion + user-taught knowledge +
-- manual confirm); there are no deterministic merchant/keyword rule tables.

-- 4) Fixed category list (seeded from data/categories.json).
CREATE TABLE IF NOT EXISTS categories (
  name    TEXT PRIMARY KEY,              -- display name (Chinese)
  key     TEXT UNIQUE,                   -- stable slug
  descr   TEXT,
  active  INTEGER DEFAULT 1,
  sort    INTEGER
);

-- 5) Sync watermark for incremental fetch + resume.
CREATE TABLE IF NOT EXISTS sync_state (
  k TEXT PRIMARY KEY,
  v TEXT
);

-- 6) Free-form knowledge the user teaches us, fed to the classifier (hermes) as
--    context. scope='merchant' pins a hint to one merchant_key; scope='global'
--    is general guidance applied to every classification. This is the layer
--    that makes hermes guess like the user over time, separate from the
--    deterministic merchant_rules table.
CREATE TABLE IF NOT EXISTS knowledge (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  scope        TEXT,                  -- merchant / global
  merchant_key TEXT,                  -- nullable; set when scope='merchant'
  text         TEXT NOT NULL,
  category     TEXT REFERENCES categories(name),  -- optional implied category
  created_at   INTEGER,
  updated_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_knowledge_mkey ON knowledge(merchant_key);

-- 7) Incoming money (进项): salary / reimbursement / transfers in / 理财到账 …
--    Parsed by the AI from CMB account-alert emails and kept separate from the
--    spending ledger. Existing sms:<ROWID> source IDs remain valid historical data.
CREATE TABLE IF NOT EXISTS deposits (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source_msg_id TEXT UNIQUE,            -- Gmail message ID or legacy sms:<ROWID>
  card_last4    TEXT,
  txn_time      TEXT,                   -- ISO8601 local 'YYYY-MM-DD HH:MM'
  amount        REAL,                   -- positive
  kind          TEXT,                   -- 工资/报销/汇款/理财赎回/分红/利息/退款/其他
  payer         TEXT,                   -- payer / source
  note          TEXT,                   -- remark
  raw_text      TEXT,                   -- original notification, for audit
  created_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_deposit_time ON deposits(txn_time);
