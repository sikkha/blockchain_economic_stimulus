#!/usr/bin/env python3
"""
Three-agent negotiation demo (no DB, no chain writes).

Roles:
  - Payer: wants to minimize price, cares about delivery time.
  - Vendor: wants to maximize price, cares about volume & timeline.
  - Auditor: moderates, ensures terms are clear & consistent.

Flow:
  1) System seeds a brief context.
  2) N rounds of proposals / counter-proposals.
  3) Auditor synthesizes final settlement (price, quantity, delivery, notes).

If llm_handler.call_LLM is available, we’ll use it.
Otherwise we fall back to a deterministic heuristic generator.

Environment toggles (optional):
  MODEL         (default: "openai")
  ROUNDS        (default: 3)
  BASE_PRICE    (default: 100.0)    # Vendor initial ask per unit
  QUANTITY      (default: 100)      # Units
  DELIVERY_DAYS (default: 7)        # Target delivery days
"""

import os
import json
from typing import Dict, List, Any

# ---------- Optional LLM adapter ----------
def _llm_call(model: str, prompt: str) -> str:
    """
    If llm_handler.call_LLM(model, prompt) exists, use it.
    Else return a deterministic heuristic text so the flow still runs.
    """
    try:
        import llm_handler  # type: ignore
        # Many users expose call_LLM at top-level; if not, try fallback names.
        if hasattr(llm_handler, "call_LLM"):
            return llm_handler.call_LLM(model, prompt)  # type: ignore
        # Fallbacks if your wrapper uses different names:
        if hasattr(llm_handler, "call_llm"):
            return llm_handler.call_llm(model, prompt)  # type: ignore
        if hasattr(llm_handler, "generate"):
            return llm_handler.generate(model, prompt)  # type: ignore
    except Exception:
        pass
    # Heuristic fallback
    return _heuristic_response(prompt)

def _heuristic_response(prompt: str) -> str:
    """
    Very simple rule-based generator so you can test the negotiation without an LLM.
    Looks for numbers & keywords and nudges the price/delivery terms.
    """
    import re
    # Extract last mentioned price and delivery days if any
    price_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:USD|usd|\$|THB)?\b", prompt)
    days_match  = re.search(r"\b(\d+)\s*(?:day|days)\b", prompt)

    # Defaults
    price = float(price_match.group(1)) if price_match else 100.0
    days  = int(days_match.group(1)) if days_match else 7

    # Nudge depending on who is speaking (detected by a marker we add in the prompt)
    if "[PAYER]" in prompt:
        # Payer tries to push price down and keep delivery within target
        price = max(0.0, price - 2.5)
        days  = min(days, 7)
        return f"PAYER PROPOSAL -> price: {price:.2f}, delivery: {days} days, notes: keep SLA & penalties."
    if "[VENDOR]" in prompt:
        # Vendor tries to keep price up, may accept a slight delivery improvement
        price = price + 1.0
        days  = max(5, days - 1)
        return f"VENDOR COUNTER -> price: {price:.2f}, delivery: {days} days, notes: includes packaging."
    if "[AUDITOR]" in prompt:
        # Auditor synthesizes: average price, reasonable delivery
        price = round(price, 2)
        days = max(5, min(days, 10))
        return f"AUDITOR SYNTHESIS -> settle_price: {price:.2f}, delivery: {days} days, terms: net-7, defect return allowed."

    # Generic
    return "OK"

# ---------- Negotiation core ----------
class Transcript:
    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def as_text(self) -> str:
        return "\n".join(f"{m['role']}: {m['content']}" for m in self.messages)

def format_ctx(ctx: Dict[str, Any]) -> str:
    return json.dumps(ctx, indent=2, ensure_ascii=False)

