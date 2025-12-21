import React, { useState, useEffect, useCallback, useMemo } from "react";
import { Backend } from "../App";
import "./thermostat.css";

// Constants
const POLL_INTERVAL_MS = 5000;
const SCHEDULE_REFRESH_MS = 60000; // Refresh schedule every minute

const HVAC_STATUS_CONFIG = {
  heating: { color: "#ff6b35", icon: "🔥" },
  cooling: { color: "#00bcd4", icon: "❄️" },
  off: { color: "#9e9e9e", icon: "⏸️" },
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
  const [prevInsideTemp, setPrevInsideTemp] = useState(null);
  const [outsideTemp, setOutsideTemp] = useState("--");
  const [hvacStatus, setHvacStatus] = useState("off");
  const [hvacPower, setHvacPower] = useState(0);
  const [hvacReason, setHvacReason] = useState("");
  const [setTemp, setSetTemp] = useState(null); // Start as null to show loading
  const [notifications, setNotifications] = useState([]);
  const [summary, setSummary] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [initialLoadDone, setInitialLoadDone] = useState(false);
  const [currentTime, setCurrentTime] = useState(new Date());
  const [tempHistory, setTempHistory] = useState([]); // Track recent temps for trend

  // Fetch saved setpoint from database on initial load
  const fetchSavedSetpoint = useCallback(async () => {
    if (!username) return;

    try {
      const data = await Backend.getSetpoint(username);
      if (data.target_temp_c && !initialLoadDone) {
        setSetTemp(Math.round(data.target_temp_c));
        console.log(
          `Loaded setpoint from ${data.source}: ${data.target_temp_c}°C`
        );
      }
    } catch (err) {
      console.error("Error fetching saved setpoint:", err);
      // Default to 22 if can't fetch
      if (!initialLoadDone) {
        setSetTemp(22);
      }
    } finally {
      setInitialLoadDone(true);
    }
  }, [username, initialLoadDone]);

  // Fetch simulation data - also receives schedule data when available
  const fetchSimulationData = useCallback(async () => {
    if (!username) return;

    try {
      const data = await Backend.getSimulation(username);

      // Track previous temp for trend detection
      if (typeof insideTemp === "number") {
        setPrevInsideTemp(insideTemp);
      }

      setInsideTemp(data.T_in_new);
      setOutsideTemp(data.T_out);
      setHvacStatus(data.hvac_mode || "off");
      setHvacPower(data.hvac_power_kw || 0);
      setHvacReason(data.reason || "");
      setError(null);

      // Track temperature history for trend (keep last 12 readings = 1 minute)
      setTempHistory((prev) => {
        const newHistory = [...prev, { temp: data.T_in_new, time: Date.now() }];
        return newHistory.slice(-12);
      });

      // Update schedule data if included in simulation response
      if (data.notifications && data.notifications.length > 0) {
        setNotifications(data.notifications);
      }
      if (data.summary) {
        setSummary(data.summary);
        // Update target temp from backend if we haven't set one yet
        if (setTemp === null && data.summary.target_temp_c) {
          setSetTemp(Math.round(data.summary.target_temp_c));
        }
      }
    } catch (err) {
      console.error("Error fetching simulation data:", err);
      setError("Unable to fetch temperature data");
    }
  }, [username, setTemp, insideTemp]);

  // Fetch HVAC schedule (initial load - don't pass setTemp to get saved value)
  const fetchHVACSchedule = useCallback(
    async (explicitTemp = null) => {
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
    },
    [username]
  );

  // Poll simulation data
  useEffect(() => {
    fetchSimulationData();
    const interval = setInterval(fetchSimulationData, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchSimulationData]);

  // Fetch saved setpoint on mount (BEFORE schedule)
  useEffect(() => {
    fetchSavedSetpoint();
  }, [fetchSavedSetpoint]);

  // Fetch schedule on mount only (will load saved setpoint)
  useEffect(() => {
    if (initialLoadDone) {
      fetchHVACSchedule(null); // null = use saved value from backend
    }
  }, [fetchHVACSchedule, initialLoadDone]);

  // Periodic refresh of schedule (keeps using saved value)
  useEffect(() => {
    const interval = setInterval(
      () => fetchHVACSchedule(null),
      SCHEDULE_REFRESH_MS
    );
    return () => clearInterval(interval);
  }, [fetchHVACSchedule]);

  // Real-time clock update
  useEffect(() => {
    const clockInterval = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(clockInterval);
  }, []);

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

  // Memoized date/time values - updates every second now
  const { dayOfWeek, timeDisplay } = useMemo(() => {
    return {
      dayOfWeek: currentTime.toLocaleDateString([], { weekday: "short" }),
      timeDisplay: currentTime.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
    };
  }, [currentTime]);

  // Calculate temperature trend from history
  const tempTrend = useMemo(() => {
    if (tempHistory.length < 2) return { direction: "stable", rate: 0 };

    const recent = tempHistory.slice(-6); // Last 30 seconds
    if (recent.length < 2) return { direction: "stable", rate: 0 };

    const firstTemp = recent[0].temp;
    const lastTemp = recent[recent.length - 1].temp;
    const change = lastTemp - firstTemp;
    const timeSpanMinutes =
      (recent[recent.length - 1].time - recent[0].time) / 60000;

    // Rate per minute
    const rate = timeSpanMinutes > 0 ? change / timeSpanMinutes : 0;

    if (Math.abs(change) < 0.05) return { direction: "stable", rate: 0 };
    return {
      direction: change > 0 ? "rising" : "falling",
      rate: Math.abs(rate).toFixed(2),
    };
  }, [tempHistory]);

  // Temperature adjustment handlers
  const decreaseTemp = useCallback(
    () => setTemp !== null && handleTempChange(setTemp - 1),
    [setTemp, handleTempChange]
  );
  const increaseTemp = useCallback(
    () => setTemp !== null && handleTempChange(setTemp + 1),
    [setTemp, handleTempChange]
  );

  // Get status display config
  const statusConfig = HVAC_STATUS_CONFIG[hvacStatus] || HVAC_STATUS_CONFIG.off;

  // Generate detailed HVAC status message
  const getDetailedStatus = useMemo(() => {
    const targetTemp = setTemp || summary?.target_temp_c || 22;
    const currentTemp = typeof insideTemp === "number" ? insideTemp : null;
    const outdoor = typeof outsideTemp === "number" ? outsideTemp : null;

    if (currentTemp === null) {
      return { text: "Initializing...", subtext: "", trend: null };
    }

    const tempDiff = targetTemp - currentTemp;
    const tempDiffAbs = Math.abs(tempDiff);

    // Get trend arrow
    const trendArrow =
      tempTrend.direction === "rising"
        ? "↑"
        : tempTrend.direction === "falling"
        ? "↓"
        : "→";
    const trendText =
      tempTrend.direction !== "stable"
        ? `${trendArrow} ${tempTrend.rate}°C/min`
        : "→ Stable";

    if (hvacStatus === "heating") {
      if (tempDiffAbs > 2) {
        return {
          text: `${statusConfig.icon} Heating to ${targetTemp}°C`,
          subtext: `${
            hvacPower > 0 ? `Using ${hvacPower.toFixed(1)} kW` : "Warming up"
          } • ${trendText}`,
          trend: tempTrend.direction,
        };
      } else if (tempDiffAbs > 0.5) {
        return {
          text: `${statusConfig.icon} Almost at target`,
          subtext: `${tempDiff.toFixed(1)}°C to go • ${trendText}`,
          trend: tempTrend.direction,
        };
      } else {
        return {
          text: `${statusConfig.icon} Fine-tuning temperature`,
          subtext: `Approaching setpoint • ${trendText}`,
          trend: tempTrend.direction,
        };
      }
    }

    if (hvacStatus === "cooling") {
      if (tempDiffAbs > 2) {
        return {
          text: `${statusConfig.icon} Cooling to ${targetTemp}°C`,
          subtext: `${
            hvacPower > 0 ? `Using ${hvacPower.toFixed(1)} kW` : "Cooling down"
          } • ${trendText}`,
          trend: tempTrend.direction,
        };
      } else if (tempDiffAbs > 0.5) {
        return {
          text: `${statusConfig.icon} Almost at target`,
          subtext: `${Math.abs(tempDiff).toFixed(1)}°C to go • ${trendText}`,
          trend: tempTrend.direction,
        };
      } else {
        return {
          text: `${statusConfig.icon} Fine-tuning temperature`,
          subtext: `Approaching setpoint • ${trendText}`,
          trend: tempTrend.direction,
        };
      }
    }

    // HVAC is off
    if (tempDiffAbs <= 0.5) {
      return {
        text: "✓ At target temperature",
        subtext: `Comfort maintained • ${trendText}`,
        trend: tempTrend.direction,
      };
    } else if (outdoor !== null) {
      // Show what's happening naturally based on weather
      if (outdoor > currentTemp && tempDiff < 0) {
        return {
          text: "⏸️ Standby - Natural cooling",
          subtext: `Outside: ${outdoor.toFixed(1)}°C • ${trendText}`,
          trend: tempTrend.direction,
        };
      } else if (outdoor < currentTemp && tempDiff > 0) {
        return {
          text: "⏸️ Standby - Natural warming",
          subtext: `Outside: ${outdoor.toFixed(1)}°C • ${trendText}`,
          trend: tempTrend.direction,
        };
      } else if (tempDiff > 1) {
        return {
          text: "⏸️ Heating will start soon",
          subtext: `Need +${tempDiff.toFixed(1)}°C • ${trendText}`,
          trend: tempTrend.direction,
        };
      } else if (tempDiff < -1) {
        return {
          text: "⏸️ Cooling will start soon",
          subtext: `Need ${tempDiff.toFixed(1)}°C • ${trendText}`,
          trend: tempTrend.direction,
        };
      }
    }

    return {
      text: "⏸️ System idle",
      subtext: `Target: ${targetTemp}°C • ${trendText}`,
      trend: tempTrend.direction,
    };
  }, [
    hvacStatus,
    insideTemp,
    outsideTemp,
    setTemp,
    summary,
    hvacPower,
    statusConfig.icon,
    tempTrend,
  ]);

  return (
    <div className="th-container">
      {/* Header */}
      <div className="th-top-row">
        <span>{dayOfWeek}</span>
        <span className="th-time">{timeDisplay}</span>
      </div>

      {/* Main Temperature Display */}
      <div className="th-main">
        <div className="th-section">
          <div className="th-label">Inside</div>
          <div className="th-temp-container">
            <div
              className={`th-temp ${
                getDetailedStatus.trend === "rising"
                  ? "temp-rising"
                  : getDetailedStatus.trend === "falling"
                  ? "temp-falling"
                  : ""
              }`}
            >
              {typeof insideTemp === "number"
                ? insideTemp.toFixed(1)
                : insideTemp}
              °C
            </div>
            {getDetailedStatus.trend &&
              getDetailedStatus.trend !== "stable" && (
                <span
                  className={`th-trend-indicator ${getDetailedStatus.trend}`}
                >
                  {getDetailedStatus.trend === "rising" ? "▲" : "▼"}
                </span>
              )}
          </div>
          <div className="th-status-text" style={{ color: statusConfig.color }}>
            {getDetailedStatus.text}
          </div>
          {getDetailedStatus.subtext && (
            <div className="th-status-subtext">{getDetailedStatus.subtext}</div>
          )}
        </div>

        <div className="th-divider" />

        <div className="th-section">
          <div className="th-label">Set to</div>
          <div className="th-temp set">
            {setTemp !== null ? `${setTemp}°C` : "..."}
          </div>
          <div className="th-buttons">
            <button
              className="th-circle"
              onClick={decreaseTemp}
              aria-label="Decrease temperature"
              disabled={setTemp === null}
            >
              –
            </button>
            <button
              className="th-circle"
              onClick={increaseTemp}
              aria-label="Increase temperature"
              disabled={setTemp === null}
            >
              +
            </button>
          </div>
        </div>
      </div>

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
                className={`th-notification ${
                  notif.hours_away === 0 ? "th-notification-now" : ""
                }`}
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
                  <span className="th-notification-time-label">
                    {notif.time_label ||
                      (notif.hours_away === 0
                        ? "Now"
                        : `In ${notif.hours_away}h`)}
                  </span>
                  <span className="th-notification-time">
                    {notif.start_time} - {notif.end_time}
                  </span>
                </div>
                <div className="th-notification-details">
                  <span className="th-notification-power">
                    ⚡ {notif.power_kw} kW
                  </span>
                  <span className="th-notification-cost">
                    💰 $
                    {typeof notif.cost === "number"
                      ? notif.cost.toFixed(2)
                      : notif.cost}
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
            ) : summary ? (
              <span>
                ✓ HVAC system maintaining comfort at {summary.target_temp_c}°C
              </span>
            ) : (
              <span>🔄 Generating AI-optimized schedule...</span>
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
