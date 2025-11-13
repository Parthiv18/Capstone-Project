import React, { useState } from "react";

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
    <div style={{ display: "flex", gap: 24, padding: 20 }}>
      <div
        style={{
          flex: 1,
          border: "1px solid #ddd",
          padding: 16,
          borderRadius: 8,
        }}
      >
        <h3>Login</h3>
        <form onSubmit={doLogin}>
          <div style={{ marginBottom: 8 }}>
            <label>Username</label>
            <input
              value={loginUser}
              onChange={(e) => setLoginUser(e.target.value)}
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label>Password</label>
            <input
              type="password"
              value={loginPass}
              onChange={(e) => setLoginPass(e.target.value)}
            />
          </div>
          <button type="submit" disabled={loading}>
            {loading ? "…" : "Login"}
          </button>
        </form>
      </div>

      <div
        style={{
          flex: 1,
          border: "1px solid #ddd",
          padding: 16,
          borderRadius: 8,
        }}
      >
        <h3>Sign Up</h3>
        <form onSubmit={doSignup}>
          <div style={{ marginBottom: 8 }}>
            <label>Username</label>
            <input
              value={signupUser}
              onChange={(e) => setSignupUser(e.target.value)}
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label>Password</label>
            <input
              type="password"
              value={signupPass}
              onChange={(e) => setSignupPass(e.target.value)}
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label>Postal Code</label>
            <input
              value={signupPostal}
              onChange={(e) => setSignupPostal(e.target.value)}
              placeholder="L7A1T1"
            />
          </div>
          <button type="submit" disabled={loading}>
            {loading ? "…" : "Sign Up"}
          </button>
        </form>
      </div>

      <div style={{ minWidth: 260 }}>
        <div style={{ fontSize: 12, color: "#666" }}>
          {error && <div style={{ color: "#b00020" }}>{error}</div>}
          <p>Don't have an account? Sign up!</p>
        </div>
      </div>
    </div>
  );
}
