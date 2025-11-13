import React from "react";

export default function Logout({ onLogout }) {
  return (
    <button
      onClick={() => onLogout && onLogout()}
      style={{
        background: "transparent",
        border: "1px solid #ddd",
        padding: "8px 12px",
        borderRadius: 8,
        cursor: "pointer",
      }}
    >
      Logout
    </button>
  );
}
