import React, { useState, useEffect } from "react";
import axios from "axios";
import "./thermostat.css";

export default function Thermostat({ username }) {
  const [insideTemp, setInsideTemp] = useState("--");
  const [outsideTemp, setOutsideTemp] = useState("--");
  const [hvacStatus, setHvacStatus] = useState("off");

  const [setTemp, setSetTemp] = useState(24);
  const [mode, setMode] = useState("heat");

  const fetchSimulationData = async () => {
    if (!username) return;
    try {
      // This matches the router prefix defined in main.py (/api)
      // and the endpoint defined in user_thermostat_api.py (/simulation/{username})
      const response = await axios.get(
        `http://localhost:8000/api/simulation/${username}`
      );
      const data = response.data;

      setInsideTemp(data.T_in_new);
      setOutsideTemp(data.T_out);
      setHvacStatus(data.hvac_mode);

      if (data.hvac_mode === "heating") setMode("heat");
      if (data.hvac_mode === "cooling") setMode("cool");
    } catch (error) {
      console.error("Error fetching simulation data:", error);
    }
  };

  useEffect(() => {
    // Initial fetch
    fetchSimulationData();

    // Poll every 5 seconds to update simulation
    const interval = setInterval(fetchSimulationData, 5000);
    return () => clearInterval(interval);
  }, [username]);

  const now = new Date();
  const dayOfWeek = now.toLocaleDateString([], { weekday: "short" });
  const currentTime = now.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div className="th-container">
      <div className="th-top-row">
        <div>{dayOfWeek}</div>
        <div className="th-time">{currentTime}</div>
        <div>Outside {outsideTemp}°</div>
      </div>

      <div className="th-main">
        <div className="th-section">
          <div className="th-label">Inside</div>
          <div className="th-temp">{insideTemp}°</div>
          <div className="th-status-text">
            {hvacStatus === "heating" && (
              <span style={{ color: "orange" }}>Heating...</span>
            )}
            {hvacStatus === "cooling" && (
              <span style={{ color: "cyan" }}>Cooling...</span>
            )}
            {hvacStatus === "off" && (
              <span style={{ color: "gray" }}>Idle</span>
            )}
          </div>
        </div>

        <div className="th-divider" />

        <div className="th-section">
          <div className="th-label">Set to</div>
          <div className="th-temp set">{setTemp}°</div>

          <div className="th-buttons">
            <button
              className="th-circle"
              onClick={() => setSetTemp((t) => t - 1)}
            >
              –
            </button>
            <button
              className="th-circle"
              onClick={() => setSetTemp((t) => t + 1)}
            >
              +
            </button>
          </div>
        </div>
      </div>

      <div className="th-modes">
        <button
          className={`th-mode-btn ${mode === "heat" ? "active-heat" : ""}`}
          onClick={() => setMode("heat")}
        >
          Heat
        </button>
        <button
          className={`th-mode-btn ${mode === "cool" ? "active-cool" : ""}`}
          onClick={() => setMode("cool")}
        >
          Cool
        </button>
      </div>
    </div>
  );
}
