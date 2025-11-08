#!/usr/bin/env python3
"""
ARC Hackathon quick-fix agent script (container-friendly)

- Tries REAL on-chain sequence on ARC testnet:
  mint 1000 mUSD to A  -> fund A,B native  -> transfer 100 A->B -> transfer 100 B->C
- Writes normalized rows into SQLite so the React monitor shows them immediately.
- Falls back to simulated inserts unless FORCE_ONCHAIN=1.

Env (override defaults if you want):
  RPC_URL, CHAIN_ID, TOKEN_ADDR
  DEPLOYER_PK, A_ADDR, A_PK, B_ADDR, B_PK, C_ADDR
  DB_PATH (default: /data/app.db in container)
  FORCE_ONCHAIN=1  (disable simulation fallback)

Usage:
  python improvise/agent_chain.py
"""

from __future__ import annotations
import os, time, sqlite3, sys
from pathlib import Path
from typing import Dict, Any, Tuple

from dotenv import load_dotenv
load_dotenv()

from web3 import Web3
from eth_account import Account

# ---------- Config (defaults; override via .env or container env) ----------
RPC_URL   = os.getenv("RPC_URL", "https://rpc.testnet.arc.network")
CHAIN_ID  = int(os.getenv("CHAIN_ID", "5042002"))
TOKEN_ADDR = os.getenv("TOKEN_ADDR", "0x70D758FdFd1Ae0d4Fb2682f50d0228Cd4B07c449")

# owner can mint + fund gas
DEPLOYER_PK = os.getenv("DEPLOYER_PK", "0x4095553577ba83901a71661f20c666075e248ff78d0c824527de650100c74ddf")

# three wallets from your notebook demo
A_ADDR = os.getenv("A_ADDR", "0x00Bfab80Cce644e99D8c358B8Bf9670b61160396")
A_PK   = os.getenv("A_PK",   "0x1e110965879762703e739bcf3b7ed8779b083c7c56bd59857a2c74a5156a3753")

B_ADDR = os.getenv("B_ADDR", "0x981F76F4C4a7A6edb0A86480a5A3Cc794A69620a")
B_PK   = os.getenv("B_PK",   "0xc37b92ea40f7183232431bf462fb9b127e87dd9e0d938dc8f95878932401de46")

C_ADDR = os.getenv("C_ADDR", "0x884c9339e1765511b02f6C8e3D7d365d396D14C1")

FORCE_ONCHAIN = os.getenv("FORCE_ONCHAIN", "0") == "1"

# Container default DB is /data/app.db (volume mount)
DB_PATH = Path(os.getenv("DB_PATH", "/data/app.db")).resolve()

# human amounts
MINT_TO_A_UI        = float(os.getenv("MINT_TO_A_UI", "1000"))
TRANSFER_A_TO_B_UI  = float(os.getenv("TRANSFER_A_TO_B_UI", "100"))
TRANSFER_B_TO_C_UI  = float(os.getenv("TRANSFER_B_TO_C_UI", "100"))

# gas params
FUND_PER_SENDER_WEI = int(os.getenv("FUND_PER_SENDER_WEI", str(5 * 10**16)))  # 0.05
GAS_LIMIT_ERC20_TRANSFER = int(os.getenv("GAS_LIMIT_ERC20_TRANSFER", "120000"))
GAS_LIMIT_NATIVE_XFER    = 21000

TOKEN_DECIMALS_DEFAULT = 6

# ---------- Minimal ABI ----------
MIN_ABI = [
    {
        "inputs": [{"internalType":"address","name":"to","type":"address"},
                   {"internalType":"uint256","name":"value","type":"uint256"}],
        "name":"transfer","outputs":[{"internalType":"bool","name":"","type":"bool"}],
        "stateMutability":"nonpayable","type":"function",
    },
    {
        "inputs":[{"internalType":"address","name":"owner","type":"address"}],
        "name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],
        "stateMutability":"view","type":"function",
    },
    {
        "inputs": [], "name":"decimals",
        "outputs":[{"internalType":"uint8","name":"","type":"uint8"}],
        "stateMutability":"view","type":"function",
    },
    {   # MockUSD has mint(owner)
        "inputs":[{"internalType":"address","name":"to","type":"address"},
                  {"internalType":"uint256","name":"value","type":"uint256"}],
        "name":"mint","outputs":[], "stateMutability":"nonpayable","type":"function",
    },
]

