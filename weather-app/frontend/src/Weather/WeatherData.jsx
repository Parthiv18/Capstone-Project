import React, {
  useEffect,
  useState,
  useMemo,
  useRef,
  useCallback,
} from "react";
import "./weather.css";
import { Backend, getStoredUser } from "../App";

// ============================================================
// Utility Functions
// ============================================================

const formatHour = (dateStr) => {
  try {
    return new Date(dateStr).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return dateStr;
  }
};

const toISODate = (date) => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
};

// Chart constants
const CHART = {
  WIDTH: 800,
  HEIGHT: 240,
  PADDING: 30,
  LABELS_COUNT: 8,
  GRID_STEPS: [0, 0.25, 0.5, 0.75, 1],
};

const TOOLTIP = {
  WIDTH: 180,
  HEIGHT: 170,
  OFFSET_X: 8,
  OFFSET_Y: -58,
};

// ============================================================
// WeatherData Component
// ============================================================

export default function WeatherData({ username, loggedIn }) {
  // State
  const [address, setAddress] = useState("");
  const [coords, setCoords] = useState({ lat: null, lon: null });
  const [serverData, setServerData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [rows, setRows] = useState([]);
  const [cursorIndex, setCursorIndex] = useState(null);
  const [selectedDay, setSelectedDay] = useState(null);

  const svgRef = useRef(null);

  // Initialize address from localStorage
  useEffect(() => {
    const user = getStoredUser();
    if (user?.address) {
      setAddress(user.address);
    }
  }, []);

  // Fetch weather data when logged in with address
  useEffect(() => {
    if (!loggedIn || !address) return;

    let cancelled = false;

    const fetchWeather = async () => {
      setLoading(true);
      setError(null);

      try {
        // Try to load stored weather first
        if (username) {
          const userJson = await Backend.getUserWeather(username);
          if (!cancelled && userJson?.data) {
            try {
              const parsed = JSON.parse(userJson.data);
              if (parsed.lat)
                setCoords({ lat: Number(parsed.lat), lon: Number(parsed.lon) });
              setServerData(parsed);
              return;
            } catch {
              // Fall through to fresh fetch
            }
          }
        }

        // Fetch fresh weather data
        const json = await Backend.weatherByAddress(address);
        if (cancelled) return;

        if (json.lat) {
          setCoords({ lat: Number(json.lat), lon: Number(json.lon) });
        }
        setServerData(json);

        // Persist to user's DB for later use (fire and forget)
        if (username) {
          Backend.saveUserWeather(username, JSON.stringify(json)).catch(
            () => {}
          );
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || String(err));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    fetchWeather();
    return () => {
      cancelled = true;
    };
  }, [loggedIn, address, username]);

  // Process weather data when serverData changes
  useEffect(() => {
    if (!serverData) return;

    const weatherRows = serverData.rows || [];
    setRows(weatherRows);

    // Set initial selected day to today
    if (weatherRows.length > 0) {
      const now = new Date(weatherRows[0].date);
      now.setHours(0, 0, 0, 0);
      setSelectedDay(toISODate(now));
    }
    setLoading(false);
  }, [serverData]);

  // Memoized computed values
  const now = Date.now();

  const currentHourRow = useMemo(() => {
    if (!rows.length) return null;
    return rows.reduce((closest, r) => {
      const rowTime = new Date(r.date).getTime();
      const closestTime = new Date(closest.date).getTime();
      return Math.abs(rowTime - now) < Math.abs(closestTime - now)
        ? r
        : closest;
    }, rows[0]);
  }, [rows, now]);

  const next7Days = useMemo(() => {
    const baseDate = rows.length > 0 ? new Date(rows[0].date) : new Date();
    baseDate.setHours(0, 0, 0, 0);

    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(baseDate);
      d.setDate(baseDate.getDate() + i);
      return {
        label: d.toLocaleDateString([], { weekday: "short" }),
        iso: toISODate(d),
        dateObj: d,
      };
    });
  }, [rows]);

  const dayRows = useMemo(() => {
    if (!selectedDay || !rows.length) return [];

    return rows
      .map((r) => ({ ...r, _date: new Date(r.date) }))
      .filter(
        (r) => toISODate(r._date) === selectedDay && r._date.getHours() <= 23
      )
      .sort((a, b) => a._date - b._date);
  }, [rows, selectedDay]);

  const { temps, minT, maxT } = useMemo(() => {
    const temperatures = dayRows.map((r) => r.temperature_2m);
    return {
      temps: temperatures,
      minT: temperatures.length ? Math.min(...temperatures) : 0,
      maxT: temperatures.length ? Math.max(...temperatures) : 30,
    };
  }, [dayRows]);

  // Chart coordinate calculation
  const getChartCoords = useCallback(
    (index) => {
      const { WIDTH, HEIGHT, PADDING } = CHART;
      const count = Math.max(1, dayRows.length - 1);
      const xStep = (WIDTH - PADDING * 2) / count;
      const x = PADDING + index * xStep;

      const temp = dayRows[index]?.temperature_2m ?? 0;
      const range = Math.max(0.0001, maxT - minT);
      const frac = (temp - minT) / range;
      const y = PADDING + (1 - frac) * (HEIGHT - PADDING * 2);

      return [x, y];
    },
    [dayRows, minT, maxT]
  );

  // Event handlers
  const handlePointerMove = useCallback(
    (e) => {
      const svg = svgRef.current;
      if (!svg || dayRows.length === 0) return;

      const rect = svg.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const { WIDTH, PADDING } = CHART;
      const xStep = (WIDTH - PADDING * 2) / Math.max(1, dayRows.length - 1);

      const idx = Math.max(
        0,
        Math.min(dayRows.length - 1, Math.round((x - PADDING) / xStep))
      );
      setCursorIndex(idx);
    },
    [dayRows.length]
  );

  const handlePointerLeave = useCallback(() => {
    setCursorIndex(null);
  }, []);

  const handleDaySelect = useCallback((iso) => {
    setSelectedDay(iso);
    setCursorIndex(null);
  }, []);
  // Loading states
  if (!serverData && coords.lat === null) {
    return <div className="wd-container wd-card">Loading Data...</div>;
  }

  if (loading) {
    return <div className="wd-container wd-card">Loading weather…</div>;
  }

  if (error) {
    return <div className="wd-container wd-card wd-error">Error: {error}</div>;
  }

  // Render chart polyline points
  const renderPolylinePoints = () =>
    dayRows.map((_, i) => getChartCoords(i).join(",")).join(" ");

  // Render area path under curve
  const renderAreaPath = () => {
    if (!dayRows.length) return "";
    const { WIDTH, HEIGHT, PADDING } = CHART;

    const pathSegments = dayRows.map((_, i) => {
      const [x, y] = getChartCoords(i);
      return i === 0 ? `M ${x} ${y}` : ` L ${x} ${y}`;
    });

    return `${pathSegments.join("")} L ${WIDTH - PADDING} ${
      HEIGHT - PADDING
    } L ${PADDING} ${HEIGHT - PADDING} Z`;
  };

  // Render tooltip for cursor
  const renderTooltip = () => {
    if (cursorIndex === null || !dayRows[cursorIndex]) return null;

    const r = dayRows[cursorIndex];
    const [cx, cy] = getChartCoords(cursorIndex);
    const { WIDTH, HEIGHT } = CHART;

    // Clamp tooltip position
    const tx = Math.min(
      Math.max(cx + TOOLTIP.OFFSET_X, 0),
      WIDTH - TOOLTIP.WIDTH
    );
    const ty = Math.min(
      Math.max(cy + TOOLTIP.OFFSET_Y, 0),
      HEIGHT - TOOLTIP.HEIGHT
    );

    const tooltipData = [
      {
        label: "Temp",
        value: `${r.temperature_2m.toFixed(1)}°C`,
        color: "#fff",
      },
      {
        label: "Humidity",
        value: `${r.humidity_2m.toFixed(0)}%`,
        color: "#ddd",
      },
      {
        label: "Solar",
        value: `${r.solar_radiation.toFixed(0)} W/m²`,
        color: "#ddd",
      },
      {
        label: "Apparent",
        value: `${r.apparent_temperature.toFixed(1)}°C`,
        color: "#ddd",
      },
      {
        label: "Dew point",
        value: `${r.dew_point_2m.toFixed(1)}°C`,
        color: "#ddd",
      },
      { label: "Rain", value: `${r.rain.toFixed(1)} mm`, color: "#ddd" },
      { label: "Snow", value: `${r.snowfall.toFixed(1)} mm`, color: "#ddd" },
      {
        label: "Wind",
        value: `${r.windspeed_10m.toFixed(1)} m/s`,
        color: "#ddd",
      },
    ];

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
          width={TOOLTIP.WIDTH}
          height={TOOLTIP.HEIGHT}
          rx="6"
          fill="#222"
          opacity="0.95"
        />
        {tooltipData.map((item, i) => (
          <text
            key={item.label}
            x={tx + 6}
            y={ty + 20 + i * 16}
            fill={item.color}
            fontSize={i === 0 ? "12" : "11"}
          >
            {item.label}: {item.value}
          </text>
        ))}
        <text x={tx + 6} y={ty + 148} fill="#ccc" fontSize="11">
          {formatHour(r.date)}
        </text>
      </g>
    );
  };

  return (
    <div className="wd-container wd-card">
      <div className="wd-card-header" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <h3 className="wd-title">
            Hourly temperature{address ? ` for ${address}` : ""}
          </h3>

          {/* 7-day selector */}
          <div className="wd-day-selector">
            {next7Days.map((d) => (
              <button
                key={d.iso}
                onClick={() => handleDaySelect(d.iso)}
                className={`wd-day-btn ${
                  selectedDay === d.iso ? "wd-day-btn-active" : ""
                }`}
              >
                <div className="wd-day-label">{d.label}</div>
                <div className="wd-day-date">
                  {d.dateObj.getMonth() + 1}/{d.dateObj.getDate()}
                </div>
              </button>
            ))}
          </div>

          {/* Weather metrics */}
          {currentHourRow && (
            <div className="wd-metrics">
              <MetricItem
                label="Rain"
                value={
                  currentHourRow.rain > 0
                    ? `${currentHourRow.rain.toFixed(1)} mm`
                    : "0 mm"
                }
              />
              <MetricItem
                label="Snow"
                value={
                  currentHourRow.snowfall > 0
                    ? `${currentHourRow.snowfall.toFixed(1)} mm`
                    : "0 mm"
                }
              />
              <MetricItem
                label="Wind"
                value={`${currentHourRow.windspeed_10m.toFixed(1)} m/s`}
              />
              <MetricItem
                label="Humidity"
                value={`${currentHourRow.humidity_2m.toFixed(0)}%`}
              />
              <MetricItem
                label="Solar"
                value={`${currentHourRow.solar_radiation.toFixed(0)} W/m²`}
              />
              <MetricItem
                label="Apparent Temperature"
                value={`${currentHourRow.apparent_temperature.toFixed(0)}°C`}
              />
              <MetricItem
                label="Dew Point"
                value={`${currentHourRow.dew_point_2m.toFixed(0)}°C`}
              />
            </div>
          )}
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
          No hourly data available for the selected day.
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
            viewBox={`0 0 ${CHART.WIDTH} ${CHART.HEIGHT}`}
            preserveAspectRatio="none"
          >
            {/* Grid lines */}
            {CHART.GRID_STEPS.map((g, gi) => {
              const y = CHART.PADDING + g * (CHART.HEIGHT - CHART.PADDING * 2);
              const temp = (maxT - g * (maxT - minT)).toFixed(0);
              return (
                <g key={gi}>
                  <line
                    x1="0"
                    x2={CHART.WIDTH}
                    y1={y}
                    y2={y}
                    stroke="#eee"
                    strokeWidth="1"
                  />
                  <text x="6" y={y - 6} fill="#888" fontSize="10">
                    {temp}°C
                  </text>
                </g>
              );
            })}

            {/* Temperature line */}
            <polyline
              fill="none"
              stroke="#ff7a18"
              strokeWidth="2"
              points={renderPolylinePoints()}
            />

            {/* Area under curve */}
            <path d={renderAreaPath()} fill="url(#grad)" opacity="0.12" />

            <defs>
              <linearGradient id="grad" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="#ff7a18" stopOpacity="0.4" />
                <stop offset="100%" stopColor="#ff7a18" stopOpacity="0.05" />
              </linearGradient>
            </defs>

            {/* Interactive tooltip */}
            {renderTooltip()}

            {/* X-axis labels */}
            {dayRows.map((r, i) => {
              if (i % Math.ceil(dayRows.length / CHART.LABELS_COUNT) !== 0)
                return null;
              const [x] = getChartCoords(i);
              return (
                <text
                  key={i}
                  x={x}
                  y={CHART.HEIGHT - 5}
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

// Reusable metric component
const MetricItem = ({ label, value }) => (
  <div className="wd-metric">
    <div className="wd-metric-label">{label}</div>
    <div className="wd-metric-value">{value}</div>
  </div>
);
