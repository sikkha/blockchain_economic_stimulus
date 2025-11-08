"""
Agent runner module (ARC testnet demo) — ON-CHAIN ONLY
======================================================

Provides TWO flows:

1) run_three_wallet_demo()
   - Hard-coded ARC testnet wallets and token (env can override).
   - Sequence:
        a) Mint 1,000 mUSD to A (owner/deployer signs).
        b) Fund A and B with native ARC (for gas).
        c) Transfer 100 mUSD A -> B (eligible=1).
        d) Transfer 100 mUSD B -> C (eligible=0).
   - Persists normalized rows into SQLite `transactions` **for real receipts only**.
   - Seeds `agents` table for payer/vendor classification.
   - Returns a summary with tx hashes, final balances and UI rollups.
   - If *any* chain step fails, returns errors and **does not fabricate DB rows**.

2) run_simple_agent()
   - Legacy env-driven loop (kept for compatibility). It does NOT write to DB.

DB schema:
  transactions(id, txid, ts, block_number, from_address, to_address,
               amount_raw, amount_ui, tier_from, tier_to, is_mint, eligible, notes)
  agents(id, wallet UNIQUE, type, province, tier, meta_json)
  metrics(id=1, m1_obs, leakage, vat_est, smes_active, last_block)

Defaults:
  - DB_PATH resolves to "data/app.db" unless overridden by env.
  - Token decimals are read from chain, fallback to 6.
"""

from __future__ import annotations

import os
import time
import sqlite3
from typing import Dict, Any

from web3 import Web3
from eth_account import Account

from dotenv import load_dotenv
load_dotenv()

# ========= CONFIG (env overrides; hard-coded defaults preserved) =========
RPC_URL    = os.getenv("RPC_URL", "https://rpc.testnet.arc.network")
CHAIN_ID   = int(os.getenv("CHAIN_ID", "5042002"))
TOKEN_ADDR = os.getenv("TOKEN_ADDR", "0x70D758FdFd1Ae0d4Fb2682f50d0228Cd4B07c449")
DB_PATH    = os.getenv("DB_PATH", "data/app.db")

# ---- Owner/deployer (can call mint, funds others with native) ----
# TESTNET ONLY — DO NOT USE ON MAINNET
DEPLOYER_PK = os.getenv(
    "DEPLOYER_PK",
    "0x4095553577ba83901a71661f20c666075e248ff78d0c824527de650100c74ddf",
)

# ---- THREE WALLETS for the demo (A -> B -> C) ----
A_ADDR = os.getenv("A_ADDR", "0x00Bfab80Cce644e99D8c358B8Bf9670b61160396")
A_PK   = os.getenv("A_PK",   "0x1e110965879762703e739bcf3b7ed8779b083c7c56bd59857a2c74a5156a3753")

B_ADDR = os.getenv("B_ADDR", "0x981F76F4C4a7A6edb0A86480a5A3Cc794A69620a")
B_PK   = os.getenv("B_PK",   "0xc37b92ea40f7183232431bf462fb9b127e87dd9e0d938dc8f95878932401de46")

C_ADDR = os.getenv("C_ADDR", "0x884c9339e1765511b02f6C8e3D7d365d396D14C1")
# (C only receives; private key not required for this demo)

# ---- Human amounts (UI units) exported so router can compute totals ----
MINT_TO_A_UI        = float(os.getenv("MINT_TO_A_UI",        "1000"))
TRANSFER_A_TO_B_UI  = float(os.getenv("TRANSFER_A_TO_B_UI",  "100"))
TRANSFER_B_TO_C_UI  = float(os.getenv("TRANSFER_B_TO_C_UI",  "100"))

# ---- Native ARC funding for gas (wei) ----
FUND_PER_SENDER_WEI = int(os.getenv("FUND_PER_SENDER_WEI", str(5 * 10**16)))  # 0.05

# ---- Explicit gas limits ----
GAS_LIMIT_ERC20_TRANSFER = int(os.getenv("GAS_LIMIT_ERC20_TRANSFER", "120000"))
GAS_LIMIT_NATIVE_XFER    = int(os.getenv("GAS_LIMIT_NATIVE_XFER", "21000"))
GAS_LIMIT_MINT           = int(os.getenv("GAS_LIMIT_MINT", "200000"))

# ---- Token decimals fallback ----
TOKEN_DECIMALS_DEFAULT = int(os.getenv("TOKEN_DECIMALS_DEFAULT", "6"))

# ========= Minimal ERC-20 ABI (transfer/balanceOf/decimals/mint) =========
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

