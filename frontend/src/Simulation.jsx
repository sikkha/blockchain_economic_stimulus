import React, { useState, useEffect } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, CartesianGrid, ResponsiveContainer } from "recharts";

/**
 * Simulation dashboard component.  This component renders a simple form for
 * adjusting policy parameters (leakage shares L_i, spending weights omega_i,
 * lambda, tau and G) and runs the simulation via the backend API.  It
 * also displays the results returned from the server.
 */
export default function Simulation() {
  // Presets loaded from the API
  const [presets, setPresets] = useState([]);
  const [selectedPreset, setSelectedPreset] = useState(null);

  // Simulation parameters (inputs)
  const [params, setParams] = useState({
    L: [0.7, 0.7, 0.7],
    omega: [1 / 3, 1 / 3, 1 / 3],
    lambda: 0.8,
    tau: 0.07,
    G: 300_000_000_000,
    venture: { alpha0: 0.02, alpha1: 0.03, alpha2: 0.0, participants_active: 100000 },
    nk: { x: 0.02, kappa: 0.1 },
    markov: { use: false, pi: [[0.85, 0.10, 0.05], [0.10, 0.80, 0.10], [0.05, 0.10, 0.85]], ell: [0.0, 0.5, 0.7], s0: [1.0, 0.0, 0.0] },
  });
  // Results returned from the simulation
  const [results, setResults] = useState(null);
  const [error, setError] = useState("");

  // Comparison results across preset scenarios
  const [compareResults, setCompareResults] = useState([]);
  const [compareError, setCompareError] = useState("");

  // Fetch presets on mount
  useEffect(() => {
    fetch("/api/sim/presets")
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data.presets)) {
          setPresets(data.presets);
        }
      })
      .catch((err) => console.error(err));
  }, []);

  // When a preset is selected, update the parameters
  function handlePresetChange(e) {
    const idx = e.target.value;
    if (idx === "") {
      setSelectedPreset(null);
      return;
    }
    const preset = presets[parseInt(idx, 10)];
    setSelectedPreset(preset.name);
    setParams((prev) => ({
      ...prev,
      ...preset.params,
    }));
  }

  // Handle changes to numeric inputs
  function handleInputChange(e, path) {
    const value = e.target.value;
    const numberValue = value === "" ? "" : Number(value);
    setParams((prev) => {
      const updated = { ...prev };
      // Deep clone arrays when necessary
      if (path.startsWith("L")) {
        const idx = parseInt(path.charAt(1), 10);
        updated.L = [...prev.L];
        updated.L[idx] = numberValue;
      } else if (path.startsWith("omega")) {
        const idx = parseInt(path.charAt(5), 10);
        updated.omega = [...prev.omega];
        updated.omega[idx] = numberValue;
      } else if (path === "lambda") {
        updated.lambda = numberValue;
      } else if (path === "tau") {
        updated.tau = numberValue;
      } else if (path === "G") {
        updated.G = numberValue;
      }
      return updated;
    });
  }

  // Toggle Markov usage
  function handleMarkovToggle() {
    setParams((prev) => ({
      ...prev,
      markov: { ...prev.markov, use: !prev.markov.use },
    }));
  }

  // Run simulation by POSTing to backend
  function runSimulation() {
    setError("");
    setResults(null);
    fetch("/api/sim/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    })
      .then(async (res) => {
        if (!res.ok) {
          const detail = (await res.json()).detail || "Unknown error";
          throw new Error(detail);
        }
        return res.json();
      })
      .then((data) => setResults(data))
      .catch((err) => {
        console.error(err);
        setError(err.message || "Failed to run simulation");
      });
  }

  // Compare preset scenarios by running simulation for each preset
  function comparePresets() {
    setCompareError("");
    setCompareResults([]);
    if (!presets || presets.length === 0) return;
    const calls = presets.map((preset) =>
      fetch("/api/sim/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(preset.params),
      }).then((res) => {
        if (!res.ok) {
          return res.json().then((j) => Promise.reject(new Error(j.detail || "Error")));
        }
        return res.json().then((data) => ({ name: preset.name, data }));
      })
    );
    Promise.all(calls)
      .then((vals) => setCompareResults(vals))
      .catch((err) => {
        console.error(err);
        setCompareError(err.message || "Failed to compare presets");
      });
  }

  // Helper to display lists nicely
  function formatList(list) {
    return Array.isArray(list) ? list.map((x) => x.toFixed(4)).join(", ") : "";
  }

  return (
    <div>
      <h2>Simulation Dashboard</h2>
      {/* Preset selector */}
      <div style={{ marginBottom: 12 }}>
        <label style={{ marginRight: 8 }}>Preset:</label>
        <select value={selectedPreset ? presets.findIndex((p) => p.name === selectedPreset) : ""} onChange={handlePresetChange}>
          <option value="">Custom</option>
          {presets.map((p, idx) => (
            <option key={idx} value={idx}>{p.name}</option>
          ))}
        </select>
      </div>
      {/* Input fields for L and omega */}
      <div style={{ display: "flex", gap: 16, marginBottom: 8 }}>
        <div>
          <strong>L (leakage)</strong>
          {params.L.map((val, idx) => (
            <div key={idx}>
              <label>L{idx + 1}: </label>
              <input type="number" step="0.01" value={val} onChange={(e) => handleInputChange(e, `L${idx}`)} />
            </div>
          ))}
        </div>
        <div>
          <strong>ω (weights)</strong>
          {params.omega.map((val, idx) => (
            <div key={idx}>
              <label>ω{idx + 1}: </label>
              <input type="number" step="0.01" value={val} onChange={(e) => handleInputChange(e, `omega${idx}`)} />
            </div>
          ))}
        </div>
        <div>
          <strong>Other</strong>
          <div>
            <label>λ: </label>
            <input type="number" step="0.01" value={params.lambda} onChange={(e) => handleInputChange(e, "lambda")} />
          </div>
          <div>
            <label>τ: </label>
            <input type="number" step="0.01" value={params.tau} onChange={(e) => handleInputChange(e, "tau")} />
          </div>
          <div>
            <label>G: </label>
            <input type="number" step="1000000000" value={params.G} onChange={(e) => handleInputChange(e, "G")} />
          </div>
        </div>
      </div>
      {/* Markov toggle */}
      <div style={{ marginBottom: 12 }}>
          <label>
            <input type="checkbox" checked={params.markov.use} onChange={handleMarkovToggle} /> Use Markov
          </label>
      </div>
      <button onClick={runSimulation}>Run Simulation</button>
      <button style={{ marginLeft: 8 }} onClick={comparePresets}>Compare Presets</button>
      {/* Error messages */}
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      {compareError && <p style={{ color: "crimson" }}>{compareError}</p>}
      {/* Results */}
      {results && (
        <div style={{ marginTop: 16 }}>
          <h3>Results</h3>
          <p><strong>Aggregate multiplier (k):</strong> {results.k.toFixed(4)}</p>
          <p><strong>Provincial multipliers (k_i):</strong> {formatList(results.k_i)}</p>
          <p><strong>Money creation (ΔM):</strong> {results.deltaM.toLocaleString()}</p>
          <p><strong>VAT:</strong> {results.vat.toLocaleString()}</p>
          <div>
            <h4>Venture formation</h4>
            <p><strong>D_i:</strong> {formatList(results.venture.D_i)}</p>
            <p><strong>P_v,i:</strong> {formatList(results.venture.Pv_i)}</p>
            <p><strong>V_i:</strong> {formatList(results.venture.V_i)}</p>
            <p><strong>Total ventures (V):</strong> {results.venture.V.toFixed(2)}</p>
          </div>
          <div>
            <h4>NK price impact</h4>
            <p><strong>Δπ (low):</strong> {results.nk.dPi_low.toFixed(4)}</p>
            <p><strong>Δπ (high):</strong> {results.nk.dPi_high.toFixed(4)}</p>
          </div>
          {results.markov && (
            <div>
              <h4>Markov (advanced)</h4>
              <p><strong>aVAT:</strong> {results.markov.aVAT.toFixed(4)}</p>
              <p><strong>aLEAK:</strong> {results.markov.aLEAK.toFixed(4)}</p>
              <p><strong>k_eff:</strong> {results.markov.k_eff.toFixed(4)}</p>
            </div>
          )}

          {/* Visualisation: bar chart for k_i by tier */}
          <div style={{ marginTop: 24 }}>
            <h4>Multiplier by Tier (k_i)</h4>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={results.k_i.map((value, idx) => ({ name: `Tier ${idx + 1}`, k: value }))} margin={{ top: 10, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="k" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Comparison charts (appears after running compare) */}
      {compareResults.length > 0 && (
        <div style={{ marginTop: 32 }}>
          <h3>Scenario Comparison</h3>
          <p>Comparing aggregate metrics across preset scenarios.</p>
          {/* Chart for aggregate multiplier k */}
          <div style={{ marginTop: 16 }}>
            <h4>Aggregate Multiplier (k)</h4>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={compareResults.map((r) => ({ name: r.name, value: r.data.k }))} margin={{ top: 10, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="value" name="k" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          {/* Chart for Delta M */}
          <div style={{ marginTop: 16 }}>
            <h4>Money Creation (ΔM)</h4>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={compareResults.map((r) => ({ name: r.name, value: r.data.deltaM }))} margin={{ top: 10, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="value" name="ΔM" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          {/* Chart for VAT */}
          <div style={{ marginTop: 16 }}>
            <h4>VAT Revenue</h4>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={compareResults.map((r) => ({ name: r.name, value: r.data.vat }))} margin={{ top: 10, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="value" name="VAT" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          {/* Chart for Total Ventures */}
          <div style={{ marginTop: 16 }}>
            <h4>Total Ventures</h4>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={compareResults.map((r) => ({ name: r.name, value: r.data.venture.V }))} margin={{ top: 10, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="value" name="V" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}