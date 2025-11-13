import React, { useState, useEffect } from "react";
import WeatherData from "./weather_data/WeatherData";
import HouseForm, { HouseFormTrigger } from "./house_data/HouseForm";
import Login from "./auth/Login";
import Logout from "./auth/Logout";
import "./house_data/house_form.css";

const API_BASE = "http://localhost:8000";

export default function App() {
  const [loggedIn, setLoggedIn] = useState(false);
  const [username, setUsername] = useState(null);

  // Restore login from localStorage on mount so reload doesn't log user out
  useEffect(() => {
    try {
      const s = localStorage.getItem("weather_user");
      if (s) {
        const parsed = JSON.parse(s);
        if (parsed && parsed.username) {
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
      <WeatherData username={username} loggedIn={loggedIn} />
    </div>
  );
}