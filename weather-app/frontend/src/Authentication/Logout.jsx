import React, { useCallback } from "react";
import { clearStoredUser } from "../App";

const BUTTON_STYLE = {
  background: "transparent",
  border: "1px solid #ffffff",
  padding: "8px 12px",
  borderRadius: 8,
  color: "#ffffff",
  cursor: "pointer",
};

export default function Logout({ onLogout }) {
  const handleLogout = useCallback(() => {
    clearStoredUser();
    onLogout?.();
  }, [onLogout]);

  return (
    <button onClick={handleLogout} style={BUTTON_STYLE}>
      Logout
    </button>
  );
}
