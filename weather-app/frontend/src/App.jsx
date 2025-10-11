import React, { useState } from "react";
import WeatherData from "./weather_data/WeatherData";

export default function App() {
  const [lat, setLat] = useState(43.716964);
  const [lon, setLon] = useState(-79.821611);
  const [activeLat, setActiveLat] = useState(lat);
  const [activeLon, setActiveLon] = useState(lon);

  return (
    <div className="app" style={{ padding: 12 }}>
      <div style={{ marginBottom: 12 }}>
        <label style={{ marginRight: 8 }}>
          Latitude
          <input
            value={lat}
            onChange={(e) => setLat(e.target.value)}
            style={{ marginLeft: 6 }}
          />
        </label>
        <label style={{ marginLeft: 12, marginRight: 8 }}>
          Longitude
          <input
            value={lon}
            onChange={(e) => setLon(e.target.value)}
            style={{ marginLeft: 6 }}
          />
        </label>
        <button
          onClick={() => {
            setActiveLat(lat);
            setActiveLon(lon);
          }}
        >
          Fetch
        </button>
      </div>
      <WeatherData lat={activeLat} lon={activeLon} />
    </div>
  );
}