# ========= Helpers =========
def _fee_params(w3: Web3) -> tuple[int, int]:
    """Compute EIP-1559 fee parameters (maxFeePerGas, maxPriorityFeePerGas)."""
    latest = w3.eth.get_block("latest")
    base = latest.get("baseFeePerGas") or w3.eth.gas_price
    try:
        tip = w3.eth.max_priority_fee
    except Exception:
        tip = max(1, w3.eth.gas_price // 10_000)
    return int(base + tip * 2), int(tip)

def _send_signed(w3: Web3, tx: dict, pk: str) -> tuple[str, dict]:
    signed = w3.eth.account.sign_transaction(tx, private_key=pk)
    # Web3 v5 uses .rawTransaction; Web3 v6+ can use .raw_transaction
    raw = getattr(signed, "rawTransaction", None)
    if raw is None:
        raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raise RuntimeError("SignedTransaction has no rawTransaction/raw_transaction attribute")
    txh = w3.eth.send_raw_transaction(raw)
    rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=240)
    if rcpt.status != 1:
        raise RuntimeError(f"Tx failed: {txh.hex()}")
    return txh.hex(), rcpt

def _seed_agents(payer_addr: str, vendor_addr: str, db_path: str) -> None:
    """Insert payer and vendor entries into the agents table if they do not exist."""
    payer_addr = payer_addr.lower()
    vendor_addr = vendor_addr.lower()
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        # Payer tier=1
        c.execute(
            """
            INSERT OR IGNORE INTO agents (wallet, type, province, tier, meta_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (payer_addr, "payer", "", 1, "{}"),
        )
        # Vendor tier=1
        c.execute(
            """
            INSERT OR IGNORE INTO agents (wallet, type, province, tier, meta_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (vendor_addr, "vendor", "", 1, "{}"),
        )
        conn.commit()

def _insert_tx_row(
    txid: str,
    ts: int,
    block_number: int,
    from_addr: str,
    to_addr: str,
    amount_raw: int,
    amount_ui: float,
    is_mint: int,
    eligible: int,
    notes: str,
    tier_from: int = 1,
    tier_to: int = 1,
    db_path: str = DB_PATH,
) -> None:
    """Insert a normalized transaction row into SQLite."""
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO transactions
              (txid, ts, block_number, from_address, to_address,
               amount_raw, amount_ui, tier_from, tier_to, is_mint, eligible, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                txid,
                ts,
                block_number,
                from_addr.lower(),
                to_addr.lower(),
                int(amount_raw),
                float(amount_ui),
                int(tier_from),
                int(tier_to),
                int(is_mint),
                int(eligible),
                notes,
            ),
        )
        conn.commit()

