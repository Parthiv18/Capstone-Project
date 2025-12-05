import React from "react";

export default function Logout({ onLogout }) {
  return (
    <button
      onClick={() => {
        try {
          localStorage.removeItem("weather_user");
        } catch (e) {}
        if (onLogout) onLogout();
      }}
      style={{
        background: "transparent",
        border: "1px solid #ffffff",
        padding: "8px 12px",
        borderRadius: 8,
        color: "#ffffff",
        cursor: "pointer",
      }}
    >
      Logout
    </button>
  );
}
