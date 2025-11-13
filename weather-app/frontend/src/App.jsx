import React, { useState, useEffect } from "react";
import WeatherData from "./weather_data/WeatherData";
import HouseForm from "./house_data/HouseForm";
import Login from "./Login";
import Logout from "./Logout";
import "./house_data/house_form.css";

const API_BASE = "http://localhost:8000";

export default function App() {
  const [loggedIn, setLoggedIn] = useState(false);
  const [username, setUsername] = useState(null);

  // switch to postal-code-driven lookup
  const [postal, setPostal] = useState("L7A1T1");
  const [activeLat, setActiveLat] = useState(43.716964);
  const [activeLon, setActiveLon] = useState(-79.821611);
  const [serverData, setServerData] = useState(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState(null);

  useEffect(() => {
    const saved = localStorage.getItem("weather_user");
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        setLoggedIn(true);
        setUsername(parsed.username);
        if (parsed.postalcode) setPostal(parsed.postalcode);
      } catch {}
    }
  }, []);

  // Auto-fetch weather whenever the logged-in user's postal code is available
  useEffect(() => {
    let cancelled = false;
    async function fetchByPostal() {
      if (!loggedIn) return;
      if (!postal) return;
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
        if (cancelled) return;
        if (json.lat) setActiveLat(Number(json.lat));
        if (json.lon) setActiveLon(Number(json.lon));
        setServerData(json);
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
  }, [loggedIn, postal]);

  function handleLogin(info) {
    setLoggedIn(true);
    setUsername(info.username);
    if (info.postalcode) setPostal(info.postalcode);
    localStorage.setItem("weather_user", JSON.stringify(info));
  }

  function handleLogout() {
    localStorage.removeItem("weather_user");
    setLoggedIn(false);
    setUsername(null);
    setServerData(null);
    setPostal(null);
    setActiveLat(43.716964);
    setActiveLon(-79.821611);
  }

  if (!loggedIn) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="app" style={{ padding: 12 }}>
      <div
        style={{
          marginBottom: 12,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <div>
          <div style={{ marginBottom: 8 }}>Welcome, {username}</div>
          <div style={{ marginBottom: 12 }}>
            <HouseFormTrigger />
          </div>
        </div>
        <div>
          <Logout onLogout={handleLogout} />
        </div>
      </div>
      <WeatherData
        lat={activeLat}
        lon={activeLon}
        serverData={serverData}
        postal={postal}
      />
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