# ========= DEMO: THREE-WALLET SEQUENCE (ON-CHAIN ONLY) =========
def run_three_wallet_demo() -> Dict[str, Any]:
    """
    ARC testnet demo (real chain only):
      - Mint 1000 mUSD to A (owner).
      - Fund A and B with native ARC for gas.
      - Transfer 100 mUSD A -> B (eligible=1).
      - Transfer 100 mUSD B -> C (eligible=0).

    Writes normalized rows into SQLite `transactions` for **real** receipts only.
    On any failure, returns an error summary and does **not** fabricate rows.
    """
    summary: Dict[str, Any] = {"steps": [], "errors": []}

    # Resolve DB path and ensure parent folder exists
    abs_db = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(abs_db), exist_ok=True)

    # Seed agents so UI can classify transfers
    try:
        _seed_agents(A_ADDR, B_ADDR, abs_db)
    except Exception as exc:
        summary["errors"].append(f"Seed agents failed (ignored): {exc}")

    try:
        # Web3 + contract
        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        assert w3.is_connected(), "RPC not reachable"

        token_addr = Web3.to_checksum_address(TOKEN_ADDR)
        token = w3.eth.contract(address=token_addr, abi=MIN_ABI)

        # Decimals
        try:
            decimals = token.functions.decimals().call()
        except Exception:
            decimals = TOKEN_DECIMALS_DEFAULT

        # Accounts
        deployer = Account.from_key(DEPLOYER_PK)
        A = Account.from_key(A_PK)
        B = Account.from_key(B_PK)
        C_cs = Web3.to_checksum_address(C_ADDR)

        max_fee, tip = _fee_params(w3)

        # (1) Mint to A
        nonce_dep = w3.eth.get_transaction_count(deployer.address)
        mint_raw = int(MINT_TO_A_UI * (10 ** decimals))
        tx_mint = token.functions.mint(Web3.to_checksum_address(A.address), mint_raw).build_transaction({
            "from": deployer.address,
            "nonce": nonce_dep,
            "chainId": CHAIN_ID,
            "type": 2,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": tip,
            "gas": GAS_LIMIT_MINT,
            "value": 0,
        })
        mint_txh, mint_rcpt = _send_signed(w3, tx_mint, DEPLOYER_PK)
        summary["steps"].append({"mint_to_A_tx": mint_txh})

        blk = w3.eth.get_block(mint_rcpt.blockNumber)
        ts = int(blk.get("timestamp", time.time()))
        _insert_tx_row(
            txid=mint_txh,
            ts=ts,
            block_number=mint_rcpt.blockNumber,
            from_addr="0x0000000000000000000000000000000000000000",
            to_addr=A.address,
            amount_raw=mint_raw,
            amount_ui=mint_raw / (10 ** decimals),
            is_mint=1,
            eligible=0,
            notes="mint",
            tier_from=0,
            tier_to=1,
            db_path=abs_db,
        )

        # (2) Fund A & B with native ARC
        nonce_dep += 1
        for target in [A.address, B.address]:
            tx = {
                "to": Web3.to_checksum_address(target),
                "value": FUND_PER_SENDER_WEI,
                "nonce": nonce_dep,
                "chainId": CHAIN_ID,
                "type": 2,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": tip,
                "gas": GAS_LIMIT_NATIVE_XFER,
            }
            fund_txh, _ = _send_signed(w3, tx, DEPLOYER_PK)
            summary["steps"].append({"fund_native": {"target": target, "tx": fund_txh}})
            nonce_dep += 1

        # (3) A -> B (eligible)
        transfer1_raw = int(TRANSFER_A_TO_B_UI * (10 ** decimals))
        nonceA = w3.eth.get_transaction_count(A.address)
        tx1 = token.functions.transfer(Web3.to_checksum_address(B.address), transfer1_raw).build_transaction({
            "from": A.address,
            "nonce": nonceA,
            "chainId": CHAIN_ID,
            "type": 2,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": tip,
            "gas": GAS_LIMIT_ERC20_TRANSFER,
            "value": 0,
        })
        a2b_txh, a2b_rcpt = _send_signed(w3, tx1, A.key)  # bytes ok
        summary["steps"].append({"A_to_B_tx": a2b_txh})

        blk = w3.eth.get_block(a2b_rcpt.blockNumber)
        ts = int(blk.get("timestamp", time.time()))
        _insert_tx_row(
            txid=a2b_txh,
            ts=ts,
            block_number=a2b_rcpt.blockNumber,
            from_addr=A.address,
            to_addr=B.address,
            amount_raw=transfer1_raw,
            amount_ui=transfer1_raw / (10 ** decimals),
            is_mint=0,
            eligible=1,  # vendor B
            notes="A->B",
            tier_from=1,
            tier_to=1,
            db_path=abs_db,
        )

        # (4) B -> C (non-eligible)
        transfer2_raw = int(TRANSFER_B_TO_C_UI * (10 ** decimals))
        nonceB = w3.eth.get_transaction_count(B.address)
        tx2 = token.functions.transfer(C_cs, transfer2_raw).build_transaction({
            "from": B.address,
            "nonce": nonceB,
            "chainId": CHAIN_ID,
            "type": 2,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": tip,
            "gas": GAS_LIMIT_ERC20_TRANSFER,
            "value": 0,
        })
        b2c_txh, b2c_rcpt = _send_signed(w3, tx2, B.key)
        summary["steps"].append({"B_to_C_tx": b2c_txh})

        blk = w3.eth.get_block(b2c_rcpt.blockNumber)
        ts = int(blk.get("timestamp", time.time()))
        _insert_tx_row(
            txid=b2c_txh,
            ts=ts,
            block_number=b2c_rcpt.blockNumber,
            from_addr=B.address,
            to_addr=C_cs,
            amount_raw=transfer2_raw,
            amount_ui=transfer2_raw / (10 ** decimals),
            is_mint=0,
            eligible=0,  # C is not a registered vendor in this demo
            notes="B->C",
            tier_from=1,
            tier_to=1,
            db_path=abs_db,
        )

        # Final balances
        balA = token.functions.balanceOf(Web3.to_checksum_address(A.address)).call()
        balB = token.functions.balanceOf(Web3.to_checksum_address(B.address)).call()
        balC = token.functions.balanceOf(C_cs).call()
        summary["final_balances"] = {
            "A": {"raw": balA, "ui": balA / (10 ** decimals)},
            "B": {"raw": balB, "ui": balB / (10 ** decimals)},
            "C": {"raw": balC, "ui": balC / (10 ** decimals)},
        }
        summary["decimals"] = decimals
        summary["token"] = TOKEN_ADDR

        # UI rollups
        summary["tx_count"] = 2
        summary["transferred_ui"] = TRANSFER_A_TO_B_UI + TRANSFER_B_TO_C_UI
        summary["mode"] = "on_chain"
        return summary

    except Exception as exc:
        summary["errors"].append(f"On-chain path failed: {exc}")
        summary["mode"] = "on_chain_failed"
        return summary

