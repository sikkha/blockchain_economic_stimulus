# improvise/agent_settle.py
from __future__ import annotations
import os, time, sqlite3, json, re, sys
from typing import Dict, Any, Tuple, List
from pathlib import Path

# --- allow local imports (for negotiation_demo.py in same folder) ---
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

load_dotenv()

# =========================
# ENV / Defaults
# =========================
DB_PATH       = os.getenv("DB_PATH", "data/app.db")
RPC_URL       = os.getenv("RPC_URL", "https://rpc.testnet.arc.network")
CHAIN_ID      = int(os.getenv("CHAIN_ID", "5042002"))
TOKEN_ADDR    = Web3.to_checksum_address(os.getenv("TOKEN_ADDR", "0x70D758FdFd1Ae0d4Fb2682f50d0228Cd4B07c449"))

PAYER_PK      = os.getenv("PAYER_PK") or os.getenv("AGENT_PAYER_PRIVATE_KEY")
VENDOR_ADDR   = Web3.to_checksum_address(os.getenv("VENDOR_ADDR") or os.getenv("AGENT_VENDOR_ADDR", "0x981F76F4C4a7A6edb0A86480a5A3Cc794A69620a"))
DEPLOYER_PK   = os.getenv("DEPLOYER_PK", "0x4095553577ba83901a71661f20c666075e248ff78d0c824527de650100c74ddf")
TOKEN_DEC_FALLBACK = int(os.getenv("TOKEN_DECIMALS_DEFAULT", "6"))

# Gas safety (native ARC) — top up payer if native balance < MIN_GAS_WEI
MIN_GAS_WEI      = int(os.getenv("MIN_GAS_WEI",      str(30_000_000_000_000_000)))  # 0.03 ARC
FUND_TOPUP_WEI   = int(os.getenv("FUND_TOPUP_WEI",   str(50_000_000_000_000_000)))  # 0.05 ARC

# =========================
# Minimal ERC-20 ABI
# =========================
MIN_ABI = [
    {
        "inputs":[
            {"internalType":"address","name":"to","type":"address"},
            {"internalType":"uint256","name":"value","type":"uint256"}
        ],
        "name":"transfer","outputs":[{"internalType":"bool","name":"","type":"bool"}],
        "stateMutability":"nonpayable","type":"function"
    },
    {
        "inputs":[{"internalType":"address","name":"owner","type":"address"}],
        "name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],
        "stateMutability":"view","type":"function"
    },
    {
        "inputs":[],"name":"decimals",
        "outputs":[{"internalType":"uint8","name":"","type":"uint8"}],
        "stateMutability":"view","type":"function"
    },
    {
        "inputs":[
            {"internalType":"address","name":"to","type":"address"},
            {"internalType":"uint256","name":"value","type":"uint256"}
        ],
        "name":"mint","outputs":[],
        "stateMutability":"nonpayable","type":"function"
    },
]

# =========================
# Web3 Helpers
# =========================
def _connect_web3() -> Tuple[Web3, Any]:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        raise RuntimeError("RPC not reachable")
    token = w3.eth.contract(address=TOKEN_ADDR, abi=MIN_ABI)
    return w3, token

def _decimals(token) -> int:
    try:
        return int(token.functions.decimals().call())
    except Exception:
        return TOKEN_DEC_FALLBACK

