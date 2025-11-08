import React, { useEffect, useMemo, useState } from "react";

/**
 * RealTimeMonitor — negotiation-aware dashboard (resilient version)
 * - Polls /api/mon/stream for tx rows
 * - Polls /api/mon/deals (fallback /api/deals) and normalizes payloads
 * - Shows admitted & settled by default (configurable)
 * - Click a deal -> /api/mon/deals/{deal_id}/log
 * - Start negotiated agent via POST /api/mon/enact
 */
export default function RealTimeMonitor() {
  // Live data
  const [events, setEvents] = useState([]);
  const [deals, setDeals] = useState([]);
  const [selectedDealId, setSelectedDealId] = useState(null);
  const [dealLog, setDealLog] = useState([]);
  const [metrics, setMetrics] = useState({
    m1_obs: 0,
    leakage: 0,
    vat_est: 0,
    smes_active: 0,
  });

  // UI states
  const [agentStatus, setAgentStatus] = useState("");
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("admitted,settled");
  const [debugDealsPayload, setDebugDealsPayload] = useState(null);

  /* ---------------- Poll: stream + metrics ---------------- */
  useEffect(() => {
    let mounted = true;

    async function pull() {
      try {
        const [streamRes, metricsRes] = await Promise.all([
          fetch("/api/mon/stream"),
          fetch("/api/mon/metrics"),
        ]);

        const streamJson = await streamRes.json();
        const metricsJson = await metricsRes.json();

        if (!mounted) return;

        setEvents(Array.isArray(streamJson.events) ? streamJson.events : []);
        setMetrics({
          m1_obs: metricsJson.m1_obs || 0,
          leakage: metricsJson.leakage || 0,
          vat_est: metricsJson.vat_est || 0,
          smes_active: metricsJson.smes_active || 0,
        });
      } catch (e) {
        console.error(e);
        if (mounted) setError("Failed to pull data");
      }
    }

    pull();
    const id = setInterval(pull, 5000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, []);

  /* ---------------- Poll: deals (normalize shapes) ---------------- */
  useEffect(() => {
    let mounted = true;

    async function pullDeals() {
      try {
        const url1 = `/api/mon/deals?status=${encodeURIComponent(
          statusFilter
        )}&order=desc&limit=200`;
        let res = await fetch(url1);
        let json;

        if (res.ok) {
          json = await res.json();
        } else {
          const url2 = `/api/deals?status=${encodeURIComponent(
            statusFilter
          )}&order=desc&limit=200`;
          res = await fetch(url2);
          json = await res.json();
        }

        if (!mounted) return;

        setDebugDealsPayload(json);

        const raw =
          (Array.isArray(json) && json) || json?.deals || json?.items || [];
        const normalized = raw.map(normalizeDealRow);
        setDeals(normalized);
      } catch (e) {
        console.error(e);
        if (mounted) setError("Failed to pull deals");
      }
    }

    pullDeals();
    const id = setInterval(pullDeals, 5000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, [statusFilter]);

  /* ---------------- Poll: selected deal log ---------------- */
  useEffect(() => {
    let mounted = true;
    if (!selectedDealId) {
      setDealLog([]);
      return;
    }
    async function fetchLog() {
      try {
        const res = await fetch(
          `/api/mon/deals/${encodeURIComponent(selectedDealId)}/log`
        );
        if (!res.ok) {
          // If you expose an alternate route, add a fallback here.
          return;
        }
        const json = await res.json();
        if (!mounted) return;
        setDealLog(Array.isArray(json.log) ? json.log : []);
      } catch (e) {
        console.error(e);
      }
    }
    fetchLog();
    const id = setInterval(fetchLog, 5000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, [selectedDealId]);

  /* ---------------- Actions ---------------- */
  async function startNegotiatedAgent() {
    setAgentStatus("Starting agent…");
    try {
      const res = await fetch("/api/mon/enact", { method: "POST" });
      const json = await res.json();
      if (json.ok) {
        setAgentStatus(
          `Agent done (${json.mode}): ${json.tx_count} txs, ${fmtNum(
            json.transferred_ui
          )} tokens, deal ${json.deal_id} (in ${json.elapsed}s)`
        );
        if (json.deal_id) setSelectedDealId(String(json.deal_id));
      } else {
        setAgentStatus(`Agent error: ${json.error || "unknown error"}`);
      }
    } catch (e) {
      console.error(e);
      setAgentStatus("Agent failed to start");
    }
  }

  function refreshOnce() {
    // Triggers the deals effect again (no-op but useful during debug)
    setStatusFilter((s) => s);
  }

  /* ---------------- Derived ---------------- */
  const selectedDeal = useMemo(
    () =>
      deals.find((d) => String(d.deal_id) === String(selectedDealId)) || null,
    [deals, selectedDealId]
  );

  /* ---------------- Render ---------------- */
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <h2>Real-time Monitor (Negotiation-aware)</h2>
      {error && <p style={{ color: "crimson" }}>{error}</p>}

      {/* Controls */}
      <section style={card}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <button onClick={startNegotiatedAgent}>Start Negotiated Agent</button>
          <button onClick={refreshOnce}>Refresh</button>
          <label style={{ fontSize: 12 }}>
            Status filter:&nbsp;
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="admitted,settled">admitted + settled</option>
              <option value="admitted">admitted</option>
              <option value="settled">settled</option>
              <option value="">(no filter)</option>
            </select>
          </label>
          {agentStatus && <span>{agentStatus}</span>}
        </div>
      </section>

      {/* KPIs */}
      <section style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <KPI title="M1 observed" value={fmtNum(metrics.m1_obs)} />
        <KPI title="Leakage" value={fmtNum(metrics.leakage)} />
        <KPI title="VAT (est.)" value={fmtNum(metrics.vat_est)} />
        <KPI title="Active SMEs" value={fmtNum(metrics.smes_active)} />
      </section>

      {/* Live Transactions */}
      <section style={card}>
        <h3 style={{ marginTop: 0 }}>Recent Transactions</h3>
        {events.length === 0 ? (
          <p>No transactions yet.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Time</th>
                  <th style={th}>Tx</th>
                  <th style={th}>From → To</th>
                  <th style={th}>Amount</th>
                  <th style={th}>Tags</th>
                  <th style={th}>Deal</th>
                </tr>
              </thead>
              <tbody>
                {events.map((ev, i) => {
                  const amount =
                    ev.amount_ui ??
                    (typeof ev.amount_raw === "number"
                      ? ev.amount_raw / 1e6
                      : 0);
                  const when =
                    typeof ev.ts === "number"
                      ? new Date(ev.ts * 1000).toLocaleString()
                      : "-";
                  const dealCell =
                    ev.deal_id && String(ev.deal_id).length
                      ? String(ev.deal_id)
                      : "";
                  return (
                    <tr key={i}>
                      <td style={td}>{when}</td>
                      <td style={td} title={ev.txid || ""}>
                        {shorten(ev.txid)}
                      </td>
                      <td style={td}>
                        {shorten(ev.from_address || ev.from)} →{" "}
                        {shorten(ev.to_address || ev.to)}
                      </td>
                      <td style={td}>{fmtNum(amount)}</td>
                      <td style={td}>{formatTags(ev)}</td>
                      <td
                        style={{
                          ...td,
                          color: "#0b69ff",
                          cursor: dealCell ? "pointer" : "default",
                          textDecoration: dealCell ? "underline" : "none",
                        }}
                        onClick={() =>
                          dealCell ? setSelectedDealId(dealCell) : null
                        }
                        title={dealCell}
                      >
                        {dealCell}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Deals + Log */}
      <section style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 16 }}>
        {/* Deals list */}
        <div style={card}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
            <h3 style={{ marginTop: 0 }}>Deals</h3>
            <small style={{ opacity: 0.7 }}>{deals.length} rows</small>
          </div>
          {deals.length === 0 ? (
            <div>
              <p>No deals found. (Tip: ensure API includes <code>admitted</code>.)</p>
              {debugDealsPayload && (
                <details>
                  <summary>Show last raw payload</summary>
                  <pre style={pre}>{safeStringify(debugDealsPayload)}</pre>
                </details>
              )}
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={table}>
                <thead>
                  <tr>
                    <th style={th}>Deal</th>
                    <th style={th}>Status</th>
                    <th style={th}>Mode</th>
                    <th style={th}>Buyer</th>
                    <th style={th}>Seller</th>
                    <th style={th}>SKU</th>
                    <th style={th}>Qty</th>
                    <th style={th}>Unit</th>
                    <th style={th}>Notional</th>
                    <th style={th}>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {deals.map((d) => (
                    <tr
                      key={String(d.deal_id)}
                      style={{
                        background:
                          String(d.deal_id) === String(selectedDealId) ? "#eef6ff" : "transparent",
                        cursor: "pointer",
                      }}
                      onClick={() => setSelectedDealId(String(d.deal_id))}
                      title={String(d.deal_id)}
                    >
                      <td style={td}>{shorten(d.deal_id)}</td>
                      <td style={td}>{d.status || "-"}</td>
                      <td style={td}>{d.mode || "-"}</td>
                      <td style={td}>{shorten(d.buyer)}</td>
                      <td style={td}>{shorten(d.seller)}</td>
                      <td style={td}>{d.sku || ""}</td>
                      <td style={td}>{fmtNum(d.qty)}</td>
                      <td style={td}>{fmtNum(d.unit_price)}</td>
                      <td style={td}>{fmtNum(d.notional_ui)}</td>
                      <td style={td}>
                        {d.created_ts
                          ? new Date(Number(d.created_ts) * 1000).toLocaleString()
                          : d.created_iso
                          ? new Date(d.created_iso).toLocaleString()
                          : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Deal detail / log */}
        <div style={card}>
          <h3 style={{ marginTop: 0 }}>
            Deal Log {selectedDealId ? `(${selectedDealId})` : ""}
          </h3>
          {!selectedDealId ? (
            <p>Select a deal to view its negotiation log.</p>
          ) : dealLog.length === 0 ? (
            <p>No log entries yet.</p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={table}>
                <thead>
                  <tr>
                    <th style={th}>Turn</th>
                    <th style={th}>Role</th>
                    <th style={th}>Type</th>
                    <th style={th}>Payload</th>
                    <th style={th}>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {dealLog.map((l, i) => (
                    <tr key={i}>
                      <td style={td}>{l.turn}</td>
                      <td style={td}>{l.role}</td>
                      <td style={td}>{l.subtype}</td>
                      <td style={{ ...td, maxWidth: 480 }}>
                        <code style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                          {l.payload_json}
                        </code>
                      </td>
                      <td style={td}>
                        {l.ts ? new Date(l.ts * 1000).toLocaleString() : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Optional commitment preview */}
          {selectedDeal && selectedDeal.commitment_json && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 12, color: "#666", marginBottom: 4 }}>
                Final Commitment
              </div>
              <pre style={pre}>{prettyJson(selectedDeal.commitment_json)}</pre>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

/* ---------------- helpers ---------------- */

function normalizeDealRow(r) {
  // Accept already-normalized rows or DB-style rows
  const committed = safeParseJSON(r.commitment_json);
  const qty = pickNumber(r.qty, committed?.quantity);
  const unit = pickNumber(r.unit_price, committed?.unit_price);
  const notional =
    pickNumber(r.notional_ui, committed?.total_value) ??
    (qty != null && unit != null ? qty * unit : null);

  // created_ts: number/string/undefined
  let created_ts = r.created_ts;
  if (created_ts != null && typeof created_ts !== "number") {
    const n = Number(created_ts);
    created_ts = Number.isNaN(n) ? null : n;
  }

  return {
    deal_id: String(r.deal_id ?? r.id ?? ""),
    status: r.status ?? "-",
    mode: r.mode ?? r.type ?? "sim",
    buyer: r.buyer ?? r.payer ?? "",
    seller: r.seller ?? r.vendor ?? "",
    sku: r.sku ?? r.product ?? "",
    qty,
    unit_price: unit,
    notional_ui: notional,
    created_ts,
    created_iso: r.created_iso ?? null,
    commitment_json: r.commitment_json ?? committed ?? null,
  };
}

function pickNumber(a, b) {
  const c = a ?? b;
  const n = Number(c);
  return Number.isFinite(n) ? n : null;
}

function fmtNum(v) {
  const n = Number(v || 0);
  return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
}
function shorten(s, head = 6, tail = 4) {
  if (!s) return "";
  const str = String(s);
  if (str.length <= head + tail + 3) return str;
  return `${str.slice(0, head)}…${str.slice(-tail)}`;
}
function prettyJson(maybeJson) {
  try {
    const obj = typeof maybeJson === "string" ? JSON.parse(maybeJson) : maybeJson;
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(maybeJson);
  }
}
function safeParseJSON(maybeJson) {
  if (!maybeJson) return null;
  try {
    return typeof maybeJson === "string" ? JSON.parse(maybeJson) : maybeJson;
  } catch {
    return null;
  }
}
function safeStringify(x) {
  try {
    return JSON.stringify(x, null, 2);
  } catch {
    return String(x);
  }
}
function formatTags(ev) {
  const tags = [];
  const isMint = !!ev.is_mint;
  if (isMint) tags.push("Mint");
  if (!isMint) tags.push(ev.eligible ? "Eligible" : "Leak");
  if (ev.tier_from !== undefined && ev.tier_to !== undefined) {
    tags.push(`T${ev.tier_from}→T${ev.tier_to}`);
  }
  return tags.join(", ");
}

/* ---------------- styles ---------------- */
const card = {
  border: "1px solid #ddd",
  borderRadius: 8,
  padding: 12,
  background: "#fff",
};

function KPI({ title, value }) {
  return (
    <div style={{ ...card, minWidth: 140 }}>
      <div style={{ fontSize: 12, color: "#666" }}>{title}</div>
      <div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

const table = {
  borderCollapse: "collapse",
  width: "100%",
  fontSize: 14,
};

const th = {
  borderBottom: "1px solid #ccc",
  textAlign: "left",
  padding: "6px 8px",
  background: "#f8f8f8",
  position: "sticky",
  top: 0,
  zIndex: 1,
};

const td = {
  borderBottom: "1px solid #eee",
  padding: "6px 8px",
  verticalAlign: "top",
};

const pre = {
  margin: 0,
  padding: 8,
  background: "#f6f8fa",
  borderRadius: 6,
  border: "1px solid #e5e7eb",
  maxHeight: 260,
  overflow: "auto",
};