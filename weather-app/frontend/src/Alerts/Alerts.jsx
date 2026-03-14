import React, { useState, useEffect, useRef, useCallback } from "react";
import "./alerts.css";

const API_BASE = "http://localhost:8000/api";

// How many seconds the Refresh button is disabled after a successful call.
// Mirrors the backend's FORCE_REFRESH_COOLDOWN_MINUTES (5 min = 300 s).
const REFRESH_COOLDOWN_SECONDS = 300;

export default function Alerts({ username }) {
  const [alertsData, setAlertsData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Seconds remaining on the refresh cooldown (0 = button is enabled)
  const [cooldown, setCooldown] = useState(0);

  // Prevents duplicate in-flight requests
  const isFetchingRef = useRef(false);
  // Lets us cancel an in-flight fetch when the component unmounts
  const abortCtrlRef = useRef(null);
  // Interval handle for the cooldown timer
  const cooldownTimerRef = useRef(null);

  // ── Cooldown ticker ────────────────────────────────────────────────
  const startCooldown = useCallback(() => {
    setCooldown(REFRESH_COOLDOWN_SECONDS);
    clearInterval(cooldownTimerRef.current);
    cooldownTimerRef.current = setInterval(() => {
      setCooldown((prev) => {
        if (prev <= 1) {
          clearInterval(cooldownTimerRef.current);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  }, []);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      abortCtrlRef.current?.abort();
      clearInterval(cooldownTimerRef.current);
    };
  }, []);

  // ── Core fetch ─────────────────────────────────────────────────────
  const fetchAlerts = useCallback(
    async (refresh = false) => {
      if (!username) {
        setError("Please log in to see appliance alerts");
        return;
      }

      // Guard: don't fire a second request while one is already running
      if (isFetchingRef.current) return;

      // Guard: don't allow manual refresh while cooldown is active
      if (refresh && cooldown > 0) return;

      // Cancel any previous in-flight request
      abortCtrlRef.current?.abort();
      abortCtrlRef.current = new AbortController();

      isFetchingRef.current = true;
      setLoading(true);
      setError(null);

      try {
        const endpoint = refresh
          ? `${API_BASE}/alerts/${username}/refresh`
          : `${API_BASE}/alerts/${username}`;

        const response = await fetch(endpoint, {
          method: refresh ? "POST" : "GET",
          signal: abortCtrlRef.current.signal,
        });

        if (!response.ok) {
          const errorData = await response.json();
          throw new Error(errorData.detail || "Failed to fetch alerts");
        }

        const data = await response.json();
        setAlertsData(data);

        // Start cooldown after any successful API round-trip
        if (refresh) startCooldown();
      } catch (err) {
        if (err.name === "AbortError") return; // Silently ignore cancelled requests
        console.error("Alerts fetch error:", err);
        setError(err.message);
      } finally {
        isFetchingRef.current = false;
        setLoading(false);
      }
    },
    [username, cooldown, startCooldown],
  );

  // Fetch on mount / username change (read-only — no cooldown applied)
  useEffect(() => {
    if (username) fetchAlerts(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [username]);

  const handleRefresh = () => fetchAlerts(true);

  // ── Alert icon helper ───────────────────────────────────────────────
  const getAlertIcon = (type) => {
    switch (type) {
      case "warning":
        return "⚠️";
      case "success":
        return "✅";
      case "info":
        return "ℹ️";
      default:
        return "📢";
    }
  };

  // ── Refresh button label ────────────────────────────────────────────
  const refreshLabel = () => {
    if (loading) return "⏳ Loading…";
    if (cooldown > 0) {
      const m = Math.floor(cooldown / 60);
      const s = cooldown % 60;
      return `⏳ ${m}:${String(s).padStart(2, "0")}`;
    }
    return "🔄 Refresh";
  };

  // ── Render states ───────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="alert-card">
        <div className="alert-header">
          <div className="alert-header-left">
            <span className="alert-icon">⚡</span>
            <span className="alert-title">Smart Appliance Alerts</span>
          </div>
        </div>
        <div className="alert-loading">
          <p>🔄 Analyzing your energy usage…</p>
          <p style={{ fontSize: "12px", marginTop: "8px" }}>
            Our AI is optimizing your appliance schedules
          </p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="alert-card">
        <div className="alert-header">
          <div className="alert-header-left">
            <span className="alert-icon">⚡</span>
            <span className="alert-title">Smart Appliance Alerts</span>
          </div>
          <button className="refresh-btn" onClick={() => fetchAlerts(false)}>
            Retry
          </button>
        </div>
        <div className="alert-error">
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!username) {
    return (
      <div className="alert-card">
        <div className="alert-header">
          <div className="alert-header-left">
            <span className="alert-icon">⚡</span>
            <span className="alert-title">Smart Appliance Alerts</span>
          </div>
        </div>
        <div className="no-appliances">
          <p>🔐 Please log in to see personalized appliance alerts</p>
        </div>
      </div>
    );
  }

  if (!alertsData) {
    return (
      <div className="alert-card">
        <div className="alert-header">
          <div className="alert-header-left">
            <span className="alert-icon">⚡</span>
            <span className="alert-title">Smart Appliance Alerts</span>
          </div>
          <button className="refresh-btn" onClick={() => fetchAlerts(false)}>
            Generate
          </button>
        </div>
        <div className="no-appliances">
          <p>📊 No alerts generated yet</p>
          <p style={{ fontSize: "12px" }}>
            Click "Generate" to get AI-powered appliance recommendations
          </p>
        </div>
      </div>
    );
  }

  const {
    appliance_schedules,
    alerts,
    generated_date,
    generated_time,
    _cache_note,
  } = alertsData;

  return (
    <div className="alert-card">
      {/* Header */}
      <div className="alert-header">
        <div className="alert-header-left">
          <span className="alert-icon">⚡</span>
          <span className="alert-title">Smart Appliance Alerts</span>
        </div>
        <button
          className="refresh-btn"
          onClick={handleRefresh}
          disabled={loading || cooldown > 0}
          title={cooldown > 0 ? "Refresh cooldown active" : "Refresh alerts"}
        >
          {refreshLabel()}
        </button>
      </div>

      {/* Cooldown / cache note from backend */}
      {_cache_note && (
        <div
          className="alert-item info"
          style={{ margin: "8px 0", fontSize: "12px" }}
        >
          <span className="alert-icon-small">ℹ️</span>
          <span>{_cache_note}</span>
        </div>
      )}

      {/* Appliance Schedules */}
      {appliance_schedules && appliance_schedules.length > 0 && (
        <div className="appliance-schedules">
          <div className="schedule-list">
            {appliance_schedules.map((schedule, index) => (
              <div
                key={index}
                className={`schedule-item ${schedule.priority?.toLowerCase()}-priority`}
              >
                <div className="schedule-header">
                  <span className="appliance-name">{schedule.appliance}</span>
                  <span
                    className={`priority-badge ${schedule.priority?.toLowerCase()}`}
                  >
                    {schedule.priority}
                  </span>
                </div>
                <div className="schedule-time">
                  <span>🕐</span>
                  <span>
                    {schedule.optimal_start_time} – {schedule.optimal_end_time}
                  </span>
                  {schedule.time_label && (
                    <span
                      className={`time-label ${schedule.time_label === "NOW" ? "now" : ""}`}
                    >
                      {schedule.time_label}
                    </span>
                  )}
                  <span style={{ color: "#666" }}>
                    ({schedule.duration_minutes} min)
                  </span>
                </div>
                <div className="schedule-details">
                  <span>⚡ {schedule.power_kw} kW</span>
                  <span>💰 ${schedule.estimated_cost?.toFixed(2)}</span>
                </div>
                {schedule.reason && (
                  <div className="schedule-reason">{schedule.reason}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Alerts List */}
      {alerts && alerts.length > 0 && (
        <div className="alerts-section">
          <div className="alerts-title">
            <span>🔔</span> Alerts &amp; Notifications
          </div>
          <div className="alerts-list">
            {alerts.map((alert, index) => (
              <div key={index} className={`alert-item ${alert.type}`}>
                <span className="alert-icon-small">
                  {getAlertIcon(alert.type)}
                </span>
                <span>{alert.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* No appliances fallback */}
      {(!appliance_schedules || appliance_schedules.length === 0) &&
        !alerts && (
          <div className="no-appliances">
            <p>🏠 No appliances configured</p>
            <p style={{ fontSize: "12px" }}>
              Add appliances in your House Settings to get smart scheduling
              alerts
            </p>
          </div>
        )}

      {/* Timestamp */}
      {generated_date && (
        <div className="generated-time">
          Last updated: {generated_date} {generated_time}
        </div>
      )}
    </div>
  );
}
