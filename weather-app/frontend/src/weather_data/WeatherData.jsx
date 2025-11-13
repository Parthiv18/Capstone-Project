import React, { useEffect, useState, useMemo, useRef } from "react";
import "./weather.css";

function formatHour(dateStr) {
  try {
    const d = new Date(dateStr);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch (_) {
    return dateStr;
  }
}

function yyyyMMdd(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(
    2,
    "0"
  )}-${String(date.getDate()).padStart(2, "0")}`;
}

export default function WeatherData({ username, loggedIn }) {
  const API_BASE = "http://localhost:8000";

  // Weather control state (moved from App)
  const [postal, setPostal] = useState("");
  const [activeLat, setActiveLat] = useState(null);
  const [activeLon, setActiveLon] = useState(null);
  const [serverData, setServerData] = useState(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState(null);

  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);
  const [cursorIndex, setCursorIndex] = useState(null);
  const [selectedDay, setSelectedDay] = useState(null); // yyyy-MM-dd string

  const svgRef = useRef(null);

  // initialize postal from localStorage if available
  useEffect(() => {
    try {
      const s = localStorage.getItem("weather_user");
      if (s) {
        const parsed = JSON.parse(s);
        if (parsed && parsed.postalcode) setPostal(parsed.postalcode);
      }
    } catch (_) {}
  }, []);

  // When logged in and postal is set, resolve postal -> serverData (lat/lon + rows)
  useEffect(() => {
    let cancelled = false;
    async function fetchByPostal() {
      if (!loggedIn) return;
      if (!postal) return;
      setFetching(true);
      setFetchError(null);
      setServerData(null);
      try {
        // Try to load stored weather from user's DB first
        if (username) {
          try {
            const userRes = await fetch(
              `${API_BASE}/user/weather?username=${encodeURIComponent(
                username
              )}`
            );
            if (userRes.ok) {
              const userJson = await userRes.json();
              if (userJson && userJson.text) {
                try {
                  const parsed = JSON.parse(userJson.text);
                  if (cancelled) return;
                  if (parsed.lat) setActiveLat(Number(parsed.lat));
                  if (parsed.lon) setActiveLon(Number(parsed.lon));
                  setServerData(parsed);
                  return; // used stored user weather
                } catch (e) {
                  // fall through to fresh fetch
                }
              }
            }
          } catch (e) {
            // ignore and fall back
          }
        }

        const res = await fetch(
          `${API_BASE}/weather_postal?postal=${encodeURIComponent(
            postal.replace(/\s+/g, "")
          )}`
        );
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(txt || res.statusText);
        }
        const json = await res.json();
        if (cancelled) return;
        if (json.lat) setActiveLat(Number(json.lat));
        if (json.lon) setActiveLon(Number(json.lon));
        setServerData(json);

        // Persist to user's DB for later use
        try {
          if (username) {
            fetch(`${API_BASE}/user/weather`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ username, text: JSON.stringify(json) }),
            }).catch(() => {});
          }
        } catch (_) {}
      } catch (e) {
        if (!cancelled) setFetchError(e.message || String(e));
      } finally {
        if (!cancelled) setFetching(false);
      }
    }
    fetchByPostal();
    return () => {
      cancelled = true;
    };
  }, [loggedIn, postal, username]);

  // load detailed weather when we have coords or serverData
  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        let data;
        if (serverData) {
          data = serverData;
        } else {
          // require valid coords
          if (activeLat == null || activeLon == null) {
            throw new Error("No coordinates available to fetch weather");
          }
          const res = await fetch(`${API_BASE}/weather`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              lat: Number(activeLat),
              lon: Number(activeLon),
            }),
          });
          if (!res.ok) throw new Error(await res.text());
          data = await res.json();
        }

        if (!cancelled) {
          const r = data.rows || [];
          setRows(r);
          const now = r.length ? new Date(r[0].date) : new Date();
          const today = new Date(now);
          today.setHours(0, 0, 0, 0);
          setSelectedDay(yyyyMMdd(today));
        }
      } catch (err) {
        setError(err.message || String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [activeLat, activeLon, serverData]);

  // Current hour row (closest to now)
  const now = Date.now();
  const currentHourRow = useMemo(() => {
    if (!rows.length) return null;
    return rows.reduce((closest, r) => {
      const t = new Date(r.date).getTime();
      return Math.abs(t - now) <
        Math.abs(new Date(closest.date).getTime() - now)
        ? r
        : closest;
    }, rows[0]);
  }, [rows, now]);

  // Build 7-day selector (today + next 6 days)
  const next7Days = useMemo(() => {
    const days = [];
    const nowLocal = rows.length ? new Date(rows[0].date) : new Date();
    const start = new Date(nowLocal);
    start.setHours(0, 0, 0, 0); // today at midnight
    for (let i = 0; i < 7; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      days.push({
        label: d.toLocaleDateString([], { weekday: "short" }),
        iso: yyyyMMdd(d),
        dateObj: d,
      });
    }
    return days;
  }, [rows]);

  // Filter rows for the selected day and hours 0..23 (inclusive)
  const dayRows = useMemo(() => {
    if (!selectedDay || !rows.length) return [];
    return rows
      .map((r) => ({ ...r, __d: new Date(r.date) }))
      .filter((r) => {
        const dstr = yyyyMMdd(r.__d);
        const h = r.__d.getHours();
        return dstr === selectedDay && h >= 0 && h <= 23;
      })
      .sort((a, b) => a.__d - b.__d);
  }, [rows, selectedDay]);

  // temps/min/max for selected day's chart
  const temps = useMemo(() => dayRows.map((r) => r.temperature_2m), [dayRows]);
  const minT = useMemo(() => (temps.length ? Math.min(...temps) : 0), [temps]);
  const maxT = useMemo(() => (temps.length ? Math.max(...temps) : 30), [temps]);

  // coords generator uses the day's rows (not the full rows)
  function coordsForIndex(i, width, height, padding) {
    const count = Math.max(1, dayRows.length - 1);
    const xStep = (width - padding * 2) / count;
    const x = padding + i * xStep;
    const t = dayRows[i].temperature_2m;
    const frac =
      dayRows.length === 0 ? 0 : (t - minT) / Math.max(0.0001, maxT - minT);
    const y = padding + (1 - frac) * (height - padding * 2);
    return [x, y];
  }

  function handlePointerMove(e) {
    const svg = svgRef.current;
    if (!svg || dayRows.length === 0) return;
    const rect = svg.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const width = rect.width;
    const padding = 30;
    const xStep = (width - padding * 2) / Math.max(1, dayRows.length - 1);
    let idx = Math.round((x - padding) / xStep);
    idx = Math.max(0, Math.min(dayRows.length - 1, idx));
    setCursorIndex(idx);
  }

  function handlePointerLeave() {
    setCursorIndex(null);
  }
  // If there is no server data and no coords, show a simple loading placeholder.
  if (!serverData && (activeLat == null || activeLon == null)) {
    return <div className="wd-container wd-card">Loading Data...</div>;
  }

  if (loading)
    return <div className="wd-container wd-card">Loading weather…</div>;
  if (error)
    return <div className="wd-container wd-card wd-error">Error: {error}</div>;

  return (
    <div className="wd-container wd-card">
      <div className="wd-card-header" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <h3 className="wd-title">
            Hourly temperature{postal ? ` for ${postal}` : ""}
          </h3>

          {/* 7-day selector (today + next 6 days) */}
          <div
            style={{
              display: "flex",
              gap: 8,
              marginBottom: 12,
              flexWrap: "wrap",
            }}
          >
            {next7Days.map((d) => {
              const active = selectedDay === d.iso;
              return (
                <button
                  key={d.iso}
                  onClick={() => {
                    setSelectedDay(d.iso);
                    setCursorIndex(null);
                  }}
                  className={`wd-day-btn ${active ? "wd-day-btn-active" : ""}`}
                  style={{
                    padding: "6px 10px",
                    borderRadius: 8,
                    border: active ? "1px solid #ff7a18" : "1px solid #eee",
                    background: active ? "#fff8f2" : "#fff",
                    cursor: "pointer",
                    minWidth: 64,
                    textAlign: "center",
                  }}
                >
                  <div style={{ fontSize: 12 }}>{d.label}</div>
                  <div style={{ fontSize: 11, color: "#555" }}>
                    {d.dateObj.getMonth() + 1}/{d.dateObj.getDate()}
                  </div>
                </button>
              );
            })}
          </div>

          {/* top metrics (kept from your original) */}
          <div className="wd-metrics">
            {currentHourRow && (
              <>
                <div className="wd-metric">
                  <div className="wd-metric-label">Rain</div>
                  <div className="wd-metric-value">
                    {currentHourRow.rain > 0
                      ? `${currentHourRow.rain.toFixed(1)} mm`
                      : "0 mm"}
                  </div>
                </div>
                <div className="wd-metric">
                  <div className="wd-metric-label">Snow</div>
                  <div className="wd-metric-value">
                    {currentHourRow.snowfall > 0
                      ? `${currentHourRow.snowfall.toFixed(1)} mm`
                      : "0 mm"}
                  </div>
                </div>
                <div className="wd-metric">
                  <div className="wd-metric-label">Wind</div>
                  <div className="wd-metric-value">
                    {currentHourRow.windspeed_10m.toFixed(1)} m/s
                  </div>
                </div>
                <div className="wd-metric">
                  <div className="wd-metric-label">Humidity</div>
                  <div className="wd-metric-value">
                    {currentHourRow.humidity_2m.toFixed(0)}%
                  </div>
                </div>
                <div className="wd-metric">
                  <div className="wd-metric-label">Solar</div>
                  <div className="wd-metric-value">
                    {currentHourRow.solar_radiation.toFixed(0)} W/m²
                  </div>
                </div>
                <div className="wd-metric">
                  <div className="wd-metric-label">Apparent Temperature</div>
                  <div className="wd-metric-value">
                    {currentHourRow.apparent_temperature.toFixed(0)}°C
                  </div>
                </div>
                <div className="wd-metric">
                  <div className="wd-metric-label">Dew Point</div>
                  <div className="wd-metric-value">
                    {currentHourRow.dew_point_2m.toFixed(0)}°C
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        <div className="wd-current">
          <div className="wd-current-label">Now</div>
          <div className="wd-current-temp">
            {currentHourRow
              ? `${currentHourRow.temperature_2m.toFixed(0)}°C`
              : "—"}
          </div>
        </div>
      </div>

      {dayRows.length === 0 ? (
        <div className="wd-empty">
          No hourly rows available for the selected day (00:00–23:00).
        </div>
      ) : (
        <div
          className="wd-chart-wrap"
          onPointerMove={handlePointerMove}
          onPointerLeave={handlePointerLeave}
        >
          <svg
            ref={svgRef}
            className="wd-chart"
            viewBox="0 0 800 240"
            preserveAspectRatio="none"
          >
            {/* grid lines */}
            {[0, 0.25, 0.5, 0.75, 1].map((g, gi) => {
              const y = 30 + g * (240 - 60);
              const t = (maxT - g * (maxT - minT)).toFixed(0);
              return (
                <g key={gi}>
                  <line
                    x1="0"
                    x2="800"
                    y1={y}
                    y2={y}
                    stroke="#eee"
                    strokeWidth="1"
                  />
                  <text x="6" y={y - 6} fill="#888" fontSize="10">
                    {t}°C
                  </text>
                </g>
              );
            })}

            {/* temperature polyline */}
            <polyline
              fill="none"
              stroke="#ff7a18"
              strokeWidth="2"
              points={dayRows
                .map((r, i) => coordsForIndex(i, 800, 240, 30).join(","))
                .join(" ")}
            />

            {/* subtle area under curve */}
            <path
              d={(() => {
                if (!dayRows.length) return "";
                let d = "";
                dayRows.forEach((r, i) => {
                  const [x, y] = coordsForIndex(i, 800, 240, 30);
                  d += i === 0 ? `M ${x} ${y}` : ` L ${x} ${y}`;
                });
                d += ` L ${800 - 30} ${240 - 30} L ${30} ${240 - 30} Z`;
                return d;
              })()}
              fill="url(#grad)"
              opacity="0.12"
            />

            <defs>
              <linearGradient id="grad" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="#ff7a18" stopOpacity="0.4" />
                <stop offset="100%" stopColor="#ff7a18" stopOpacity="0.05" />
              </linearGradient>
            </defs>

            {/* interactive cursor for dayRows */}
            {cursorIndex !== null &&
              dayRows[cursorIndex] &&
              (() => {
                const r = dayRows[cursorIndex];
                const [cx, cy] = coordsForIndex(cursorIndex, 800, 240, 30);

                // Tooltip size
                const tooltipW = 180;
                const tooltipH = 170;
                const offsetX = 8;
                const offsetY = -58;

                // Clamp tooltip inside chart
                const tx = Math.min(Math.max(cx + offsetX, 0), 800 - tooltipW);
                const ty = Math.min(Math.max(cy + offsetY, 0), 240 - tooltipH);

                return (
                  <g>
                    <line
                      x1={cx}
                      x2={cx}
                      y1={20}
                      y2={220}
                      stroke="#333"
                      strokeWidth="1"
                      strokeDasharray="3 3"
                      opacity="0.8"
                    />
                    <circle
                      cx={cx}
                      cy={cy}
                      r="5"
                      fill="#fff"
                      stroke="#ff7a18"
                      strokeWidth="2"
                    />

                    <rect
                      x={tx}
                      y={ty}
                      width={tooltipW}
                      height={tooltipH}
                      rx="6"
                      fill="#222"
                      opacity="0.95"
                    />

                    <text x={tx + 6} y={ty + 20} fill="#fff" fontSize="12">
                      Temp: {r.temperature_2m.toFixed(1)}°C
                    </text>
                    <text x={tx + 6} y={ty + 36} fill="#ddd" fontSize="11">
                      Humidity: {r.humidity_2m.toFixed(0)}%
                    </text>
                    <text x={tx + 6} y={ty + 52} fill="#ddd" fontSize="11">
                      Solar: {r.solar_radiation.toFixed(0)} W/m²
                    </text>
                    <text x={tx + 6} y={ty + 68} fill="#ddd" fontSize="11">
                      Apparent: {r.apparent_temperature.toFixed(1)}°C
                    </text>
                    <text x={tx + 6} y={ty + 84} fill="#ddd" fontSize="11">
                      Dew point: {r.dew_point_2m.toFixed(1)}°C
                    </text>
                    <text x={tx + 6} y={ty + 100} fill="#ddd" fontSize="11">
                      Rain: {r.rain.toFixed(1)} mm
                    </text>
                    <text x={tx + 6} y={ty + 116} fill="#ddd" fontSize="11">
                      Snow: {r.snowfall.toFixed(1)} mm
                    </text>
                    <text x={tx + 6} y={ty + 132} fill="#ddd" fontSize="11">
                      Wind: {r.windspeed_10m.toFixed(1)} m/s
                    </text>
                    <text x={tx + 6} y={ty + 148} fill="#ccc" fontSize="11">
                      {formatHour(r.date)}
                    </text>
                  </g>
                );
              })()}

            {/* x-axis labels for 00:00-23:00 */}
            {dayRows.map((r, i) => {
              // render up to 8 labels evenly spaced
              if (i % Math.ceil(dayRows.length / 8) !== 0) return null;
              const [x] = coordsForIndex(i, 800, 240, 30);
              return (
                <text
                  key={i}
                  x={x}
                  y={235}
                  fontSize="11"
                  fill="#555"
                  textAnchor="middle"
                >
                  {new Date(r.date).getHours()}:00
                </text>
              );
            })}
          </svg>
        </div>
      )}
    </div>
  );
}
