import React, { useState, useEffect } from "react";
import WeatherData from "./weather_data/WeatherData";
import HouseForm, { HouseFormTrigger } from "./house_data/HouseForm";
import Login from "./auth/Login";
import Logout from "./auth/Logout";
import Thermostat from "./thermostat_data/Thermostat";
import Alerts from "./alerts_data/Alerts"; // <— NEW
import "./app.css"; // <— NEW layout styling
import "./house_data/house_form.css";

const API_BASE = "http://localhost:8000";

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
          <HouseFormTrigger />
        </div>
        <Logout onLogout={handleLogout} />
      </div>

      <div className="cards-grid">
        <div className="left-column">
          <WeatherData username={username} loggedIn={loggedIn} />
        </div>

        <div className="right-column">
          <Thermostat />
          <Alerts />
        </div>
      </div>
    </div>
  );
}
