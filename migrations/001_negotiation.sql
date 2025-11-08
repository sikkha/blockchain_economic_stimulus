-- migrations/001_negotiation.sql

-- --- Deals (one per negotiation/settlement) ---
CREATE TABLE IF NOT EXISTS deals (
  deal_id         TEXT PRIMARY KEY,
  status          TEXT NOT NULL,      -- 'draft' | 'admitted' | 'settled' | 'aborted' | 'failed'
  mode            TEXT NOT NULL,      -- 'on_chain' | 'sim'
  buyer           TEXT NOT NULL,
  seller          TEXT NOT NULL,
  sku             TEXT,
  qty             REAL,
  unit_price      REAL,
  vat_rate        REAL,
  notional_ui     REAL,
  commitment_json TEXT,               -- final agreed JSON for settlement
  created_ts      INTEGER NOT NULL,
  finalized_ts    INTEGER
);

-- --- Negotiation turns (LLM messages, tool checks, etc.) ---
CREATE TABLE IF NOT EXISTS negotiation_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  deal_id       TEXT NOT NULL,
  turn          INTEGER NOT NULL,
  role          TEXT NOT NULL,  -- 'buyer' | 'seller' | 'judge' | 'tool'
  subtype       TEXT,           -- 'proposal' | 'counter' | 'accept' | 'check' | 'settle' | ...
  payload_json  TEXT NOT NULL,  -- raw JSON (prompt/response or structured)
  ts            INTEGER NOT NULL,
  FOREIGN KEY(deal_id) REFERENCES deals(deal_id)
);

-- --- Link transactions to a deal (additive) ---
ALTER TABLE transactions ADD COLUMN deal_id TEXT;

-- --- Helpful indexes ---
CREATE INDEX IF NOT EXISTS idx_transactions_deal ON transactions(deal_id);
CREATE INDEX IF NOT EXISTS idx_neglog_deal_turn ON negotiation_log(deal_id, turn);
CREATE INDEX IF NOT EXISTS idx_deals_created ON deals(created_ts);

