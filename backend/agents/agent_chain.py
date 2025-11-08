# backend/agents/agent_chain.py
"""
Drop-in replacement focused on:
  1) **Deals not written**: normalize settlement payload (quantity/unit_price → qty/price;
     payer/vendor → buyer/seller) and perform explicit INSERT/UPDATE with commits.
  2) **Negotiation log schema mismatch**: write to live schema
     (payer, vendor, auditor, transcript, final_settlement, created_at DEFAULT).

Assumptions (from live DB):
  - deals(
      deal_id TEXT PRIMARY KEY,
      status TEXT NOT NULL,
      mode TEXT NOT NULL,
      buyer TEXT NOT NULL,
      seller TEXT NOT NULL,
      sku TEXT,
      qty REAL,
      unit_price REAL,
      vat_rate REAL,
      notional_ui REAL,
      commitment_json TEXT,
      created_ts INTEGER NOT NULL,
      finalized_ts INTEGER
    )
  - negotiation_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      payer TEXT,
      vendor TEXT,
      auditor TEXT,
      transcript TEXT,
      final_settlement TEXT
    )
  - transactions(..., deal_id TEXT) exists.

This module avoids dynamic column lists and ensures commits.
"""
from __future__ import annotations
import os, time, uuid, json, sqlite3
from typing import Dict, Any, Tuple, Optional

from web3 import Web3
from eth_account import Account

# ---------- Optional imports from runner.py ----------
try:
    from .runner import (
        MIN_ABI, _fee_params, _send_signed,
        TOKEN_DECIMALS_DEFAULT as DEC_FALLBACK,
        GAS_LIMIT_NATIVE_XFER, GAS_LIMIT_ERC20_TRANSFER,
    )