# ---------- SQLite helpers ----------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  txid TEXT,
  ts INTEGER,
  block_number INTEGER,
  from_address TEXT,
  to_address TEXT,
  amount_raw INTEGER,
  amount_ui REAL,
  tier_from INTEGER,
  tier_to INTEGER,
  is_mint INTEGER,
  eligible INTEGER,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS agents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  wallet TEXT UNIQUE NOT NULL,
  type TEXT,
  province TEXT,
  tier INTEGER,
  meta_json TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  m1_obs REAL,
  leakage REAL,
  vat_est REAL,
  smes_active INTEGER,
  last_block INTEGER
);
"""

def ensure_schema(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_SQL)
        cur = conn.execute("SELECT 1 FROM metrics WHERE id=1")
        if cur.fetchone() is None:
            conn.execute("INSERT INTO metrics (id, m1_obs, leakage, vat_est, smes_active, last_block) VALUES (1,0,0,0,0,0)")
        conn.commit()

def seed_agents(payer: str, vendor: str) -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO agents (wallet, type, province, tier, meta_json) VALUES (?,?,?,?,?)",
            (payer.lower(), "payer", "", 1, "{}"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO agents (wallet, type, province, tier, meta_json) VALUES (?,?,?,?,?)",
            (vendor.lower(), "vendor", "", 1, "{}"),
        )
        conn.commit()

def insert_row(*, txid: str, ts: int, block_number: int,
               from_addr: str, to_addr: str,
               amount_raw: int, amount_ui: float,
               is_mint: int, eligible: int,
               notes: str, tier_from: int = 1, tier_to: int = 1) -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """INSERT INTO transactions
               (txid, ts, block_number, from_address, to_address,
                amount_raw, amount_ui, tier_from, tier_to, is_mint, eligible, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (txid, ts, block_number, from_addr.lower(), to_addr.lower(),
             int(amount_raw), float(amount_ui), int(tier_from), int(tier_to),
             int(is_mint), int(eligible), notes),
        )
        conn.commit()

# ---------- Chain helpers ----------
def fee_params(w3: Web3) -> Tuple[int,int]:
    latest = w3.eth.get_block("latest")
    base = latest.get("baseFeePerGas") or w3.eth.gas_price
    try:
        tip = w3.eth.max_priority_fee
    except Exception:
        tip = max(1, w3.eth.gas_price // 10_000)
    return int(base + tip * 2), int(tip)

def send_tx(w3: Web3, tx: dict, pk: str):
    signed = w3.eth.account.sign_transaction(tx, private_key=pk)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=240)
    if rcpt.status != 1:
        raise RuntimeError(f"Tx failed: {txh.hex()}")
    return txh.hex(), rcpt

# ---------- Flow ----------
def try_onchain() -> Dict[str, Any]:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    assert w3.is_connected(), "RPC not reachable"
    assert w3.eth.chain_id == CHAIN_ID, f"Wrong chain ({w3.eth.chain_id})"

    token = w3.eth.contract(address=Web3.to_checksum_address(TOKEN_ADDR), abi=MIN_ABI)
    try:
        decimals = token.functions.decimals().call()
    except Exception:
        decimals = TOKEN_DECIMALS_DEFAULT

    deployer = Account.from_key(DEPLOYER_PK)
    A = Account.from_key(A_PK)
    B = Account.from_key(B_PK)
    C = Web3.to_checksum_address(C_ADDR)

    max_fee, tip = fee_params(w3)

    out: Dict[str, Any] = {"steps": [], "mode":"on_chain", "errors":[]}

    # mint to A
    nonce_dep = w3.eth.get_transaction_count(deployer.address)
    mint_raw = int(MINT_TO_A_UI * (10 ** decimals))
    tx_mint = token.functions.mint(Web3.to_checksum_address(A.address), mint_raw).build_transaction({
        "from": deployer.address, "nonce": nonce_dep, "chainId": CHAIN_ID,
        "type": 2, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip,
        "gas": 200_000, "value": 0,
    })
    mint_txh, mint_rcpt = send_tx(w3, tx_mint, DEPLOYER_PK)
    out["steps"].append({"mint_to_A_tx": mint_txh})
    blk = w3.eth.get_block(mint_rcpt.blockNumber)
    insert_row(
        txid=mint_txh, ts=int(blk["timestamp"]), block_number=mint_rcpt.blockNumber,
        from_addr="0x0000000000000000000000000000000000000000", to_addr=A.address,
        amount_raw=mint_raw, amount_ui=MINT_TO_A_UI,
        is_mint=1, eligible=0, notes="mint", tier_from=0, tier_to=1
    )

    # fund A & B native
    nonce_dep += 1
    for target in (A.address, B.address):
        tx = {
            "to": Web3.to_checksum_address(target), "value": FUND_PER_SENDER_WEI,
            "nonce": nonce_dep, "chainId": CHAIN_ID, "type": 2,
            "maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip, "gas": GAS_LIMIT_NATIVE_XFER,
        }
        fund_txh, _ = send_tx(w3, tx, DEPLOYER_PK)
        out["steps"].append({"fund_native": {"target": target, "tx": fund_txh}})
        nonce_dep += 1

    # A -> B
    a2b_raw = int(TRANSFER_A_TO_B_UI * (10 ** decimals))
    nonceA = w3.eth.get_transaction_count(A.address)
    tx1 = token.functions.transfer(Web3.to_checksum_address(B.address), a2b_raw).build_transaction({
        "from": A.address, "nonce": nonceA, "chainId": CHAIN_ID, "type": 2,
        "maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip, "gas": GAS_LIMIT_ERC20_TRANSFER,
    })
    a2b_txh, a2b_rcpt = send_tx(w3, tx1, A.key.hex())
    out["steps"].append({"A_to_B_tx": a2b_txh})
    blk = w3.eth.get_block(a2b_rcpt.blockNumber)
    insert_row(
        txid=a2b_txh, ts=int(blk["timestamp"]), block_number=a2b_rcpt.blockNumber,
        from_addr=A.address, to_addr=B.address,
        amount_raw=a2b_raw, amount_ui=TRANSFER_A_TO_B_UI,
        is_mint=0, eligible=1, notes="A->B", tier_from=1, tier_to=1
    )

    # B -> C
    b2c_raw = int(TRANSFER_B_TO_C_UI * (10 ** decimals))
    nonceB = w3.eth.get_transaction_count(B.address)
    tx2 = token.functions.transfer(C, b2c_raw).build_transaction({
        "from": B.address, "nonce": nonceB, "chainId": CHAIN_ID, "type": 2,
        "maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip, "gas": GAS_LIMIT_ERC20_TRANSFER,
    })
    b2c_txh, b2c_rcpt = send_tx(w3, tx2, B.key.hex())
    out["steps"].append({"B_to_C_tx": b2c_txh})
    blk = w3.eth.get_block(b2c_rcpt.blockNumber)
    insert_row(
        txid=b2c_txh, ts=int(blk["timestamp"]), block_number=b2c_rcpt.blockNumber,
        from_addr=B.address, to_addr=C,
        amount_raw=b2c_raw, amount_ui=TRANSFER_B_TO_C_UI,
        is_mint=0, eligible=0, notes="B->C", tier_from=1, tier_to=1
    )

    out["tx_count"] = 2
    out["transferred_ui"] = TRANSFER_A_TO_B_UI + TRANSFER_B_TO_C_UI
    return out

