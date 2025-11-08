"""
Watcher subsystem for the ARC hackathon dashboard.

This module defines a Watcher class that monitors the ARC testnet for
USDC‑like transfer events on a specified token contract and writes
normalized transaction records into a SQLite database.  It also maintains
rolling metrics such as observed money creation, leakage, VAT estimates
and active SMEs.  The watcher runs in a background thread and polls
periodically for new logs.

Note: Network calls may block; this implementation uses a separate
thread to avoid blocking the FastAPI event loop.  The environment must
provide RPC_URL, CHAIN_ID, and TOKEN_ADDR via environment variables.
"""
from __future__ import annotations

import os
import threading
import time
import sqlite3
from datetime import datetime
from typing import Optional, Dict

from web3 import Web3


class Watcher:
    def __init__(
        self,
        db_path: str,
        rpc_url: str,
        token_addr: str,
        tau: float = 0.07,
        lam: float = 0.8,
        poll_interval: float = 5.0,
    ) -> None:
        self.db_path = db_path
        self.rpc_url = rpc_url
        self.token_addr = Web3.to_checksum_address(token_addr)
        self.tau = tau
        self.lam = lam
        self.poll_interval = poll_interval
        self.w3: Optional[Web3] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # topic for ERC20 Transfer event
        self.transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)").hex()
        # Keep track of last processed block to avoid duplicates
        self.last_block_key = "last_block"

    def start(self) -> None:
        """Start the watcher in a daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="WatcherThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the watcher thread to stop and wait for it to finish."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def run(self) -> None:
        """Main loop: connect to Web3, ensure DB, then poll for logs."""
        # Connect to Web3 provider
        try:
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            if not self.w3.is_connected():
                print("Watcher: RPC not reachable")
                return
        except Exception as exc:
            print(f"Watcher: Web3 connection error: {exc}")
            return
        # Ensure DB schema
        self._init_db()
        # Poll loop
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as exc:
                # Log and continue
                print(f"Watcher: error during poll: {exc}")
            time.sleep(self.poll_interval)

    def _init_db(self) -> None:
        """Initialize the SQLite database with required tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            # Transactions table
            c.execute(
                """
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
                )
                """
            )
            # Metrics table: store cumulative metrics
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    m1_obs REAL,
                    leakage REAL,
                    vat_est REAL,
                    smes_active INTEGER,
                    last_block INTEGER
                )
                """
            )
            # Initialise metrics row if absent
            c.execute("SELECT COUNT(*) FROM metrics WHERE id=1")
            if c.fetchone()[0] == 0:
                c.execute(
                    "INSERT INTO metrics (id, m1_obs, leakage, vat_est, smes_active, last_block) VALUES (1, 0, 0, 0, 0, 0)"
                )
            # Agents table: track registered payers and vendors.  The watcher
            # relies on this table to classify transactions by tier and
            # eligibility.  Create it if it does not exist.  Note: the
            # wallet column is unique so that inserts/updates can be
            # idempotent.  Additional metadata can be stored as JSON.
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet TEXT UNIQUE NOT NULL,
                    type TEXT,
                    province TEXT,
                    tier INTEGER,
                    meta_json TEXT
                )
                """
            )
            conn.commit()

    def _get_metrics(self) -> Dict[str, float]:
        """Load current metrics from the database."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT m1_obs, leakage, vat_est, smes_active, last_block FROM metrics WHERE id=1")
            row = c.fetchone()
            return {
                "m1_obs": row[0],
                "leakage": row[1],
                "vat_est": row[2],
                "smes_active": row[3],
                "last_block": row[4],
            }

    def _update_metrics(self, m1_inc=0.0, leak_inc=0.0, vat_inc=0.0, smes_active=None, last_block=None) -> None:
        """Incrementally update metrics and optionally set smes_active and last_block."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT m1_obs, leakage, vat_est, smes_active, last_block FROM metrics WHERE id=1"
            )
            row = c.fetchone()
            m1 = row[0] + m1_inc
            leak = row[1] + leak_inc
            vat = row[2] + vat_inc
            smes = smes_active if smes_active is not None else row[3]
            block = last_block if last_block is not None else row[4]
            c.execute(
                "UPDATE metrics SET m1_obs=?, leakage=?, vat_est=?, smes_active=?, last_block=? WHERE id=1",
                (m1, leak, vat, smes, block),
            )
            conn.commit()

    def _poll(self) -> None:
        """Fetch new Transfer logs since the last processed block and insert into DB."""
        if not self.w3:
            return
        metrics = self._get_metrics()
        last_block = metrics.get("last_block", 0)
        latest_block = self.w3.eth.block_number
        if latest_block <= last_block:
            return
        # Build filter
        logs = self.w3.eth.get_logs(
            {
                "fromBlock": last_block + 1,
                "toBlock": latest_block,
                "address": self.token_addr,
                "topics": [self.transfer_topic],
            }
        )
        # Load agents mapping
        agents = self._load_agents()
        # Track vendor sales counts for SME metric
        vendor_sales: Dict[str, int] = {}
        for event in logs:
            tx_hash = event["transactionHash"].hex()
            block_num = event["blockNumber"]
            # Decode topics: Transfer indexed parameters: from, to
            topics = event["topics"]
            # topics[0] is event signature; topics[1] and topics[2] are indexed parameters
            from_addr = "0x" + topics[1].hex()[-40:]
            to_addr = "0x" + topics[2].hex()[-40:]
            from_addr = Web3.to_checksum_address(from_addr)
            to_addr = Web3.to_checksum_address(to_addr)
            # Data holds the value
            value = int(event["data"], 16)
            amount_ui = value / (10 ** 6)  # assume 6 decimals (USDC style)
            # Get timestamp
            block = self.w3.eth.get_block(block_num)
            ts = block["timestamp"]
            # Classification
            is_mint = int(from_addr.lower() == "0x0000000000000000000000000000000000000000")
            # Look up tiers
            tier_from = agents.get(from_addr.lower(), {}).get("tier", -1)
            tier_to = agents.get(to_addr.lower(), {}).get("tier", -1)
            # Eligible if to_addr is a registered vendor
            eligible = int(agents.get(to_addr.lower(), {}).get("type") == "vendor")
            # Insert into transactions
            self._insert_transaction(
                txid=tx_hash,
                ts=ts,
                block_number=block_num,
                from_address=from_addr,
                to_address=to_addr,
                amount_raw=value,
                amount_ui=amount_ui,
                tier_from=tier_from,
                tier_to=tier_to,
                is_mint=is_mint,
                eligible=eligible,
            )
            # Update metrics
            # M1: add minted amounts and eligible spends
            m1_inc = 0.0
            leak_inc = 0.0
            vat_inc = 0.0
            if is_mint:
                m1_inc += amount_ui
            else:
                if eligible:
                    m1_inc += amount_ui
                    vat_inc += self.tau * amount_ui
                    # Count SME sales
                    vendor_sales[to_addr.lower()] = vendor_sales.get(to_addr.lower(), 0) + 1
                else:
                    leak_inc += amount_ui
            self._update_metrics(m1_inc, leak_inc, vat_inc)
        # Update SME count based on sales counts threshold
        current_metrics = self._get_metrics()
        active_smes = current_metrics["smes_active"]
        threshold = 3  # simple threshold: at least 3 sales
        count_active = len([addr for addr, cnt in vendor_sales.items() if cnt >= threshold])
        if count_active != active_smes:
            # Update metrics with new smes_active count
            self._update_metrics(smes_active=count_active)
        # Update last_block
        self._update_metrics(last_block=latest_block)

    def _insert_transaction(
        self,
        txid: str,
        ts: int,
        block_number: int,
        from_address: str,
        to_address: str,
        amount_raw: int,
        amount_ui: float,
        tier_from: int,
        tier_to: int,
        is_mint: int,
        eligible: int,
        notes: str = "",
    ) -> None:
        """Insert a transaction record into the database."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO transactions (txid, ts, block_number, from_address, to_address, amount_raw, amount_ui, tier_from, tier_to, is_mint, eligible, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    txid,
                    ts,
                    block_number,
                    from_address,
                    to_address,
                    amount_raw,
                    amount_ui,
                    tier_from,
                    tier_to,
                    is_mint,
                    eligible,
                    notes,
                ),
            )
            conn.commit()

    def _load_agents(self) -> Dict[str, Dict[str, any]]:
        """Load agent information from the database into a dict keyed by wallet address (lowercase)."""
        agents = {}
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""SELECT wallet, type, province, tier, meta_json FROM agents""")
            for row in c.fetchall():
                wallet = row[0].lower()
                agents[wallet] = {
                    "type": row[1],
                    "province": row[2],
                    "tier": row[3],
                    "meta": row[4],
                }
        return agents