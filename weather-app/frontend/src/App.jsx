import React, { useState } from "react";
import WeatherData from "./weather_data/WeatherData";
import HouseForm from "./house_data/HouseForm";
import "./house_data/house_form.css";

const API_BASE = "http://localhost:8000";

export default function App() {
  // switch to postal-code-driven lookup
  const [postal, setPostal] = useState("L7A1T1");
  const [activeLat, setActiveLat] = useState(43.716964);
  const [activeLon, setActiveLon] = useState(-79.821611);
  const [serverData, setServerData] = useState(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState(null);

  return (
    <div className="app" style={{ padding: 12 }}>
      <div style={{ marginBottom: 12 }}>
        <label style={{ marginRight: 8 }}>
          Postal Code
          <input
            value={postal}
            onChange={(e) => setPostal(e.target.value)}
            style={{ marginLeft: 6 }}
            placeholder="e.g. L7A1T1"
          />
        </label>
        <button
          onClick={async () => {
            setFetching(true);
            setFetchError(null);
            setServerData(null);
            try {
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
              // set coords for display and pass full data to WeatherData
              if (json.lat) setActiveLat(Number(json.lat));
              if (json.lon) setActiveLon(Number(json.lon));
              setServerData(json);
            } catch (e) {
              setFetchError(e.message || String(e));
            } finally {
              setFetching(false);
            }
          }}
          disabled={fetching}
        >
          {fetching ? "Looking up…" : "Fetch"}
        </button>
        {fetchError && (
          <div style={{ color: "#b00020", marginLeft: 12 }}>{fetchError}</div>
        )}
        <div style={{ marginBottom: 12 }}>
          <HouseFormTrigger />
        </div>
      </div>
      <WeatherData lat={activeLat} lon={activeLon} serverData={serverData} />
    </div>
  );
}

function HouseFormTrigger() {
  const [show, setShow] = useState(false);
  return (
    <>
      <button
        onClick={() => setShow(true)}
        style={{
          background: "linear-gradient(90deg,#ff7a18,#ffb347)",
          color: "white",
          border: "none",
          padding: "10px 16px",
          borderRadius: 10,
          cursor: "pointer",
          boxShadow: "0 6px 18px rgba(0,0,0,0.12)",
        }}
      >
        Enter house variables
      </button>
      {show && <HouseForm onClose={() => setShow(false)} />}
    </>
  );
}
