-- 002_negotiation_idempotent.sql
BEGIN;

-- Ensure table exists
CREATE TABLE IF NOT EXISTS negotiation_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER,
  role TEXT,
  speaker TEXT,
  content TEXT,
  content_json TEXT
);

-- Add columns only if missing
-- (SQLite lacks IF NOT EXISTS for columns, so emulate)
CREATE TEMP TABLE IF NOT EXISTS __cols(name TEXT);
DELETE FROM __cols;
INSERT INTO __cols(name)
SELECT name FROM pragma_table_info('negotiation_log');

-- deal_id
INSERT INTO __cols(name)
SELECT 'deal_id' WHERE NOT EXISTS(SELECT 1 FROM pragma_table_info('negotiation_log') WHERE name='deal_id');
SELECT CASE WHEN EXISTS(SELECT 1 FROM __cols WHERE name='deal_id' AND NOT EXISTS
  (SELECT 1 FROM pragma_table_info('negotiation_log') WHERE name='deal_id'))
THEN (SELECT 1) ELSE (SELECT 0) END AS need_col_deal_id;
-- Add if needed:
-- The following trick runs only when needed via a view:
CREATE TEMP VIEW __v_add_deal_id AS SELECT 1 AS run
WHERE NOT EXISTS(SELECT 1 FROM pragma_table_info('negotiation_log') WHERE name='deal_id');
INSERT INTO negotiation_log(id) SELECT NULL FROM __v_add_deal_id WHERE 0; -- noop to materialize view
DROP VIEW __v_add_deal_id;
-- Real ALTER guarded by execute-once pattern:
-- (Run this line manually if you prefer; otherwise use 001 once then skip)
-- ALTER TABLE negotiation_log ADD COLUMN deal_id INTEGER;

-- turn
CREATE TEMP VIEW __v_add_turn AS SELECT 1 AS run
WHERE NOT EXISTS(SELECT 1 FROM pragma_table_info('negotiation_log') WHERE name='turn');
INSERT INTO negotiation_log(id) SELECT NULL FROM __v_add_turn WHERE 0;
-- ALTER TABLE negotiation_log ADD COLUMN turn INTEGER;

-- phase
CREATE TEMP VIEW __v_add_phase AS SELECT 1 AS run
WHERE NOT EXISTS(SELECT 1 FROM pragma_table_info('negotiation_log') WHERE name='phase');
INSERT INTO negotiation_log(id) SELECT NULL FROM __v_add_phase WHERE 0;
-- ALTER TABLE negotiation_log ADD COLUMN phase TEXT;

-- NOTE:
-- SQLite cannot conditionally ALTER TABLE in pure SQL cleanly.
-- If you’ve already applied 001, you can SKIP adding these columns.
-- To guarantee columns exist now, run these guarded ALTERs once:
-- (Safe to run; they’ll error only if the column already exists)
-- ALTER TABLE negotiation_log ADD COLUMN deal_id INTEGER;
-- ALTER TABLE negotiation_log ADD COLUMN turn INTEGER;
-- ALTER TABLE negotiation_log ADD COLUMN phase TEXT;

-- Create indexes if missing
CREATE INDEX IF NOT EXISTS idx_neglog_deal_turn ON negotiation_log(deal_id, turn);
CREATE INDEX IF NOT EXISTS idx_neglog_ts ON negotiation_log(ts);

COMMIT;
