import React, { useState, useEffect, useCallback } from "react";
import WeatherData from "./Weather/WeatherData";
import { HouseFormTrigger } from "./HouseVariable/HouseForm";
import "./HouseVariable/house_form.css";
import Login from "./Authentication/Login";
import Logout from "./Authentication/Logout";
import Thermostat from "./Thermostat/Thermostat";
import Alerts from "./Alerts/Alerts";
import "./app.css";

// Constants
const API_BASE = "http://localhost:8000";
const STORAGE_KEY = "weather_user";

/**
 * Helper to make API requests with consistent error handling
 */
const apiRequest = async (url, options = {}) => {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(errorText || `Request failed with status ${res.status}`);
  }

  return res.json();
};

/**
 * Centralized Backend API - all server communication in one place
 */
export const Backend = {
  // Auth
  login: (username, password) =>
    apiRequest(`${API_BASE}/login`, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  signup: (username, password, address) =>
    apiRequest(`${API_BASE}/signup`, {
      method: "POST",
      body: JSON.stringify({ username, password, address }),
    }),

  // Weather
  weatherByAddress: (address) =>
    apiRequest(
      `${API_BASE}/weather_address?address=${encodeURIComponent(address)}`
    ),

  getUserWeather: async (username) => {
    try {
      return await apiRequest(
        `${API_BASE}/user/weather?username=${encodeURIComponent(username)}`
      );
    } catch {
      return null;
    }
  },

  saveUserWeather: (username, data) =>
    apiRequest(`${API_BASE}/user/weather`, {
      method: "POST",
      body: JSON.stringify({ username, data }),
    }),

  // House
  saveHouse: (username, data) =>
    apiRequest(`${API_BASE}/user/house`, {
      method: "POST",
      body: JSON.stringify({ username, data }),
    }),

  getHouse: async (username) => {
    try {
      return await apiRequest(
        `${API_BASE}/user/house?username=${encodeURIComponent(username)}`
      );
    } catch {
      return null;
    }
  },

  // Simulation
  getSimulation: (username) =>
    apiRequest(`${API_BASE}/api/simulation/${encodeURIComponent(username)}`),

  // HVAC AI - no targetTemp param means use saved/default
  getHVACSchedule: (username, targetTemp = null) => {
    const url =
      targetTemp !== null
        ? `${API_BASE}/api/hvac/${encodeURIComponent(
            username
          )}?target_temp=${targetTemp}`
        : `${API_BASE}/api/hvac/${encodeURIComponent(username)}`;
    return apiRequest(url);
  },

  refreshHVACSchedule: (username, targetTemp) =>
    apiRequest(
      `${API_BASE}/api/hvac/${encodeURIComponent(
        username
      )}/refresh?target_temp=${targetTemp}`,
      { method: "POST" }
    ),
};

/**
 * Helper to safely access localStorage
 */
const getStoredUser = () => {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : null;
  } catch {
    return null;
  }
};

const setStoredUser = (user) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
  } catch {
    // Storage unavailable - silent fail
  }
};

const clearStoredUser = () => {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable - silent fail
  }
};

export { STORAGE_KEY, getStoredUser, setStoredUser, clearStoredUser };

export default function App() {
  const [loggedIn, setLoggedIn] = useState(false);
  const [username, setUsername] = useState(null);

  // Restore session on mount
  useEffect(() => {
    const storedUser = getStoredUser();
    if (storedUser?.username) {
      setLoggedIn(true);
      setUsername(storedUser.username);
    }
  }, []);

  const handleLogin = useCallback((info) => {
    setLoggedIn(true);
    setUsername(info.username);
  }, []);

  const handleLogout = useCallback(() => {
    clearStoredUser();
    setLoggedIn(false);
    setUsername(null);
  }, []);

  // Show login if not authenticated
  if (!loggedIn) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <div>
          <h1 className="welcome">Welcome, {username}</h1>
          <div className="header-actions">
            <HouseFormTrigger />
            <Logout onLogout={handleLogout} />
          </div>
        </div>
      </header>

      <main className="cards-grid">
        <section className="top-row">
          <WeatherData username={username} loggedIn={loggedIn} />
          <Thermostat username={username} />
        </section>
        <Alerts username={username} />
      </main>
    </div>
  );
}
