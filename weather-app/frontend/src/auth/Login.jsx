import React, { useState } from "react";
import "./Login.css";

const API_BASE = "http://localhost:8000";

export default function Login({ onLogin }) {
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [signupUser, setSignupUser] = useState("");
  const [signupPass, setSignupPass] = useState("");
  const [signupPostal, setSignupPostal] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function doLogin(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: loginUser, password: loginPass }),
      });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || res.statusText);
      }
      const json = await res.json();
      const info = { username: json.username, postalcode: json.postalcode };
      try {
        localStorage.setItem("weather_user", JSON.stringify(info));
      } catch (e) {}
      onLogin(info);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function doSignup(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    // client-side validation: require username, password, postal
    if (!signupUser || !signupPass || !signupPostal) {
      setError("username, password and postal code are required for signup");
      setLoading(false);
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: signupUser,
          password: signupPass,
          postalcode: signupPostal,
        }),
      });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || res.statusText);
      }
      // auto-login after signup
      const loginRes = await fetch(`${API_BASE}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: signupUser, password: signupPass }),
      });
      if (!loginRes.ok) {
        const txt = await loginRes.text();
        throw new Error(txt || loginRes.statusText);
      }
      const json = await loginRes.json();
      const info = { username: json.username, postalcode: json.postalcode };
      try {
        localStorage.setItem("weather_user", JSON.stringify(info));
      } catch (e) {}
      onLogin(info);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-container">
      <div className="auth-card">
        <div className="auth-grid">
          {/* SIGN UP */}
          <div className="auth-form-container">
            <h2 className="auth-h2">Sign Up</h2>
            <form onSubmit={doSignup} className="auth-form">
              <div className="auth-input-group">
                <label className="auth-label">Username</label>
                <div className="auth-input-wrapper">
                  <input
                    type="text"
                    value={signupUser}
                    onChange={(e) => setSignupUser(e.target.value)}
                    className="auth-input"
                    placeholder="Enter username"
                  />
                </div>
              </div>

              <div className="auth-input-group">
                <label className="auth-label">Password</label>
                <div className="auth-input-wrapper">
                  <input
                    type="password"
                    value={signupPass}
                    onChange={(e) => setSignupPass(e.target.value)}
                    className="auth-input"
                    placeholder="Create password"
                  />
                </div>
              </div>

              <div className="auth-input-group">
                <label className="auth-label">Postal Code</label>
                <div className="auth-input-wrapper">
                  <input
                    type="text"
                    value={signupPostal}
                    onChange={(e) => setSignupPostal(e.target.value)}
                    className="auth-input"
                    placeholder="Enter postal code"
                  />
                </div>
              </div>

              <button
                type="submit"
                className="auth-button signup-button"
                disabled={loading}
              >
                {loading ? "..." : "Sign Up"}
              </button>
            </form>
          </div>

          {/* LOGIN */}
          <div className="auth-form-container">
            <h2 className="auth-h2">Login</h2>
            <form onSubmit={doLogin} className="auth-form">
              <div className="auth-input-group">
                <label className="auth-label">Username</label>
                <div className="auth-input-wrapper">
                  <input
                    type="text"
                    value={loginUser}
                    onChange={(e) => setLoginUser(e.target.value)}
                    className="auth-input"
                    placeholder="Enter username"
                  />
                </div>
              </div>

              <div className="auth-input-group">
                <label className="auth-label">Password</label>
                <div className="auth-input-wrapper">
                  <input
                    type="password"
                    value={loginPass}
                    onChange={(e) => setLoginPass(e.target.value)}
                    className="auth-input"
                    placeholder="Enter password"
                  />
                </div>
              </div>

              <button
                type="submit"
                className="auth-button login-button"
                disabled={loading}
              >
                <span className="login-button-text">
                  {loading ? "..." : "Login"}
                </span>
              </button>
            </form>
          </div>
        </div>

        {/* FOOTER */}
        <div className="auth-footer">
          {error ? (
            <p className="auth-error-text">
              {error}{" "}
              <span className="auth-footer-link">
                Don't have an account? Sign up!
              </span>
            </p>
          ) : (
            <p className="auth-welcome-text">
              Welcome
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
