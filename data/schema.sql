CREATE TABLE IF NOT EXISTS negotiation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    payer TEXT,
    vendor TEXT,
    auditor TEXT,
    transcript TEXT,
    final_settlement TEXT
);
