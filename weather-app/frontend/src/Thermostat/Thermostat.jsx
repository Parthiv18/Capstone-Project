import React, { useState, useEffect, useCallback, useMemo } from "react";
import { Backend } from "../App";
import "./thermostat.css";

// Constants
const POLL_INTERVAL_MS = 30000; // Poll every 30 seconds (reduced from 5s)
const SCHEDULE_REFRESH_MS = 300000; // Refresh HVAC schedule every 5 min (reduced API calls)
const MAX_ERROR_STREAK = 3; // Skip Genai calls after 3 consecutive failures
const ERROR_BACKOFF_MS = 60000; // Wait 1 minute before retrying after error

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
  const [aiSetpoint, setAiSetpoint] = useState(null); // AI setpoint metadata

  // Error tracking for retry logic
  const [scheduleErrorStreak, setScheduleErrorStreak] = useState(0);
  const [lastScheduleErrorTime, setLastScheduleErrorTime] = useState(0);
  const [shouldSkipScheduleFetch, setShouldSkipScheduleFetch] = useState(false);

  // Fetch saved setpoint from database on initial load
  const fetchSavedSetpoint = useCallback(async () => {
    if (!username) return;

    try {
      const data = await Backend.getSetpoint(username);
      if (data.target_temp_c && !initialLoadDone) {
        setSetTemp(Math.round(data.target_temp_c));
        console.log(
          `Loaded setpoint from ${data.source}: ${data.target_temp_c}°C`,
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
      // Capture AI setpoint metadata from simulation step response
      if (data.ai_setpoint) {
        setAiSetpoint(data.ai_setpoint);
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

      // Check if we should skip due to recent errors
      if (shouldSkipScheduleFetch) {
        const timeSinceError = Date.now() - lastScheduleErrorTime;
        if (timeSinceError < ERROR_BACKOFF_MS) {
          // Still in backoff period - skip this call
          return;
        } else {
          // Backoff period expired, allow retry
          setShouldSkipScheduleFetch(false);
          setScheduleErrorStreak(0);
        }
      }

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

        // Capture AI setpoint from schedule response too
        if (data.ai_setpoint) {
          setAiSetpoint(data.ai_setpoint);
        }

        setError(null);
        // Reset error streak on success
        setScheduleErrorStreak(0);
      } catch (err) {
        // Increment error streak
        const newStreak = scheduleErrorStreak + 1;
        setScheduleErrorStreak(newStreak);

        // Only log error when error streak resets (every MAX_ERROR_STREAK attempts)
        if (newStreak % MAX_ERROR_STREAK === 0) {
          console.error(
            `HVAC Schedule Error (attempt ${newStreak}):`,
            err.message,
          );
        }

        // Activate backoff after MAX_ERROR_STREAK consecutive failures
        if (newStreak >= MAX_ERROR_STREAK) {
          setShouldSkipScheduleFetch(true);
          setLastScheduleErrorTime(Date.now());
          console.warn(
            `HVAC API quota exceeded or unavailable. Pausing requests for ${ERROR_BACKOFF_MS / 1000}s`,
          );
        }
      } finally {
        setIsLoading(false);
      }
    },
    [
      username,
      shouldSkipScheduleFetch,
      lastScheduleErrorTime,
      scheduleErrorStreak,
    ],
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
      SCHEDULE_REFRESH_MS,
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

      // Skip refresh if in error backoff period
      if (shouldSkipScheduleFetch) {
        const timeSinceError = Date.now() - lastScheduleErrorTime;
        if (timeSinceError < ERROR_BACKOFF_MS) {
          console.warn("API temporarily unavailable. Using cached schedule.");
          return;
        } else {
          setShouldSkipScheduleFetch(false);
          setScheduleErrorStreak(0);
        }
      }

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
          setScheduleErrorStreak(0); // Reset on success
        } catch (err) {
          console.error("Error refreshing schedule:", err.message);
          const newStreak = scheduleErrorStreak + 1;
          setScheduleErrorStreak(newStreak);
          if (newStreak >= MAX_ERROR_STREAK) {
            setShouldSkipScheduleFetch(true);
            setLastScheduleErrorTime(Date.now());
          }
        }
      }, 500);
    },
    [
      username,
      shouldSkipScheduleFetch,
      lastScheduleErrorTime,
      scheduleErrorStreak,
    ],
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

    // Use the full history window (up to 12 readings = 6 min at 30s poll)
    // for a stable signal.  Short windows show noise; longer shows real trend.
    const recent =
      tempHistory.length >= 6
        ? tempHistory.slice(-12) // last 6 min
        : tempHistory;

    if (recent.length < 2) return { direction: "stable", rate: 0 };

    const firstTemp = recent[0].temp;
    const lastTemp = recent[recent.length - 1].temp;
    const change = lastTemp - firstTemp;
    const timeSpanMinutes =
      (recent[recent.length - 1].time - recent[0].time) / 60000;

    const rate = timeSpanMinutes > 0 ? change / timeSpanMinutes : 0;

    // Threshold: 0.01°C over the window is enough to call it moving.
    // (RC physics at 30s poll gives ~0.013°C/poll = 0.078°C over 6 readings)
    if (Math.abs(change) < 0.01) return { direction: "stable", rate: 0 };
    return {
      direction: change > 0 ? "rising" : "falling",
      rate: Math.abs(rate).toFixed(3),
    };
  }, [tempHistory]);

  // Temperature adjustment handlers
  const decreaseTemp = useCallback(
    () => setTemp !== null && handleTempChange(setTemp - 1),
    [setTemp, handleTempChange],
  );
  const increaseTemp = useCallback(
    () => setTemp !== null && handleTempChange(setTemp + 1),
    [setTemp, handleTempChange],
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

    // Trend label — RC physics moves slowly so show °C/hr for readability
    const trendArrow =
      tempTrend.direction === "rising"
        ? "↑"
        : tempTrend.direction === "falling"
          ? "↓"
          : "→";
    const ratePerHr = (parseFloat(tempTrend.rate) * 60).toFixed(1);
    const trendText =
      tempTrend.direction !== "stable"
        ? `${trendArrow} ${ratePerHr}°C/hr`
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

  // ── AI setpoint label shown below the schedule list ──────────────────────
  // Always rendered when we have any data — shows active AI target when AI
  // mode is on, or a "Smart suggestion" from the physics optimiser otherwise.
  // Now includes comfort band data and drift calculation.
  const aiSetpointLabel = useMemo(() => {
    if (!aiSetpoint) return null;

    // Manual override trumps everything
    if (aiSetpoint.manual_override) {
      const until = aiSetpoint.manual_override_until
        ? new Date(aiSetpoint.manual_override_until).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })
        : null;
      return {
        icon: "✋",
        text: `Manual override: ${aiSetpoint.manual_override_c}°C${until ? ` until ${until}` : ""}`,
        subtext: "AI resumes after override expires",
        comfortCenter: null,
        comfortBand: null,
        drift: null,
      };
    }

    // AI mode fully active
    if (aiSetpoint.enabled && aiSetpoint.setpoint_c != null) {
      const src = aiSetpoint.source === "genai" ? "AI" : "Smart";
      const pre = aiSetpoint.pre_conditioning;
      const comfortCenter = aiSetpoint.comfort_center_c;
      const comfortTolerance = aiSetpoint.comfort_tolerance_c;
      const bandMin = aiSetpoint.comfort_band_min_c;
      const bandMax = aiSetpoint.comfort_band_max_c;
      const drift = aiSetpoint.drift_from_comfort_c;
      const driftReason = aiSetpoint.drift_reason;

      const driftText = drift
        ? ` (drift: ${drift > 0 ? "+" : ""}${drift}°C)`
        : "";

      return {
        icon: pre ? "⚡" : "🧠",
        text: `${src} target: ${aiSetpoint.setpoint_c}°C${driftText}${pre ? " · pre-conditioning" : ""}`,
        subtext: aiSetpoint.strategy || null,
        comfortCenter,
        comfortBand: bandMin && bandMax ? [bandMin, bandMax] : null,
        drift,
        driftReason,
      };
    }

    // Manual mode — show physics suggestion with comfort context
    if (aiSetpoint.suggested_setpoint_c != null) {
      const sp = aiSetpoint.suggested_setpoint_c;
      const pre = aiSetpoint.suggested_pre_conditioning;
      const comfortCenter = aiSetpoint.comfort_center_c;
      const comfortTolerance = aiSetpoint.comfort_tolerance_c;
      const bandMin = aiSetpoint.comfort_band_min_c;
      const bandMax = aiSetpoint.comfort_band_max_c;
      const drift = aiSetpoint.drift_from_comfort_c;
      const driftReason = aiSetpoint.drift_reason;

      const driftText = drift
        ? ` (drift: ${drift > 0 ? "+" : ""}${drift}°C from your ${comfortCenter}°C comfort)`
        : "";

      return {
        icon: pre ? "⚡" : "🧠",
        text: `Smart suggestion: ${sp}°C${driftText}${pre ? " · pre-condition now" : ""}`,
        subtext: aiSetpoint.suggested_strategy || null,
        comfortCenter,
        comfortBand: bandMin && bandMax ? [bandMin, bandMax] : null,
        drift,
        driftReason,
      };
    }

    return null;
  }, [aiSetpoint]);

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
            {notifications.map((notif, index) => {
              // ── Active-window detection ──────────────────────────────────
              // Backend sends is_active:true + minutes_away:0 for the running
              // window. Fall back to the old hours_away===0 shape just in case.
              const isNow =
                notif.is_active === true || notif.minutes_away === 0;
              const timeLabel =
                notif.time_label || (isNow ? "Now" : `In ${notif.hours_away}h`);

              // Parse "X min left" from the backend message string
              const minsLeftMatch =
                isNow && notif.message
                  ? notif.message.match(/(\d+) min left/)
                  : null;

              return (
                <div
                  key={index}
                  className={`th-notification ${isNow ? "th-notification-now" : ""}`}
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
                      {timeLabel}
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
                    {/* "X min left" badge — only on the active window */}
                    {minsLeftMatch && (
                      <span className="th-notification-timeleft">
                        {minsLeftMatch[1]} min left
                      </span>
                    )}
                  </div>

                  <div className="th-notification-reason">{notif.reason}</div>
                </div>
              );
            })}
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

        {/* AI setpoint row — only shown when AI mode is active */}
        {aiSetpointLabel && (
          <div className="th-ai-setpoint-row">
            <span className="th-ai-setpoint-icon">{aiSetpointLabel.icon}</span>
            <div className="th-ai-setpoint-body">
              <span className="th-ai-setpoint-text">
                {aiSetpointLabel.text}
              </span>
              {aiSetpointLabel.subtext && (
                <span className="th-ai-setpoint-sub">
                  {aiSetpointLabel.subtext}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Comfort Band Visualization — shows user preference vs AI suggestion */}
        {aiSetpointLabel &&
          aiSetpointLabel.comfortBand &&
          aiSetpointLabel.comfortCenter && (
            <div className="th-comfort-band">
              <div className="th-comfort-band-label">
                Your comfort: {aiSetpointLabel.comfortCenter}°C (±
                {aiSetpointLabel.comfortCenter && aiSetpointLabel.comfortBand
                  ? (
                      aiSetpointLabel.comfortBand[1] -
                      aiSetpointLabel.comfortCenter
                    ).toFixed(1)
                  : "2.0"}
                °C)
              </div>

              {/* Visual comfort band bar */}
              <div className="th-comfort-band-container">
                <div className="th-comfort-band-bg">
                  <div
                    className="th-comfort-band-bar"
                    style={{
                      left: `${((aiSetpointLabel.comfortBand[0] - 10) / 20) * 100}%`,
                      right: `${100 - ((aiSetpointLabel.comfortBand[1] - 10) / 20) * 100}%`,
                    }}
                  ></div>

                  {/* AI suggestion marker */}
                  {aiSetpointLabel.drift !== null && (
                    <div
                      className="th-comfort-band-marker"
                      style={{
                        left: `${((aiSetpointLabel.comfortCenter + aiSetpointLabel.drift - 10) / 20) * 100}%`,
                        backgroundColor:
                          Math.abs(aiSetpointLabel.drift) <=
                          aiSetpointLabel.comfortBand[1] -
                            aiSetpointLabel.comfortCenter
                            ? "#2196F3"
                            : "#FF9800",
                      }}
                    >
                      <span className="th-comfort-band-marker-label">
                        AI:{" "}
                        {(
                          aiSetpointLabel.comfortCenter + aiSetpointLabel.drift
                        ).toFixed(1)}
                        °C
                      </span>
                    </div>
                  )}
                </div>

                {/* Scale labels */}
                <div className="th-comfort-band-scale">
                  <span className="th-comfort-band-scale-label">10°C</span>
                  <span className="th-comfort-band-scale-label">20°C</span>
                  <span className="th-comfort-band-scale-label">30°C</span>
                </div>
              </div>

              {/* Drift explanation */}
              {aiSetpointLabel.driftReason && (
                <div className="th-comfort-band-reason">
                  {aiSetpointLabel.driftReason}
                </div>
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
