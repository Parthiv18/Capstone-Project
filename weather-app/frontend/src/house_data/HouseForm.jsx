import React, { useState } from "react";

export default function HouseForm({ onClose }) {
  const fields = [
    { key: "home_size", label: "Home size (sq ft)", type: "text" },
    {
      key: "insulation_quality",
      label: "Insulation quality",
      type: "select",
      options: ["poor", "average", "good", "excellent"],
    },
    {
      key: "hvac_type",
      label: "HVAC type",
      type: "select",
      options: ["central", "heat_pump", "window_ac", "none"],
    },
    { key: "occupancy_start", label: "Occupancy start (HH:MM)", type: "text" },
    { key: "occupancy_end", label: "Occupancy end (HH:MM)", type: "text" },
  ];

  const [data, setData] = useState({
    home_size: "",
    insulation_quality: "",
    hvac_type: "",
    occupancy_start: "",
    occupancy_end: "",
  });
  const [page, setPage] = useState(0); // 0=input, 1=confirm, 2=done
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  function update(key, val) {
    setData((d) => ({ ...d, [key]: val }));
  }

  function validate() {
    for (const f of fields) {
      if (!data[f.key] || String(data[f.key]).trim() === "") return false;
    }
    return true;
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      const payload = { ...data };
      const resp = await fetch("http://127.0.0.1:8000/house_variables", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const txt = await resp.text();
        throw new Error(txt || resp.statusText);
      }
      const json = await resp.json();
      setSuccess(json.file || "saved");
      setPage(2);
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
          {page === 0 && (
            <>
              {fields.map((f) => (
                <div className="hf-field" key={f.key}>
                  <label className="hf-label">{f.label}</label>
                  {f.type === "select" ? (
                    <select
                      className="hf-input"
                      value={data[f.key]}
                      onChange={(e) => update(f.key, e.target.value)}
                    >
                      <option value="">Select {f.label.toLowerCase()}</option>
                      {f.options.map((opt) => (
                        <option key={opt} value={opt}>
                          {opt.charAt(0).toUpperCase() + opt.slice(1)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className="hf-input"
                      type={f.type}
                      value={data[f.key]}
                      onChange={(e) => update(f.key, e.target.value)}
                      placeholder={f.label}
                    />
                  )}
                </div>
              ))}
              <div className="hf-controls">
                <button
                  className="hf-btn hf-btn-primary"
                  disabled={!validate()}
                  onClick={() => setPage(1)}
                >
                  Next
                </button>
              </div>
            </>
          )}

          {page === 1 && (
            <>
              <div className="hf-confirm">
                <h4>Confirm your entries:</h4>
                <ul style={{ listStyle: "none", padding: 0 }}>
                  {fields.map((f) => (
                    <li key={f.key} style={{ marginBottom: 8 }}>
                      <strong>{f.label}:</strong> {String(data[f.key])}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="hf-controls">
                <button
                  className="hf-btn hf-btn-ghost"
                  onClick={() => setPage(0)}
                >
                  Back
                </button>
                <button
                  className="hf-btn hf-btn-primary"
                  onClick={handleSubmit}
                  disabled={submitting}
                >
                  {submitting ? "Submitting..." : "Confirm & Save"}
                </button>
              </div>
              {error && <div className="hf-error">Error: {error}</div>}
            </>
          )}

          {page === 2 && (
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
