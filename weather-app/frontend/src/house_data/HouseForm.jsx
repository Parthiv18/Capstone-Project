import React, { useState } from "react";

export default function HouseForm({ onClose }) {
  // Page 1 fields
  const [data, setData] = useState({
    home_size: "",
    age_of_house: "",
    insulation_quality: "",
    // page 2
    hvac_type: "",
    hvac_age: "",
    personal_comfort: 25,
    occupancy: "",
  });

  const [page, setPage] = useState(1); // 1=page1, 2=page2, 3=comfort, 4=done
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  function update(key, val) {
    setData((d) => ({ ...d, [key]: val }));
  }

  function validatePage1() {
    if (!data.home_size || Number(data.home_size) <= 0) return false;
    if (!data.age_of_house || Number(data.age_of_house) < 0) return false;
    if (!data.insulation_quality) return false;
    return true;
  }

  function validatePage2() {
    if (!data.hvac_type) return false;
    return true;
  }

  function validatePage3() {
    // personal_comfort has a default; occupancy must be selected on page 3
    if (!data.occupancy) return false;
    return true;
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      // normalize numeric fields
      const payload = {
        home_size: Number(data.home_size),
        age_of_house: Number(data.age_of_house),
        insulation_quality: data.insulation_quality,
        hvac_type: data.hvac_type,
        hvac_age: data.hvac_age ? Number(data.hvac_age) : null,
        personal_comfort: Number(data.personal_comfort),
        occupancy: data.occupancy,
      };

      // Because `None` is not valid in JS, handle hvac_age removal
      if (!payload.hvac_age) delete payload.hvac_age;

      const savedUser = (() => {
        try {
          const s = localStorage.getItem("weather_user");
          return s ? JSON.parse(s) : null;
        } catch {
          return null;
        }
      })();

      const body = { ...payload };
      if (savedUser && savedUser.username) body.username = savedUser.username;

      const resp = await fetch("http://localhost:8000/house_variables", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const txt = await resp.text();
        throw new Error(txt || resp.statusText);
      }
      const json = await resp.json();
      setSuccess(json.file || "saved");
      setPage(4);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="hf-backdrop" onClick={onClose}>
      <div className="hf-card" onClick={(e) => e.stopPropagation()}>
        <div className="hf-header">
          <h3>House variables</h3>
          <button className="hf-close" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="hf-body">
          <div className="hf-step-indicator">
            <div className={`hf-step ${page === 1 ? "hf-step-active" : ""}`}>
              1
            </div>
            <div className={`hf-step ${page === 2 ? "hf-step-active" : ""}`}>
              2
            </div>
            <div className={`hf-step ${page === 3 ? "hf-step-active" : ""}`}>
              3
            </div>
          </div>

          {page === 1 && (
            <>
              <div className="hf-field">
                <label className="hf-label">Home — Square Feet</label>
                <input
                  className="hf-input"
                  type="number"
                  min="0"
                  value={data.home_size}
                  onChange={(e) => update("home_size", e.target.value)}
                  placeholder="Enter square feet"
                />
              </div>

              <div className="hf-field">
                <label className="hf-label">Age of House (years)</label>
                <input
                  className="hf-input"
                  type="number"
                  min="0"
                  value={data.age_of_house}
                  onChange={(e) => update("age_of_house", e.target.value)}
                  placeholder="Enter age of house"
                />
              </div>

              <div className="hf-field">
                <label className="hf-label">Insulation Quality</label>
                <select
                  className="hf-input"
                  value={data.insulation_quality}
                  onChange={(e) => update("insulation_quality", e.target.value)}
                >
                  <option value="">Select insulation quality</option>
                  <option value="excellent">Excellent</option>
                  <option value="average">Average</option>
                  <option value="poor">Poor</option>
                </select>
              </div>

              <div className="hf-controls">
                <button
                  className="hf-btn hf-btn-primary"
                  disabled={!validatePage1()}
                  onClick={() => setPage(2)}
                >
                  Next
                </button>
              </div>
            </>
          )}

          {page === 2 && (
            <>
              <div className="hf-field">
                <label className="hf-label">HVAC Type</label>
                <select
                  className="hf-input"
                  value={data.hvac_type}
                  onChange={(e) => update("hvac_type", e.target.value)}
                >
                  <option value="">Select HVAC type</option>
                  <option value="central">Central</option>
                  <option value="heat_pump">Heat pump</option>
                  <option value="mini_split">Mini-split</option>
                  <option value="window_ac">Window AC</option>
                  <option value="none">None</option>
                </select>
              </div>

              <div className="hf-field">
                <label className="hf-label">HVAC Age (years)</label>
                <input
                  className="hf-input"
                  type="number"
                  min="0"
                  value={data.hvac_age}
                  onChange={(e) => update("hvac_age", e.target.value)}
                />
              </div>

              <div className="hf-controls">
                <button
                  className="hf-btn hf-btn-ghost"
                  onClick={() => setPage(1)}
                >
                  Back
                </button>
                <button
                  className="hf-btn hf-btn-primary"
                  onClick={() => setPage(3)}
                  disabled={!validatePage2()}
                >
                  Next
                </button>
              </div>
              {error && <div className="hf-error">Error: {error}</div>}
            </>
          )}

          {page === 3 && (
            <>
              <div className="hf-field">
                <label className="hf-label">Personal Comfort (°C)</label>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    value={data.personal_comfort}
                    onChange={(e) => update("personal_comfort", e.target.value)}
                  />
                  <div style={{ minWidth: 36 }}>{data.personal_comfort}°C</div>
                </div>
              </div>

              <div className="hf-field">
                <label className="hf-label">Occupancy</label>
                <select
                  className="hf-input"
                  value={data.occupancy}
                  onChange={(e) => update("occupancy", e.target.value)}
                >
                  <option value="">Select occupancy</option>
                  <option value="home_daytime">Home daytime</option>
                  <option value="away_daytime">Away daytime</option>
                  <option value="hybrid">Hybrid</option>
                </select>
              </div>

              <div className="hf-controls">
                <button
                  className="hf-btn hf-btn-ghost"
                  onClick={() => setPage(2)}
                >
                  Back
                </button>
                <button
                  className="hf-btn hf-btn-primary"
                  onClick={handleSubmit}
                  disabled={submitting || !validatePage3()}
                >
                  {submitting ? "Submitting..." : "Save"}
                </button>
              </div>
              {error && <div className="hf-error">Error: {error}</div>}
            </>
          )}

          {page === 4 && (
            <div className="hf-success">
              <h4>Saved!</h4>
              <div>
                File: <code>{success}</code>
              </div>
              <button
                className="hf-btn hf-btn-primary"
                style={{ marginTop: 12 }}
                onClick={onClose}
              >
                Close
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
