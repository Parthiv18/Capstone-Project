import React from "react";
import "./alerts.css";

export default function Alerts() {
  // Hard-coded example alert
  const alertText =
    "Running dryer now will add 3 kW during the predicted HVAC peak.";

  return (
    <div className="alert-card">
      <div className="alert-header">
        <span className="alert-icon">⚠️</span>
        <span className="alert-title">Alerts</span>
      </div>

      <div className="alert-body">{alertText}</div>
    </div>
  );
}
