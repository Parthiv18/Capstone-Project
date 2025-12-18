import React, { useState, useEffect } from "react";
import WeatherData from "./Weather/WeatherData";
import HouseForm, { HouseFormTrigger } from "./HouseVariable/HouseForm";
import "./HouseVariable/house_form.css";
import Login from "./Authentication/Login";
import Logout from "./Authentication/Logout";
import Thermostat from "./Thermostat/Thermostat";
import Alerts from "./Alerts/Alerts";
import "./app.css";

const API_BASE = "http://localhost:8000";

// Frontend connector: centralized backend API methods
export const Backend = {
  async login(username, password) {
    const res = await fetch(`${API_BASE}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async signup(username, password, address) {
    const res = await fetch(`${API_BASE}/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, address }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async weatherByAddress(address) {
    const res = await fetch(
      `${API_BASE}/weather_address?address=${encodeURIComponent(address)}`
    );
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async getUserWeather(username) {
    const res = await fetch(
      `${API_BASE}/user/weather?username=${encodeURIComponent(username)}`
    );
    if (!res.ok) return null;
    return res.json();
  },

  async saveUserWeather(username, data) {
    const res = await fetch(`${API_BASE}/user/weather`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, data }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async saveHouse(username, data) {
    const res = await fetch(`${API_BASE}/user/house`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, data }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async getHouse(username) {
    const res = await fetch(
      `${API_BASE}/user/house?username=${encodeURIComponent(username)}`
    );
    if (!res.ok) return null;
    return res.json();
  },

  async getSimulation(username) {
    const res = await fetch(
      `${API_BASE}/api/simulation/${encodeURIComponent(username)}`
    );
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
};

export default function App() {
  const [loggedIn, setLoggedIn] = useState(false);
  const [username, setUsername] = useState(null);

  useEffect(() => {
    try {
      const s = localStorage.getItem("weather_user");
      if (s) {
        const parsed = JSON.parse(s);
        if (parsed?.username) {
          setLoggedIn(true);
          setUsername(parsed.username);
        }
      }
    } catch (_) {}
  }, []);

  function handleLogin(info) {
    setLoggedIn(true);
    setUsername(info.username);
  }

  function handleLogout() {
    setLoggedIn(false);
    setUsername(null);
  }

  if (!loggedIn) return <Login onLogin={handleLogin} />;

  return (
    <div className="app-container">
      <div className="app-header">
        <div>
          <div className="welcome">Welcome, {username}</div>
          <div className="header-actions">
            <HouseFormTrigger />
            <Logout onLogout={handleLogout} />
          </div>
        </div>
      </div>

      <div className="cards-grid">
        <div className="top-row">
          <WeatherData username={username} loggedIn={loggedIn} />
          <Thermostat username={username} />
        </div>
        <Alerts />
      </div>
    </div>
  );
}