except Exception:
    MIN_ABI = [
        {
            "inputs": [
                {"internalType": "address", "name": "to", "type": "address"},
                {"internalType": "uint256", "name": "value", "type": "uint256"},
            ],
            "name": "transfer",
            "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
            "stateMutability": "nonpayable",
            "type": "function",
        },
        {
            "inputs": [{"internalType": "address", "name": "owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "decimals",
            "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [
                {"internalType": "address", "name": "to", "type": "address"},
                {"internalType": "uint256", "name": "value", "type": "uint256"},
            ],
            "name": "mint",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        },
    ]

    def _fee_params(w3: Web3) -> Tuple[int, int]:
        latest = w3.eth.get_block("latest")
        base = latest.get("baseFeePerGas") or w3.eth.gas_price
        tip = getattr(w3.eth, "max_priority_fee", w3.eth.gas_price // 10_000 or 1)
        return int(base + tip * 2), int(tip)

    def _send_signed(w3: Web3, tx: dict, pk: str):
        signed = w3.eth.account.sign_transaction(tx, private_key=pk)
        txh = w3.eth.send_raw_transaction(signed.raw_transaction)
        rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=240)
        if rcpt.status != 1:
            raise RuntimeError(f"Tx failed: {txh.hex()}")
        return txh.hex(), rcpt

    DEC_FALLBACK = 6
    GAS_LIMIT_NATIVE_XFER = 21_000
    GAS_LIMIT_ERC20_TRANSFER = 120_000

# ---------- LLM handler (optional) ----------
try:
    from ..llm_handler import call_LLM as CALL_LLM
except Exception:
    CALL_LLM = None

# ---------- Environment ----------
DB_PATH = os.getenv("DB_PATH", "/data/app.db")  # default to live path you used
RPC_URL = os.getenv("RPC_URL", "https://rpc.testnet.arc.network")
CHAIN_ID = int(os.getenv("CHAIN_ID", "5042002"))
TOKEN_ADDR = Web3.to_checksum_address(os.getenv("TOKEN_ADDR", "0x70D758FdFd1Ae0d4Fb2682f50d0228Cd4B07c449"))

DEPLOYER_PK = os.getenv(
    "DEPLOYER_PK",
    "0x4095553577ba83901a71661f20c666075e248ff78d0c824527de650100c74ddf",
)
A_PK = os.getenv(
    "A_PK",
    "0x1e110965879762703e739bcf3b7ed8779b083c7c56bd59857a2c74a5156a3753",
)
B_PK = os.getenv(
    "B_PK",
    "0xc37b92ea40f7183232431bf462fb9b127e87dd9e0d938dc8f95878932401de46",
)
C_ADDR = Web3.to_checksum_address(os.getenv("C_ADDR", "0x884c9339e1765511b02f6C8e3D7d365d396D14C1"))

NEG_SIM = int(os.getenv("NEGOTIATION_SIMULATE", "0"))  # 1=skip LLM, produce deterministic script

# ---------- DB helpers ----------

def _db_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _norm_settlement(s: Optional[dict]) -> dict:
    """Normalize incoming settlement JSON to the columns our INSERT expects."""
    s = dict(s or {})
    # quantity/unit_price → qty/price
    s["qty"] = s.get("qty") or s.get("quantity")
    s["price"] = s.get("price") or s.get("unit_price")
    # buyer/seller from payer/vendor if not present
    s["buyer"] = s.get("buyer") or s.get("payer")
    s["seller"] = s.get("seller") or s.get("vendor")
    # default mode
    s["mode"] = s.get("mode") or "sim"
    # compute notional_ui if missing
    if s.get("notional_ui") is None and s.get("qty") is not None and s.get("price") is not None:
        try:
            s["notional_ui"] = float(s["qty"]) * float(s["price"])
        except Exception:
            pass
    return s


def _insert_deal_initial(deal_id: str, normalized: dict, created_ts: int) -> None:
    with _db_conn() as conn:
        conn.execute(
            """
            INSERT INTO deals (
                deal_id, status, mode, buyer, seller, sku, qty, unit_price,
                vat_rate, notional_ui, commitment_json, created_ts, finalized_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                deal_id,
                normalized.get("status") or "draft",
                normalized.get("mode") or "sim",
                (normalized.get("buyer") or "").lower(),
                (normalized.get("seller") or "").lower(),
                normalized.get("sku") or "SKU-DEMO",
                normalized.get("qty") or 0,
                normalized.get("price") or 0,
                normalized.get("vat_rate") or 0,
                normalized.get("notional_ui"),
                json.dumps(normalized, ensure_ascii=False) if normalized else None,
                created_ts,
                None,
            ),
        )
        conn.commit()


def _finalize_deal(deal_id: str, commitment: dict) -> None:
    with _db_conn() as conn:
        conn.execute(
            """
            UPDATE deals
               SET status = ?, mode = ?, commitment_json = ?, finalized_ts = ?
             WHERE deal_id = ?
            """,
            (
                "settled",
                "on_chain",
                json.dumps(commitment, ensure_ascii=False),
                int(time.time()),
                deal_id,
            ),
        )
        conn.commit()


def _neg_log_add(payer: str, vendor: str, auditor: str, transcript: str, settlement: Optional[dict]) -> None:
    with _db_conn() as conn:
        conn.execute(
            """
            INSERT INTO negotiation_log (payer, vendor, auditor, transcript, final_settlement)
            VALUES (?,?,?,?,?)
            """,
            (
                payer,
                vendor,
                auditor,
                transcript,
                json.dumps(settlement, ensure_ascii=False) if settlement is not None else None,
            ),
        )
        conn.commit()


def _insert_tx_row_with_deal(
    txid: str,
    ts: int,
    block: int,
    from_addr: str,
    to_addr: str,
    amount_raw: int,
    amount_ui: float,
    is_mint: int,
    eligible: int,
    notes: str,
    deal_id: str,
    tier_from: int = 1,
    tier_to: int = 1,
) -> None:
    with _db_conn() as conn:
        conn.execute(
            """
            INSERT INTO transactions
            (txid, ts, block_number, from_address, to_address, amount_raw, amount_ui,
             tier_from, tier_to, is_mint, eligible, notes, deal_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                txid,
                ts,
                block,
                from_addr.lower(),
                to_addr.lower(),
                int(amount_raw),
                float(amount_ui),
                int(tier_from),
                int(tier_to),
                int(is_mint),
                int(eligible),
                notes,
                deal_id,
            ),
        )
        conn.commit()


# ---------- LLM helper ----------

def _llm_or_sim(model: str, prompt: str, role: str) -> dict:
    if NEG_SIM or CALL_LLM is None:
        return {"role": role, "model": "sim", "content": f"[simulated] {prompt[:120]} ..."}
    try:
        out = CALL_LLM(model, prompt)
        return {"role": role, "model": model, "content": out}
    except Exception as e:
        return {"role": role, "model": model, "error": str(e), "content": ""}


# ---------- Main entry ----------

def run_negotiated_three_wallet_deal() -> Dict[str, Any]:
    """
    Creates a deal, records a simple negotiation (aligned to live negotiation_log schema),
    executes on-chain settlement, writes transactions tied to the deal_id, and finalizes the deal.
    """
    deal_id = str(uuid.uuid4())
    created_ts = int(time.time())

    buyer_addr = Account.from_key(A_PK).address
    seller_addr = Account.from_key(B_PK).address

    # Seed settlement proposal (can be replaced by LLM output later)
    proposed = {
        "quantity": 2,
        "unit_price": 100.0,
        "payer": buyer_addr,
        "vendor": seller_addr,
        "mode": "on_chain",
        "sku": "SKU-DEMO",
        "vat_rate": 0.0,
    }
    normalized = _norm_settlement(proposed)
    if normalized.get("notional_ui") is None:
        try:
            normalized["notional_ui"] = float(normalized.get("qty") or 0) * float(normalized.get("price") or 0)
        except Exception:
            normalized["notional_ui"] = None

    # Initial DEAL row (status=draft)
    _insert_deal_initial(deal_id, normalized, created_ts)

    # Negotiation log (schema-aligned)
    _neg_log_add(payer=buyer_addr, vendor=seller_addr, auditor="AuditorBot", transcript="Buyer proposes.", settlement=None)
    _neg_log_add(payer=buyer_addr, vendor=seller_addr, auditor="AuditorBot", transcript="Seller accepts.", settlement=None)

    # A judge/arbiter recommends exact legs to execute
    commitment = {
        "version": 1,
        "deal_id": deal_id,
        "legs": [
            {"from": buyer_addr, "to": seller_addr, "ui": 100.0, "eligible": 1, "note": "A->B"},
            {"from": seller_addr, "to": C_ADDR, "ui": 100.0, "eligible": 0, "note": "B->C"},
        ],
    }
    _neg_log_add(
        payer=buyer_addr,
        vendor=seller_addr,
        auditor="AuditorBot",
        transcript="Judge recommends settlement legs.",
        settlement=commitment,
    )

    # ---------- On-chain settlement ----------
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    assert w3.is_connected(), "RPC not reachable"
    token = w3.eth.contract(address=TOKEN_ADDR, abi=MIN_ABI)
    try:
        dec = token.functions.decimals().call()
    except Exception:
        dec = DEC_FALLBACK
    raw = lambda ui: int(ui * (10 ** dec))

    deployer = Account.from_key(DEPLOYER_PK)
    A = Account.from_key(A_PK)
    B = Account.from_key(B_PK)

    max_fee, tip = _fee_params(w3)
    GAS_MINT = 200_000

    # Mint 1000 to A
    nonce_dep = w3.eth.get_transaction_count(deployer.address)
    mint_raw = raw(1000.0)
    tx_mint = token.functions.mint(A.address, mint_raw).build_transaction(
        {
            "from": deployer.address,
            "nonce": nonce_dep,
            "chainId": CHAIN_ID,
            "type": 2,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": tip,
            "gas": GAS_MINT,
        }
    )
    mint_txh, mint_rcpt = _send_signed(w3, tx_mint, DEPLOYER_PK)
    blk = w3.eth.get_block(mint_rcpt.blockNumber)
    ts = int(blk.get("timestamp", time.time()))
    _insert_tx_row_with_deal(
        txid=mint_txh,
        ts=ts,
        block=mint_rcpt.blockNumber,
        from_addr="0x0000000000000000000000000000000000000000",
        to_addr=A.address,
        amount_raw=mint_raw,
        amount_ui=1000.0,
        is_mint=1,
        eligible=0,
        notes="mint",
        deal_id=deal_id,
        tier_from=0,
        tier_to=1,
    )

    # Fund A & B native for gas
    nonce_dep += 1
    for target in [A.address, B.address]:
        tx = {
            "to": target,
            "value": 5 * 10**16,
            "nonce": nonce_dep,
            "chainId": CHAIN_ID,
            "type": 2,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": tip,
            "gas": GAS_LIMIT_NATIVE_XFER,
        }
        _send_signed(w3, tx, DEPLOYER_PK)
        nonce_dep += 1

    # A -> B (100)
    nonceA = w3.eth.get_transaction_count(A.address)
    a2b_raw = raw(100.0)
    tx1 = token.functions.transfer(B.address, a2b_raw).build_transaction(
        {
            "from": A.address,
            "nonce": nonceA,
            "chainId": CHAIN_ID,
            "type": 2,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": tip,
            "gas": GAS_LIMIT_ERC20_TRANSFER,
        }
    )
    a2b_txh, a2b_rcpt = _send_signed(w3, tx1, A.key)
    blk = w3.eth.get_block(a2b_rcpt.blockNumber)
    ts = int(blk.get("timestamp", time.time()))
    _insert_tx_row_with_deal(
        txid=a2b_txh,
        ts=ts,
        block=a2b_rcpt.blockNumber,
        from_addr=A.address,
        to_addr=B.address,
        amount_raw=a2b_raw,
        amount_ui=100.0,
        is_mint=0,
        eligible=1,
        notes="A->B",
        deal_id=deal_id,
        tier_from=1,
        tier_to=1,
    )

    # B -> C (100)
    nonceB = w3.eth.get_transaction_count(B.address)
    b2c_raw = raw(100.0)
    tx2 = token.functions.transfer(C_ADDR, b2c_raw).build_transaction(
        {
            "from": B.address,
            "nonce": nonceB,
            "chainId": CHAIN_ID,
            "type": 2,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": tip,
            "gas": GAS_LIMIT_ERC20_TRANSFER,
        }
    )
    b2c_txh, b2c_rcpt = _send_signed(w3, tx2, B.key)
    blk = w3.eth.get_block(b2c_rcpt.blockNumber)
    ts = int(blk.get("timestamp", time.time()))
    _insert_tx_row_with_deal(
        txid=b2c_txh,
        ts=ts,
        block=b2c_rcpt.blockNumber,
        from_addr=B.address,
        to_addr=C_ADDR,
        amount_raw=b2c_raw,
        amount_ui=100.0,
        is_mint=0,
        eligible=0,
        notes="B->C",
        deal_id=deal_id,
        tier_from=1,
        tier_to=1,
    )

    # Finalize deal
    _finalize_deal(deal_id, commitment)

    return {
        "deal_id": deal_id,
        "mode": "on_chain",
        "tx_count": 2,
        "transferred_ui": 200.0,
    }
