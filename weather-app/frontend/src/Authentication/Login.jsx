import React, { useState } from "react";
import "./Login.css";
import { Backend } from "../App";

export default function Login({ onLogin }) {
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [signupUser, setSignupUser] = useState("");
  const [signupPass, setSignupPass] = useState("");
  const [signupAddress, setSignupAddress] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function doLogin(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const json = await Backend.login(loginUser, loginPass);
      const info = { username: json.username, address: json.address };
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
    // client-side validation: require username, password, address
    if (!signupUser || !signupPass || !signupAddress) {
      setError("username, password and address are required for signup");
      setLoading(false);
      return;
    }
    try {
      await Backend.signup(signupUser, signupPass, signupAddress);
      // auto-login after signup
      const json = await Backend.login(signupUser, signupPass);
      const info = { username: json.username, address: json.address };
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
                <label className="auth-label">Address</label>
                <div className="auth-input-wrapper">
                  <input
                    type="text"
                    value={signupAddress}
                    onChange={(e) => setSignupAddress(e.target.value)}
                    className="auth-input"
                    placeholder={`Format: "Street, City"`}
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
            <p className="auth-welcome-text">Welcome</p>
          )}
        </div>
      </div>
    </div>
  );
}
