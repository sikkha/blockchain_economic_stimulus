# backend/monitoring/router.py
from __future__ import annotations
import os, sqlite3, json, time
from typing import List, Dict, Any, Optional
from fastapi import APIRouter

router = APIRouter()

DB_PATH = os.getenv("DB_PATH", "/data/app.db")

def _rows(q: str, params: tuple = ()) -> List[sqlite3.Row]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(q, params)
        return cur.fetchall()
    finally:
        conn.close()

@router.get("/metrics")
def metrics() -> Dict[str, Any]:
    # basic counts + latest balances (derived by summing transfers by address)
    tx_count = _rows("SELECT COUNT(*) c FROM transactions")[0]["c"] if _rows("SELECT COUNT(*) c FROM transactions") else 0
    deal_count = _rows("SELECT COUNT(*) c FROM negotiation_log")[0]["c"] if _rows("SELECT COUNT(*) c FROM negotiation_log") else 0

    latest = _rows("""
      SELECT txid, ts, block_number, from_address, to_address, amount_ui, is_mint, eligible
      FROM transactions ORDER BY id DESC LIMIT 5
    """)
    latest_list = [dict(r) for r in latest]

    return {
        "ok": True,
        "tx_count": tx_count,
        "deal_count": deal_count,
        "latest": latest_list,
        "ts": int(time.time()),
    }

@router.get("/deals")
def deals(limit: int = 50) -> Dict[str, Any]:
    logs = _rows("""
      SELECT id, created_at, payer, vendor, auditor, transcript, final_settlement
      FROM negotiation_log
      ORDER BY id DESC
      LIMIT ?
    """, (limit,))
    out = []
    for r in logs:
        try:
            settlement = json.loads(r["final_settlement"] or "{}")
        except Exception:
            settlement = {}
        out.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "payer": r["payer"],
            "vendor": r["vendor"],
            "auditor": r["auditor"],
            "settlement": settlement,
            "transcript": r["transcript"],
        })
    return {"ok": True, "deals": out}

@router.get("/stream")
def stream_placeholder() -> Dict[str, Any]:
    # simple placeholder so the frontend stops erroring
    return {"ok": True, "note": "SSE not implemented; polling /metrics and /deals instead."}