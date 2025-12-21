import React, { useState, useEffect } from "react";
import "./alerts.css";

const API_BASE = "http://localhost:8000/api";

export default function Alerts({ username }) {
  const [alertsData, setAlertsData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Fetch alerts on mount and when username changes
  useEffect(() => {
    if (username) {
      fetchAlerts();
    }
  }, [username]);

  const fetchAlerts = async (refresh = false) => {
    if (!username) {
      setError("Please log in to see appliance alerts");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const endpoint = refresh
        ? `${API_BASE}/alerts/${username}/refresh`
        : `${API_BASE}/alerts/${username}`;

      const response = await fetch(endpoint, {
        method: refresh ? "POST" : "GET",
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Failed to fetch alerts");
      }

      const data = await response.json();
      setAlertsData(data);
    } catch (err) {
      console.error("Alerts fetch error:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = () => {
    fetchAlerts(true);
  };

  // Get alert icon based on type
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

  // Render loading state
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
          <p>🔄 Analyzing your energy usage...</p>
          <p style={{ fontSize: "12px", marginTop: "8px" }}>
            Our AI is optimizing your appliance schedules
          </p>
        </div>
      </div>
    );
  }

  // Render error state
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

  // Render no data / no username state
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

  // Render no alerts data yet
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

  const { appliance_schedules, alerts, generated_date, generated_time } =
    alertsData;

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
          disabled={loading}
        >
          🔄 Refresh
        </button>
      </div>

      {/* Daily summary removed per request */}

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
                    {schedule.optimal_start_time} - {schedule.optimal_end_time}
                  </span>
                  {schedule.time_label && (
                    <span
                      className={`time-label ${
                        schedule.time_label === "NOW" ? "now" : ""
                      }`}
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
            <span>🔔</span> Alerts & Notifications
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

      {/* No appliances message */}
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

      {/* Generated timestamp */}
      {generated_date && (
        <div className="generated-time">
          Last updated: {generated_date} {generated_time}
        </div>
      )}
    </div>
  );
}