def run_negotiation(
    model: str = "openai",
    rounds: int = 3,
    base_price: float = 100.0,
    quantity: int = 100,
    delivery_days: int = 7,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "transcript": [...],
        "final_settlement": {
          "unit_price": float,
          "quantity": int,
          "delivery_days": int,
          "total_value": float,
          "notes": str
        }
      }
    """
    ctx = {
        "product": "mUSD-denominated voucher",
        "quantity": quantity,
        "payer": "PayerCo",
        "vendor": "VendorLtd",
        "auditor": "AuditBot",
        "constraints": {
            "payer_budget_cap": base_price * quantity,
            "target_delivery_days": delivery_days,
            "sla": "99.5% service availability, penalty 2% per day late"
        },
        "initial_terms": {
            "vendor_ask_unit_price": base_price,
            "delivery_days": delivery_days
        }
    }

    transcript = Transcript()
    transcript.add("SYSTEM", f"Context:\n{format_ctx(ctx)}")
    current_price = base_price
    current_delivery = delivery_days

    for r in range(1, rounds + 1):
        # Payer proposes
        payer_prompt = (
            f"[PAYER] Round {r}: You represent the Payer.\n"
            f"Context: {format_ctx(ctx)}\n"
            f"Last known terms -> price: {current_price:.2f}, delivery: {current_delivery} days.\n"
            f"Respond with a concise proposal including numeric 'price' and 'delivery days'."
        )
        payer_msg = _llm_call(model, payer_prompt)
        transcript.add("PAYER", payer_msg)

        # Vendor counters (uses payer's message as the last proposal)
        vendor_prompt = (
            f"[VENDOR] Round {r}: You represent the Vendor.\n"
            f"Context: {format_ctx(ctx)}\n"
            f"Payer said: {payer_msg}\n"
            f"Respond with a concise counterproposal including numeric 'price' and 'delivery days'."
        )
        vendor_msg = _llm_call(model, vendor_prompt)
        transcript.add("VENDOR", vendor_msg)

        # Auditor moderates (sets the next “current terms” baseline)
        auditor_prompt = (
            f"[AUDITOR] Round {r}: You are the Auditor. Combine the two messages below and produce a synthesized interim term.\n"
            f"- Payer: {payer_msg}\n"
            f"- Vendor: {vendor_msg}\n"
            f"Return a compact line like: 'settle_price: <float>, delivery: <int> days, terms: ...'."
        )
        auditor_msg = _llm_call(model, auditor_prompt)
        transcript.add("AUDITOR", auditor_msg)

        # Extract numbers heuristically from auditor_msg to carry to next round
        import re
        price_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", auditor_msg)
        days_match = re.search(r"(\d+)\s*days", auditor_msg, flags=re.I)
        if price_match:
            current_price = float(price_match.group(1))
        if days_match:
            current_delivery = int(days_match.group(1))

    # Final settlement (auditor seals it)
    final_prompt = (
        f"[AUDITOR] Finalize settlement now based on last interim terms.\n"
        f"Context: {format_ctx(ctx)}\n"
        f"Last interim -> price: {current_price:.2f}, delivery: {current_delivery} days.\n"
        f"Return strict JSON with keys: unit_price, quantity, delivery_days, total_value, notes."
    )
    final_msg = _llm_call(model, final_prompt)
    # Parse strict JSON if LLM complied; otherwise fallback to heuristic
    try:
        final = json.loads(final_msg)
        # basic validation
        _ = float(final["unit_price"])
        _ = int(final["quantity"])
        _ = int(final["delivery_days"])
        _ = float(final["total_value"])
        _ = str(final.get("notes", ""))
    except Exception:
        total_value = round(current_price * quantity, 2)
        final = {
            "unit_price": round(current_price, 2),
            "quantity": quantity,
            "delivery_days": int(current_delivery),
            "total_value": total_value,
            "notes": "Heuristic settlement (no-LLM or non-JSON response)."
        }

    return {
        "transcript": transcript.messages,
        "final_settlement": final,
    }

# ---------- CLI ----------

import sqlite3
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "./data/app.db")

def save_to_db(result: dict, db_path: str = DB_PATH) -> None:
    """Store negotiation transcript and final settlement."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS negotiation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            payer TEXT,
            vendor TEXT,
            auditor TEXT,
            transcript TEXT,
            final_settlement TEXT
        )
    """)
    conn.commit()

    payer = "PayerCo"
    vendor = "VendorLtd"
    auditor = "AuditBot"
    transcript_text = json.dumps(result["transcript"], ensure_ascii=False)
    settlement_text = json.dumps(result["final_settlement"], ensure_ascii=False)

    c.execute("""
        INSERT INTO negotiation_log (payer, vendor, auditor, transcript, final_settlement)
        VALUES (?, ?, ?, ?, ?)
    """, (payer, vendor, auditor, transcript_text, settlement_text))
    conn.commit()
    conn.close()
    print(f"[DB] Negotiation logged successfully at {datetime.now()}")

def main() -> None:
    model = os.getenv("MODEL", "openai")
    rounds = int(os.getenv("ROUNDS", "3"))
    base_price = float(os.getenv("BASE_PRICE", "100"))
    quantity = int(os.getenv("QUANTITY", "100"))
    delivery_days = int(os.getenv("DELIVERY_DAYS", "7"))

    result = run_negotiation(
        model=model,
        rounds=rounds,
        base_price=base_price,
        quantity=quantity,
        delivery_days=delivery_days,
    )

    print("\n=== NEGOTIATION TRANSCRIPT ===")
    for m in result["transcript"]:
        print(f"{m['role']}: {m['content']}")

    print("\n=== FINAL SETTLEMENT ===")
    print(json.dumps(result["final_settlement"], indent=2))

    # Save to DB
    save_to_db(result)

if __name__ == "__main__":
    main()
