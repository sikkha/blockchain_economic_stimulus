# backend/monitoring/deals_router.py
import sqlite3
from typing import List, Literal, Optional
from fastapi import APIRouter, Query

DB_PATH = "/data/app.db"
router = APIRouter(prefix="/api", tags=["deals"])

def _rows(sql: str, args=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

@router.get("/deals")
def list_deals(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Comma list, e.g. 'admitted,settled'"),
    order: Literal["asc","desc"] = "desc",
):
    status_list = None
    params = []
    where = []
    if status:
        status_list = [s.strip() for s in status.split(",") if s.strip()]
        if status_list:
            where.append("(" + " OR ".join(["status = ?"] * len(status_list)) + ")")
            params.extend(status_list)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
      SELECT deal_id, status, mode, buyer, seller, sku, qty, unit_price, vat_rate,
             notional_ui, commitment_json, created_ts, finalized_ts
      FROM deals
      {where_sql}
      ORDER BY created_ts {order}
      LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = _rows(sql, params)

    # Convert created_ts to ISO for the UI
    for r in rows:
        ts = r.get("created_ts")
        if ts:
            try:
                import datetime as dt
                r["created_iso"] = dt.datetime.utcfromtimestamp(int(ts)).isoformat() + "Z"
            except Exception:
                r["created_iso"] = None
        else:
            r["created_iso"] = None
    return {"items": rows, "limit": limit, "offset": offset, "count": len(rows)}
