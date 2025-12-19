import React, { useState, useEffect, useCallback, useMemo } from "react";
import { Backend } from "../App";
import "./thermostat.css";

// Constants
const POLL_INTERVAL_MS = 5000;
const HVAC_MODES = ["heat", "cool", "off", "pre-heat", "pre-cool"];

const HVAC_STATUS_CONFIG = {
  heating: { color: "orange", text: "Heating..." },
  cooling: { color: "cyan", text: "Cooling..." },
  off: { color: "gray", text: "Idle" },
};

export default function Thermostat({ username }) {
  const [insideTemp, setInsideTemp] = useState("--");
  const [hvacStatus, setHvacStatus] = useState("off");
  const [setTemp, setSetTemp] = useState(24);
  const [mode, setMode] = useState("heat");

  // Fetch simulation data with useCallback
  const fetchSimulationData = useCallback(async () => {
    if (!username) return;

    try {
      const data = await Backend.getSimulation(username);
      setInsideTemp(data.T_in_new);
      setHvacStatus(data.hvac_mode);

      // Sync mode with HVAC status
      if (data.hvac_mode === "heating") setMode("heat");
      if (data.hvac_mode === "cooling") setMode("cool");
    } catch (error) {
      console.error("Error fetching simulation data:", error);
    }
  }, [username]);

  // Poll simulation data
  useEffect(() => {
    fetchSimulationData();
    const interval = setInterval(fetchSimulationData, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchSimulationData]);

  // Memoized date/time values
  const { dayOfWeek, currentTime } = useMemo(() => {
    const now = new Date();
    return {
      dayOfWeek: now.toLocaleDateString([], { weekday: "short" }),
      currentTime: now.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      }),
    };
  }, []);

  // Temperature adjustment handlers
  const decreaseTemp = useCallback(() => setSetTemp((t) => t - 1), []);
  const increaseTemp = useCallback(() => setSetTemp((t) => t + 1), []);

  // Get status display config
  const statusConfig = HVAC_STATUS_CONFIG[hvacStatus] || HVAC_STATUS_CONFIG.off;

  return (
    <div className="th-container">
      <div className="th-top-row">
        <span>{dayOfWeek}</span>
        <span className="th-time">{currentTime}</span>
      </div>

      <div className="th-main">
        <div className="th-section">
          <div className="th-label">Inside</div>
          <div className="th-temp">{insideTemp}°</div>
          <div className="th-status-text" style={{ color: statusConfig.color }}>
            {statusConfig.text}
          </div>
        </div>

        <div className="th-divider" />

        <div className="th-section">
          <div className="th-label">Set to</div>
          <div className="th-temp set">{setTemp}°</div>
          <div className="th-buttons">
            <button
              className="th-circle"
              onClick={decreaseTemp}
              aria-label="Decrease temperature"
            >
              –
            </button>
            <button
              className="th-circle"
              onClick={increaseTemp}
              aria-label="Increase temperature"
            >
              +
            </button>
          </div>
        </div>
      </div>

      <div className="th-modes">
        {HVAC_MODES.map((m) => (
          <button
            key={m}
            className={`th-mode-btn ${mode === m ? `active-${m}` : ""}`}
            onClick={() => setMode(m)}
          >
            {m.charAt(0).toUpperCase() + m.slice(1).replace("-", "-")}
          </button>
        ))}
      </div>
    </div>
  );
}
