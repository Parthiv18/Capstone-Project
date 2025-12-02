import React, { useState } from "react";
import "./thermostat.css";

export default function Thermostat() {
  const [setTemp, setSetTemp] = useState(24);
  const [mode, setMode] = useState("heat");

  const now = new Date();
  const dayOfWeek = now.toLocaleDateString([], { weekday: "short" });
  const currentTime = now.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  const insideTemp = 22;
  const outsideTemp = -5;

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