def _fee_params(w3: Web3) -> Tuple[int, int]:
    latest = w3.eth.get_block("latest")
    base = latest.get("baseFeePerGas") or w3.eth.gas_price
    try:
        tip = getattr(w3.eth, "max_priority_fee")
    except Exception:
        tip = None
    if callable(tip):
        tip = tip()
    if tip is None:
        tip = max(1, int(base // 10_000))
    max_fee = int(base + int(tip) * 2)
    return max_fee, int(tip)

def _send_signed(w3: Web3, tx: dict, pk: str) -> Tuple[str, dict]:
    signed = w3.eth.account.sign_transaction(tx, private_key=pk)
    # web3.py v5 uses rawTransaction; v6 uses raw_transaction
    raw = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
    if raw is None:
        raise RuntimeError("SignedTransaction missing raw payload (rawTransaction/raw_transaction)")
    txh = w3.eth.send_raw_transaction(raw)
    rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=240)
    if (rcpt.get("status", 0) if isinstance(rcpt, dict) else getattr(rcpt, "status", 0)) != 1:
        raise RuntimeError(f"Tx failed: {txh.hex()}")
    return txh.hex(), rcpt

def fund_native_if_needed(
    w3: Web3,
    deployer_pk: str,
    target_addr: str,
    min_gas_wei: int,
    topup_wei: int,
    chain_id: int,
) -> None:
    """
    Ensure target has enough native ARC for gas. If not, send topup from deployer.
    No-op if deployer key not provided.
    """
    if not deployer_pk:
        return
    target_addr = Web3.to_checksum_address(target_addr)
    bal = w3.eth.get_balance(target_addr)
    if bal >= min_gas_wei:
        return

    funder = Account.from_key(deployer_pk)
    base = w3.eth.get_block("latest").get("baseFeePerGas") or w3.eth.gas_price
    try:
        tip = getattr(w3.eth, "max_priority_fee")
    except Exception:
        tip = None
    if callable(tip):
        tip = tip()
    if tip is None:
        tip = max(1, int(base // 10_000))
    max_fee = int(base + int(tip) * 2)

    tx = {
        "to": target_addr,
        "value": int(topup_wei),
        "nonce": w3.eth.get_transaction_count(funder.address),
        "chainId": chain_id,
        "type": 2,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": int(tip),
        "gas": 21_000,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key=deployer_pk)
    raw = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
    txh = w3.eth.send_raw_transaction(raw)
    w3.eth.wait_for_transaction_receipt(txh, timeout=180)

# =========================
# Database Utilities
# =========================
def _insert_tx_row(
    txid: str, ts: int, block_number: int,
    from_addr: str, to_addr: str,
    amount_raw: int, amount_ui: float,
    is_mint: int, eligible: int, notes: str,
    tier_from: int = 1, tier_to: int = 1,
) -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
          CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txid TEXT, ts INTEGER, block_number INTEGER,
            from_address TEXT, to_address TEXT,
            amount_raw INTEGER, amount_ui REAL,
            tier_from INTEGER, tier_to INTEGER,
            is_mint INTEGER, eligible INTEGER, notes TEXT
          )
        """)
        c.execute("""
          INSERT INTO transactions
            (txid, ts, block_number, from_address, to_address,
             amount_raw, amount_ui, tier_from, tier_to, is_mint, eligible, notes)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            txid, ts, block_number,
            from_addr.lower(), to_addr.lower(),
            int(amount_raw), float(amount_ui),
            int(tier_from), int(tier_to),
            int(is_mint), int(eligible), notes
        ))
        conn.commit()

