import React, { useState, useCallback } from "react";
import "./Login.css";
import { Backend, setStoredUser } from "../App";

// ============================================================
// Login Component
// ============================================================

export default function Login({ onLogin }) {
  // Form state
  const [loginForm, setLoginForm] = useState({ user: "", pass: "" });
  const [signupForm, setSignupForm] = useState({
    user: "",
    pass: "",
    address: "",
  });
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // Handle successful authentication
  const handleAuthSuccess = useCallback(
    (userInfo) => {
      const info = { username: userInfo.username, address: userInfo.address };
      setStoredUser(info);
      onLogin(info);
    },
    [onLogin]
  );

  // Login handler
  const handleLogin = useCallback(
    async (e) => {
      e.preventDefault();
      setError(null);
      setLoading(true);

      try {
        const result = await Backend.login(loginForm.user, loginForm.pass);
        handleAuthSuccess(result);
      } catch (err) {
        setError(err.message || String(err));
      } finally {
        setLoading(false);
      }
    },
    [loginForm, handleAuthSuccess]
  );

  // Signup handler
  const handleSignup = useCallback(
    async (e) => {
      e.preventDefault();
      setError(null);

      // Validation
      if (!signupForm.user || !signupForm.pass || !signupForm.address) {
        setError("Username, password, and address are required for signup");
        return;
      }

      setLoading(true);

      try {
        await Backend.signup(
          signupForm.user,
          signupForm.pass,
          signupForm.address
        );
        // Auto-login after signup
        const result = await Backend.login(signupForm.user, signupForm.pass);
        handleAuthSuccess(result);
      } catch (err) {
        setError(err.message || String(err));
      } finally {
        setLoading(false);
      }
    },
    [signupForm, handleAuthSuccess]
  );

  // Input change handlers
  const updateLoginForm = useCallback((field, value) => {
    setLoginForm((prev) => ({ ...prev, [field]: value }));
  }, []);

  const updateSignupForm = useCallback((field, value) => {
    setSignupForm((prev) => ({ ...prev, [field]: value }));
  }, []);

  return (
    <div className="auth-container">
      <div className="auth-card">
        <div className="auth-grid">
          {/* Sign Up Form */}
          <AuthForm
            title="Sign Up"
            onSubmit={handleSignup}
            loading={loading}
            buttonText="Sign Up"
            buttonClass="signup-button"
          >
            <InputField
              label="Username"
              value={signupForm.user}
              onChange={(v) => updateSignupForm("user", v)}
              placeholder="Enter username"
            />
            <InputField
              label="Password"
              type="password"
              value={signupForm.pass}
              onChange={(v) => updateSignupForm("pass", v)}
              placeholder="Create password"
            />
            <InputField
              label="Address"
              value={signupForm.address}
              onChange={(v) => updateSignupForm("address", v)}
              placeholder='Format: "Street, City"'
            />
          </AuthForm>

          {/* Login Form */}
          <AuthForm
            title="Login"
            onSubmit={handleLogin}
            loading={loading}
            buttonText="Login"
            buttonClass="login-button"
          >
            <InputField
              label="Username"
              value={loginForm.user}
              onChange={(v) => updateLoginForm("user", v)}
              placeholder="Enter username"
            />
            <InputField
              label="Password"
              type="password"
              value={loginForm.pass}
              onChange={(v) => updateLoginForm("pass", v)}
              placeholder="Enter password"
            />
          </AuthForm>
        </div>

        {/* Footer */}
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

// ============================================================
// Sub-components
// ============================================================

const AuthForm = ({
  title,
  onSubmit,
  loading,
  buttonText,
  buttonClass,
  children,
}) => (
  <div className="auth-form-container">
    <h2 className="auth-h2">{title}</h2>
    <form onSubmit={onSubmit} className="auth-form">
      {children}
      <button
        type="submit"
        className={`auth-button ${buttonClass}`}
        disabled={loading}
      >
        <span className="login-button-text">
          {loading ? "..." : buttonText}
        </span>
      </button>
    </form>
  </div>
);

const InputField = ({ label, type = "text", value, onChange, placeholder }) => (
  <div className="auth-input-group">
    <label className="auth-label">{label}</label>
    <div className="auth-input-wrapper">
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="auth-input"
        placeholder={placeholder}
      />
    </div>
  </div>
);
