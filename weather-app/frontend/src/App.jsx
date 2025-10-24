import React, { useState } from "react";
import WeatherData from "./weather_data/WeatherData";
import HouseForm from "./house_data/HouseForm";
import "./house_data/house_form.css";

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
        <div style={{ marginBottom: 12 }}>
          <HouseFormTrigger />
        </div>
      </div>
      <WeatherData lat={activeLat} lon={activeLon} />
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
