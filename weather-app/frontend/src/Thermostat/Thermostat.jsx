import React, { useState, useEffect, useCallback, useMemo } from "react";
import { Backend } from "../App";
import "./thermostat.css";

// Constants
const POLL_INTERVAL_MS = 5000;
const SCHEDULE_REFRESH_MS = 60000; // Refresh schedule every minute

const HVAC_STATUS_CONFIG = {
  heating: { color: "#ff6b35", text: "Heating..." },
  cooling: { color: "#00bcd4", text: "Cooling..." },
  off: { color: "#9e9e9e", text: "Idle" },
};

const MODE_COLORS = {
  heat: "#ff6b35",
  cool: "#00bcd4",
  "pre-heat": "#ff9800",
  "pre-cool": "#4fc3f7",
  off: "#9e9e9e",
};

export default function Thermostat({ username }) {
  const [insideTemp, setInsideTemp] = useState("--");
  const [outsideTemp, setOutsideTemp] = useState("--");
  const [hvacStatus, setHvacStatus] = useState("off");
  const [setTemp, setSetTemp] = useState(22);
  const [notifications, setNotifications] = useState([]);
  const [summary, setSummary] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  // Fetch simulation data
  const fetchSimulationData = useCallback(async () => {
    if (!username) return;

    try {
      const data = await Backend.getSimulation(username);
      setInsideTemp(data.T_in_new);
      setOutsideTemp(data.T_out);
      setHvacStatus(data.hvac_mode || "off");
      setError(null);
    } catch (err) {
      console.error("Error fetching simulation data:", err);
      setError("Unable to fetch temperature data");
    }
  }, [username]);

  // Fetch HVAC schedule (initial load - don't pass setTemp to get saved value)
  const fetchHVACSchedule = useCallback(async (explicitTemp = null) => {
    if (!username) return;

    try {
      setIsLoading(true);
      // Only pass temp if explicitly provided (user changed it)
      const data = await Backend.getHVACSchedule(username, explicitTemp);

      if (data.notifications) {
        setNotifications(data.notifications);
      }

      if (data.summary) {
        setSummary(data.summary);
        // Update setTemp from backend's saved/calculated value on initial load
        if (explicitTemp === null && data.summary.target_temp_c) {
          setSetTemp(Math.round(data.summary.target_temp_c));
        }
      }

      setError(null);
    } catch (err) {
      console.error("Error fetching HVAC schedule:", err);
      // Don't show error for schedule - it might not be available yet
    } finally {
      setIsLoading(false);
    }
  }, [username]);

  // Poll simulation data
  useEffect(() => {
    fetchSimulationData();
    const interval = setInterval(fetchSimulationData, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchSimulationData]);

  // Fetch schedule on mount only (will load saved setpoint)
  useEffect(() => {
    fetchHVACSchedule(null); // null = use saved value from backend
  }, [fetchHVACSchedule]);

  // Periodic refresh of schedule (keeps using saved value)
  useEffect(() => {
    const interval = setInterval(() => fetchHVACSchedule(null), SCHEDULE_REFRESH_MS);
    return () => clearInterval(interval);
  }, [fetchHVACSchedule]);

  // Refresh schedule when target temp changes
  const handleTempChange = useCallback(
    async (newTemp) => {
      setSetTemp(newTemp);
      // Debounced refresh after temp change - explicitly pass new temp to save it
      setTimeout(async () => {
        try {
          const data = await Backend.refreshHVACSchedule(username, newTemp);
          if (data.notifications) {
            setNotifications(data.notifications);
          }
          if (data.summary) {
            setSummary(data.summary);
          }
        } catch (err) {
          console.error("Error refreshing schedule:", err);
        }
      }, 500);
    },
    [username]
  );

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
  const decreaseTemp = useCallback(
    () => handleTempChange(setTemp - 1),
    [setTemp, handleTempChange]
  );
  const increaseTemp = useCallback(
    () => handleTempChange(setTemp + 1),
    [setTemp, handleTempChange]
  );

  // Get status display config
  const statusConfig = HVAC_STATUS_CONFIG[hvacStatus] || HVAC_STATUS_CONFIG.off;

  return (
    <div className="th-container">
      {/* Header */}
      <div className="th-top-row">
        <span>{dayOfWeek}</span>
        <span className="th-time">{currentTime}</span>
      </div>

      {/* Main Temperature Display */}
      <div className="th-main">
        <div className="th-section">
          <div className="th-label">Inside</div>
          <div className="th-temp">
            {typeof insideTemp === "number"
              ? insideTemp.toFixed(1)
              : insideTemp}
            °C
          </div>
          <div className="th-status-text" style={{ color: statusConfig.color }}>
            {statusConfig.text}
          </div>
        </div>

        <div className="th-divider" />

        <div className="th-section">
          <div className="th-label">Set to</div>
          <div className="th-temp set">{setTemp}°C</div>
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

      {/* Outside Temperature */}
      {outsideTemp !== "--" && (
        <div className="th-outside">
          <span className="th-outside-label">Outside:</span>
          <span className="th-outside-temp">
            {typeof outsideTemp === "number"
              ? outsideTemp.toFixed(1)
              : outsideTemp}
            °C
          </span>
        </div>
      )}

      {/* Summary Stats */}
      {summary && (
        <div className="th-summary">
          <div className="th-summary-item">
            <span className="th-summary-label">24h Energy</span>
            <span className="th-summary-value">
              {summary.total_energy_24h_kwh} kWh
            </span>
          </div>
          <div className="th-summary-item">
            <span className="th-summary-label">24h Cost</span>
            <span className="th-summary-value">${summary.total_cost_24h}</span>
          </div>
          <div className="th-summary-item">
            <span className="th-summary-label">Comfort</span>
            <span className="th-summary-value">{summary.comfort_score}%</span>
          </div>
        </div>
      )}

      {/* HVAC Notifications */}
      <div className="th-notifications">
        <div className="th-notifications-header">
          <span className="th-notifications-title">Smart HVAC Schedule</span>
          {isLoading && <span className="th-loading">Loading...</span>}
        </div>

        {notifications.length > 0 ? (
          <div className="th-notifications-list">
            {notifications.map((notif, index) => (
              <div
                key={index}
                className="th-notification"
                style={{
                  borderLeftColor: MODE_COLORS[notif.mode] || "#9e9e9e",
                }}
              >
                <div className="th-notification-header">
                  <span
                    className="th-notification-mode"
                    style={{
                      backgroundColor: MODE_COLORS[notif.mode] || "#9e9e9e",
                    }}
                  >
                    {notif.mode.toUpperCase()}
                  </span>
                  <span className="th-notification-time">
                    {notif.start_time} - {notif.end_time}
                  </span>
                </div>
                <div className="th-notification-details">
                  <span className="th-notification-power">
                    ⚡ {notif.power_kw} kWh
                  </span>
                  <span className="th-notification-cost">
                    💰 ${notif.cost.toFixed(2)}
                  </span>
                </div>
                <div className="th-notification-reason">{notif.reason}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="th-notifications-empty">
            {error ? (
              <span className="th-error">{error}</span>
            ) : (
              <span>
                No scheduled HVAC actions. System maintaining comfort
                automatically.
              </span>
            )}
          </div>
        )}
      </div>

      {/* AI Status Indicator */}
      <div className="th-ai-status">
        <div className="th-ai-indicator"></div>
        <span>AI Optimizing for cost savings & comfort</span>
      </div>
    </div>
  );
}