def _append_negotiation_log(transcript: str, final_settlement: dict) -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
          CREATE TABLE IF NOT EXISTS negotiation_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            payer TEXT, vendor TEXT, auditor TEXT,
            transcript TEXT, final_settlement TEXT
          )
        """)
        conn.execute("""
          INSERT INTO negotiation_log (payer, vendor, auditor, transcript, final_settlement)
          VALUES (?, ?, ?, ?, ?)
        """, (
            final_settlement.get("payer", "PayerCo"),
            final_settlement.get("vendor", "VendorLtd"),
            final_settlement.get("auditor", "AuditBot"),
            transcript,
            json.dumps(final_settlement),
        ))
        conn.commit()

# =========================
# Negotiation (imports your printy demo and parses result)
# =========================
def negotiate() -> Tuple[str, dict]:
    """
    Runs improvise/negotiation_demo.py:main(), captures stdout, and extracts
    the final settlement JSON. Adds payer/vendor/auditor defaults if missing.
    """
    import io, contextlib
    from negotiation_demo import main as negotiation_main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        negotiation_main()
    out = buf.getvalue()

    # Extract JSON after "FINAL SETTLEMENT"
    tail = out.split("FINAL SETTLEMENT")[-1]
    m = re.search(r"\{.*\}", tail, re.S)
    settlement: dict = {}
    if m:
        try:
            settlement = json.loads(m.group(0))
        except json.JSONDecodeError:
            settlement = {}
    # ensure fields for logging
    settlement.setdefault("payer", "PayerCo")
    settlement.setdefault("vendor", "VendorLtd")
    settlement.setdefault("auditor", "AuditBot")
    return out, settlement

# =========================
# Token Funding (mint) if payer short of token
# =========================
def ensure_payer_funded_token(
    w3: Web3, token, payer_addr: str, need_raw: int, decimals: int
) -> List[dict]:
    steps: List[dict] = []
    # If you don't have a minter, skip
    if not DEPLOYER_PK:
        return steps

    deployer = Account.from_key(DEPLOYER_PK)
    bal = token.functions.balanceOf(payer_addr).call()
    short = max(0, need_raw - bal)
    if short == 0:
        return steps

    max_fee, tip = _fee_params(w3)
    nonce_dep = w3.eth.get_transaction_count(deployer.address)
    tx = token.functions.mint(Web3.to_checksum_address(payer_addr), int(short)).build_transaction({
        "from": deployer.address, "nonce": nonce_dep, "chainId": CHAIN_ID,
        "type": 2, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip,
        "gas": 200_000, "value": 0
    })
    txh, rcpt = _send_signed(w3, tx, DEPLOYER_PK)
    steps.append({"mint_to_payer_tx": txh})

    blk = w3.eth.get_block(rcpt.blockNumber)
    ts = int(blk.get("timestamp", time.time()))
    _insert_tx_row(
        txid=txh, ts=ts, block_number=rcpt.blockNumber,
        from_addr="0x0000000000000000000000000000000000000000",
        to_addr=payer_addr,
        amount_raw=int(short), amount_ui=float(short) / (10 ** decimals),
        is_mint=1, eligible=0, notes="mint for settlement", tier_from=0, tier_to=1
    )
    return steps

# =========================
# Settlement Flow
# =========================
def settle_on_chain(transcript: str, settlement: dict) -> Dict[str, Any]:
    if not PAYER_PK:
        raise RuntimeError("Missing PAYER_PK or AGENT_PAYER_PRIVATE_KEY")

    w3, token = _connect_web3()
    decimals = _decimals(token)

    payer = Account.from_key(PAYER_PK)
    vendor = Web3.to_checksum_address(VENDOR_ADDR)

    # total transfer amount in UI units -> raw
    total_ui = float(settlement["total_value"])
    raw = int(round(total_ui * (10 ** decimals)))

    # 0) Ensure payer has native gas (auto top-up from deployer if configured)
    fund_native_if_needed(
        w3=w3,
        deployer_pk=DEPLOYER_PK,
        target_addr=payer.address,
        min_gas_wei=MIN_GAS_WEI,
        topup_wei=FUND_TOPUP_WEI,
        chain_id=CHAIN_ID,
    )

    # 1) Ensure payer has enough token (mint from deployer if short)
    steps = ensure_payer_funded_token(w3, token, payer.address, raw, decimals)

    # 2) Send settlement transfer
    max_fee, tip = _fee_params(w3)
    nonce = w3.eth.get_transaction_count(payer.address)
    tx = token.functions.transfer(vendor, raw).build_transaction({
        "from": payer.address, "nonce": nonce, "chainId": CHAIN_ID,
        "type": 2, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip,
        "gas": 120_000, "value": 0
    })
    txh, rcpt = _send_signed(w3, tx, PAYER_PK)
    steps.append({"settle_tx": txh})

    # 3) Record normalized row
    blk = w3.eth.get_block(rcpt.blockNumber)
    ts = int(blk.get("timestamp", time.time()))
    _insert_tx_row(
        txid=txh, ts=ts, block_number=rcpt.blockNumber,
        from_addr=payer.address, to_addr=vendor,
        amount_raw=raw, amount_ui=total_ui,
        is_mint=0, eligible=1, notes="settlement: negotiation",
        tier_from=1, tier_to=1
    )

    # 4) Record negotiation log
    _append_negotiation_log(transcript, settlement)

    # 5) Balances after
    bal_p = token.functions.balanceOf(payer.address).call()
    bal_v = token.functions.balanceOf(vendor).call()

    return {
        "mode": "on_chain",
        "token": TOKEN_ADDR,
        "txid": txh,
        "block": rcpt.blockNumber,
        "payer": payer.address,
        "vendor": vendor,
        "amount_ui": total_ui,
        "amount_raw": raw,
        "steps": steps,
        "balances": {
            "payer_ui": bal_p / (10 ** decimals),
            "vendor_ui": bal_v / (10 ** decimals),
        }
    }

# =========================
# Entrypoint
# =========================
def _once_verbose() -> None:
    print(f"[settle] DB: {DB_PATH}")
    print("[settle] Negotiating…")
    transcript, settlement = negotiate()
    print("\n=== NEGOTIATION ===")
    print(json.dumps(settlement, indent=2))
    print("[settle] Settling on ArcNet…")
    res = settle_on_chain(transcript, settlement)
    print("\n=== SETTLEMENT RESULT ===")
    print(json.dumps(res, indent=2))
    print(f"[settle] Done. Tx: {res['txid']}")

def main() -> None:
    _once_verbose()

if __name__ == "__main__":
    AUTORUN  = os.getenv("AUTORUN", "0") == "1"
    INTERVAL = int(os.getenv("AUTORUN_INTERVAL_SEC", "60"))

    if not AUTORUN:
        try:
            _once_verbose()
        except Exception as e:
            print(f"[settle] ERROR: {e}")
            raise
    else:
        print(f"[settle] AUTORUN=1 (interval={INTERVAL}s). Looping…")
        while True:
            try:
                _once_verbose()
            except Exception as e:
                print("[settle] ERROR in loop:", e)
            time.sleep(INTERVAL)