def simulate_into_db(decimals: int = TOKEN_DECIMALS_DEFAULT) -> Dict[str, Any]:
    now = int(time.time())
    mint_raw = int(MINT_TO_A_UI * (10 ** decimals))
    a2b_raw = int(TRANSFER_A_TO_B_UI * (10 ** decimals))
    b2c_raw = int(TRANSFER_B_TO_C_UI * (10 ** decimals))

    sim_mint = "0x" + "aa"*32
    sim_a2b  = "0x" + "bb"*32
    sim_b2c  = "0x" + "cc"*32
    blk      = 0

    insert_row(
        txid=sim_mint, ts=now, block_number=blk,
        from_addr="0x0000000000000000000000000000000000000000",
        to_addr=A_ADDR, amount_raw=mint_raw, amount_ui=MINT_TO_A_UI,
        is_mint=1, eligible=0, notes="mint (simulated)", tier_from=0, tier_to=1
    )
    insert_row(
        txid=sim_a2b, ts=now+1, block_number=blk,
        from_addr=A_ADDR, to_addr=B_ADDR,
        amount_raw=a2b_raw, amount_ui=TRANSFER_A_TO_B_UI,
        is_mint=0, eligible=1, notes="A->B (simulated)", tier_from=1, tier_to=1
    )
    insert_row(
        txid=sim_b2c, ts=now+2, block_number=blk,
        from_addr=B_ADDR, to_addr=C_ADDR,
        amount_raw=b2c_raw, amount_ui=TRANSFER_B_TO_C_UI,
        is_mint=0, eligible=0, notes="B->C (simulated)", tier_from=1, tier_to=1
    )

    return {"mode":"simulated","tx_count":2,"transferred_ui":TRANSFER_A_TO_B_UI+TRANSFER_B_TO_C_UI}

def main() -> None:
    print(f"[agent] DB: {DB_PATH}")
    ensure_schema(DB_PATH)
    seed_agents(A_ADDR, B_ADDR)
    try:
        print("[agent] Attempting on-chain flow…")
        out = try_onchain()
        print("[agent] On-chain complete. tx_count:", out.get("tx_count"))
    except Exception as e:
        print(f"[agent] On-chain failed: {e}")
        if FORCE_ONCHAIN:
            print("[agent] FORCE_ONCHAIN=1 — aborting (no simulated rows).")
            sys.exit(2)
        print("[agent] Writing simulated rows to DB instead…")
        out = simulate_into_db()
        print("[agent] Simulated rows inserted. tx_count:", out.get("tx_count"))

if __name__ == "__main__":
    main()