# ========= LEGACY ENV-DRIVEN AGENT (kept for compatibility; no DB) =========
def run_simple_agent() -> Dict[str, Any]:
    """
    Execute a simple agent routine driven by env vars (payer->vendor loop).
    NOTE: This legacy function does NOT persist to DB. Prefer run_three_wallet_demo().
    """
    # Load environment
    rpc_url = os.getenv("RPC_URL")
    chain_id_str = os.getenv("CHAIN_ID")
    token_addr = os.getenv("TOKEN_ADDR")
    payer_pk = os.getenv("AGENT_PAYER_PRIVATE_KEY") or os.getenv("PREFUNDED_PRIVATE_KEY")
    vendor_addr = os.getenv("AGENT_VENDOR_ADDR")

    # Validate required env
    missing = []
    if not rpc_url:
        missing.append("RPC_URL")
    if not chain_id_str:
        missing.append("CHAIN_ID")
    if not token_addr:
        missing.append("TOKEN_ADDR")
    if not payer_pk:
        missing.append("AGENT_PAYER_PRIVATE_KEY or PREFUNDED_PRIVATE_KEY")
    if not vendor_addr:
        missing.append("AGENT_VENDOR_ADDR")
    if missing:
        return {"error": f"Missing environment variables: {', '.join(missing)}"}

    # Convert chain_id
    try:
        chain_id = int(chain_id_str)
    except ValueError:
        return {"error": f"Invalid CHAIN_ID: {chain_id_str}"}

    # Connect to Web3
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            return {"error": "RPC not reachable"}
    except Exception as exc:
        return {"error": f"Web3 connection error: {exc}"}

    token_addr = Web3.to_checksum_address(token_addr)
    vendor_addr = Web3.to_checksum_address(vendor_addr)

    # Load payer account
    try:
        payer_account = Account.from_key(payer_pk)
    except Exception as exc:
        return {"error": f"Invalid private key: {exc}"}

    token = w3.eth.contract(address=token_addr, abi=MIN_ABI)

    # Determine decimals
    try:
        decimals = token.functions.decimals().call()
    except Exception:
        decimals = TOKEN_DECIMALS_DEFAULT

    # Determine transfer amount (UI units)
    try:
        transfer_ui = float(os.getenv("AGENT_TRANSFER_UI", "100"))
    except ValueError:
        transfer_ui = 100.0
    transfer_raw = int(transfer_ui * (10 ** decimals))

    # Determine number of transfers
    try:
        num_txs = int(os.getenv("AGENT_NUM_TXS", "5"))
    except ValueError:
        num_txs = 5

    summary: Dict[str, Any] = {
        "tx_count": 0,
        "transferred_ui": 0.0,
        "errors": [],
    }

    # Fees and nonce
    try:
        max_fee, tip = _fee_params(w3)
        nonce = w3.eth.get_transaction_count(payer_account.address)
    except Exception as exc:
        summary["errors"].append(f"Init failed: {exc}")
        return summary

    for _ in range(num_txs):
        try:
            bal_raw = token.functions.balanceOf(payer_account.address).call()
            if bal_raw < transfer_raw:
                break
            tx = token.functions.transfer(vendor_addr, transfer_raw).build_transaction(
                {
                    "from": payer_account.address,
                    "nonce": nonce,
                    "chainId": chain_id,
                    "type": 2,
                    "maxFeePerGas": max_fee,
                    "maxPriorityFeePerGas": tip,
                }
            )
            tx["gas"] = int(w3.eth.estimate_gas(tx))
            txh, rcpt = _send_signed(w3, tx, payer_account.key.hex())
            if rcpt.get("status", 0) != 1:
                summary["errors"].append(f"Tx {txh} failed")
                break
            summary["tx_count"] += 1
            summary["transferred_ui"] += transfer_ui
            nonce += 1
        except Exception as exc:
            summary["errors"].append(f"Error during transfer: {exc}")
            break

    return summary

# --- Negotiated deal shim (imports agent_chain) ---
def run_negotiated_three_wallet_deal() -> Dict[str, Any]:
    from .agent_chain import run_negotiated_three_wallet_deal as _go
    return _go()

