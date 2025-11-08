import React, { useState } from "react";
import Simulation from "./Simulation.jsx";
import RealTimeMonitor from "./RealTimeMonitor.jsx";

// Tab bar component with two buttons
function Tabs({ tab, setTab }) {
  const buttonStyle = (active) => ({
    padding: "6px 12px",
    marginRight: 4,
    border: "1px solid #888",
    borderRadius: 4,
    backgroundColor: active ? "#007bff" : "#f0f0f0",
    color: active ? "#fff" : "#000",
    cursor: "pointer",
  });
  return (
    <div style={{ display: "flex", gap: 4, marginBottom: 16 }}>
      <button style={buttonStyle(tab === "sim")} onClick={() => setTab("sim")}>Simulation</button>
      <button style={buttonStyle(tab === "mon")} onClick={() => setTab("mon")}>Real‑time Monitor</button>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState("sim");
  return (
    <div style={{ fontFamily: "sans-serif", padding: 16 }}>
      <h1>ARC Hackathon Dashboard</h1>
      <Tabs tab={tab} setTab={setTab} />
      {tab === "sim" ? (
        <Simulation />
      ) : (
        <RealTimeMonitor />
      )}
    </div>
  );
}
