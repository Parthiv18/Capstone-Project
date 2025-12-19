import React, { useState, useEffect, useCallback } from "react";
import { Backend, getStoredUser } from "../App";

// ============================================================
// Constants
// ============================================================

const INITIAL_FORM_DATA = {
  home_size: "",
  age_of_house: "",
  insulation_quality: "",
  hvac_type: "",
  hvac_age: "",
  personal_comfort: 25,
  occupancy: "",
};

const INSULATION_OPTIONS = [
  { value: "", label: "Select insulation quality" },
  { value: "excellent", label: "Excellent" },
  { value: "average", label: "Average" },
  { value: "poor", label: "Poor" },
];

const HVAC_OPTIONS = [
  { value: "", label: "Select HVAC type" },
  { value: "central", label: "Central" },
  { value: "heat_pump", label: "Heat pump" },
  { value: "mini_split", label: "Mini-split" },
  { value: "window_ac", label: "Window AC" },
  { value: "none", label: "None" },
];

const OCCUPANCY_OPTIONS = [
  { value: "", label: "Select occupancy" },
  { value: "home_daytime", label: "Home daytime" },
  { value: "away_daytime", label: "Away daytime" },
  { value: "hybrid", label: "Hybrid" },
];

const APPLIANCE_OPTIONS = [
  "Electric Space Heater",
  "Portable Air Conditioner",
  "Electric Water Heater",
  "Gas Water Heater",
  "Oven (Electric or Gas)",
  "Stove / Cooktop (Electric, Gas, or Induction)",
  "Clothes Dryer (Electric or Gas)",
  "Washing Machine (hot water cycles)",
  "Dishwasher (especially drying cycles)",
  "Electric Vehicle Charger (Level 1 or Level 2)",
];

const TOTAL_PAGES = 4;

// ============================================================
// Helper Functions
// ============================================================

const normalizeIncomingHouse = (raw) => {
  if (!raw || typeof raw !== "object") return null;

  // Support both current and legacy data shapes
  const merged =
    raw.data && typeof raw.data === "object"
      ? { ...raw.data, appliances: raw.appliances }
      : raw;

  const {
    home_size,
    age_of_house,
    insulation_quality,
    hvac_type,
    hvac_age,
    personal_comfort,
    occupancy,
    appliances,
  } = merged;

  return {
    data: {
      home_size: home_size ?? "",
      age_of_house: age_of_house ?? "",
      insulation_quality: insulation_quality ?? "",
      hvac_type: hvac_type ?? "",
      hvac_age: hvac_age ?? "",
      personal_comfort: personal_comfort ?? 25,
      occupancy: occupancy ?? "",
    },
    appliances: Array.isArray(appliances) ? appliances : [],
  };
};

// ============================================================
// HouseForm Component
// ============================================================

export default function HouseForm({ onClose }) {
  const [data, setData] = useState(INITIAL_FORM_DATA);
  const [appliances, setAppliances] = useState([]);
  const [page, setPage] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  // Load existing house data
  const loadHouseData = useCallback(async () => {
    const user = getStoredUser();
    if (!user?.username) return;

    try {
      const body = await Backend.getHouse(user.username);
      if (!body?.data) return;

      const normalized = normalizeIncomingHouse(body.data);
      if (normalized) {
        setData((prev) => ({ ...prev, ...normalized.data }));
        setAppliances(normalized.appliances);
      }
    } catch {
      // Silent fail - user may not have house data yet
    }
  }, []);

  useEffect(() => {
    loadHouseData();
  }, [loadHouseData]);

  // Form handlers
  const updateField = useCallback((key, value) => {
    setData((prev) => ({ ...prev, [key]: value }));
  }, []);

  const toggleAppliance = useCallback((appliance, checked) => {
    setAppliances((prev) =>
      checked ? [...prev, appliance] : prev.filter((a) => a !== appliance)
    );
  }, []);

  // Form submission
  const handleSubmit = useCallback(async () => {
    setSubmitting(true);
    setError(null);

    try {
      const user = getStoredUser();
      if (!user?.username) throw new Error("Not logged in");

      const payload = {
        home_size: Number(data.home_size),
        age_of_house: Number(data.age_of_house),
        insulation_quality: data.insulation_quality,
        hvac_type: data.hvac_type,
        hvac_age: data.hvac_age ? Number(data.hvac_age) : null,
        personal_comfort: Number(data.personal_comfort),
        occupancy: data.occupancy,
        appliances,
      };

      // Remove null hvac_age
      if (!payload.hvac_age) delete payload.hvac_age;

      const result = await Backend.saveHouse(user.username, payload);
      setSuccess(result.file || "saved");
      setPage(5);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setSubmitting(false);
    }
  }, [data, appliances]);

  // Navigation handlers
  const goToPage = useCallback((pageNum) => setPage(pageNum), []);

  // Render page content based on current page
  const renderPageContent = () => {
    switch (page) {
      case 1:
        return (
          <Page1
            data={data}
            updateField={updateField}
            onNext={() => goToPage(2)}
          />
        );
      case 2:
        return (
          <Page2
            data={data}
            updateField={updateField}
            onBack={() => goToPage(1)}
            onNext={() => goToPage(3)}
            error={error}
          />
        );
      case 3:
        return (
          <Page3
            appliances={appliances}
            options={APPLIANCE_OPTIONS}
            onToggle={toggleAppliance}
            onBack={() => goToPage(2)}
            onNext={() => goToPage(4)}
          />
        );
      case 4:
        return (
          <Page4
            data={data}
            updateField={updateField}
            onBack={() => goToPage(3)}
            onSubmit={handleSubmit}
            submitting={submitting}
            error={error}
          />
        );
      case 5:
        return <SuccessPage success={success} onClose={onClose} />;
      default:
        return null;
    }
  };

  return (
    <div className="hf-backdrop" onClick={onClose}>
      <div className="hf-card" onClick={(e) => e.stopPropagation()}>
        <div className="hf-header">
          <h3>House Variables</h3>
          <button className="hf-close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>

        <div className="hf-body">
          {page < 5 && (
            <StepIndicator
              currentPage={page}
              totalPages={TOTAL_PAGES}
              onPageClick={goToPage}
            />
          )}
          {renderPageContent()}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// Sub-components
// ============================================================

const StepIndicator = ({ currentPage, totalPages, onPageClick }) => (
  <div className="hf-step-indicator">
    {Array.from({ length: totalPages }, (_, i) => (
      <button
        key={i + 1}
        className={`hf-step ${currentPage === i + 1 ? "hf-step-active" : ""}`}
        onClick={() => onPageClick(i + 1)}
      >
        {i + 1}
      </button>
    ))}
  </div>
);

const FormField = ({ label, children }) => (
  <div className="hf-field">
    <label className="hf-label">{label}</label>
    {children}
  </div>
);

const SelectField = ({ label, value, options, onChange }) => (
  <FormField label={label}>
    <select
      className="hf-input"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  </FormField>
);

const NumberField = ({ label, value, onChange, placeholder, min = 0 }) => (
  <FormField label={label}>
    <input
      className="hf-input"
      type="number"
      min={min}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
    />
  </FormField>
);

const FormControls = ({
  onBack,
  onNext,
  onSubmit,
  submitting,
  nextLabel = "Next",
}) => (
  <div className="hf-controls">
    {onBack && (
      <button className="hf-btn hf-btn-ghost" onClick={onBack}>
        Back
      </button>
    )}
    {onNext && (
      <button className="hf-btn hf-btn-primary" onClick={onNext}>
        {nextLabel}
      </button>
    )}
    {onSubmit && (
      <button
        className="hf-btn hf-btn-primary"
        onClick={onSubmit}
        disabled={submitting}
      >
        {submitting ? "Submitting..." : "Save"}
      </button>
    )}
  </div>
);

const ErrorMessage = ({ error }) =>
  error && <div className="hf-error">Error: {error}</div>;

// Page Components
const Page1 = ({ data, updateField, onNext }) => (
  <>
    <NumberField
      label="Home — Square Feet"
      value={data.home_size}
      onChange={(v) => updateField("home_size", v)}
      placeholder="Enter square feet"
    />
    <NumberField
      label="Age of House (years)"
      value={data.age_of_house}
      onChange={(v) => updateField("age_of_house", v)}
      placeholder="Enter age of house"
    />
    <SelectField
      label="Insulation Quality"
      value={data.insulation_quality}
      options={INSULATION_OPTIONS}
      onChange={(v) => updateField("insulation_quality", v)}
    />
    <FormControls onNext={onNext} />
  </>
);

const Page2 = ({ data, updateField, onBack, onNext, error }) => (
  <>
    <SelectField
      label="HVAC Type"
      value={data.hvac_type}
      options={HVAC_OPTIONS}
      onChange={(v) => updateField("hvac_type", v)}
    />
    <NumberField
      label="HVAC Age (years)"
      value={data.hvac_age}
      onChange={(v) => updateField("hvac_age", v)}
    />
    <FormControls onBack={onBack} onNext={onNext} />
    <ErrorMessage error={error} />
  </>
);

const Page3 = ({ appliances, options, onToggle, onBack, onNext }) => (
  <>
    <FormField label="Appliances at home">
      <div className="hf-checkbox-group">
        {options.map((appliance) => (
          <label key={appliance} className="hf-checkbox-label">
            <input
              type="checkbox"
              checked={appliances.includes(appliance)}
              onChange={(e) => onToggle(appliance, e.target.checked)}
            />
            {appliance}
          </label>
        ))}
      </div>
    </FormField>
    <FormControls onBack={onBack} onNext={onNext} />
  </>
);

const Page4 = ({ data, updateField, onBack, onSubmit, submitting, error }) => (
  <>
    <FormField label="Personal Comfort (°C)">
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <input
          type="range"
          min="0"
          max="100"
          value={data.personal_comfort}
          onChange={(e) => updateField("personal_comfort", e.target.value)}
        />
        <span style={{ minWidth: 36 }}>{data.personal_comfort}°C</span>
      </div>
    </FormField>
    <SelectField
      label="Occupancy"
      value={data.occupancy}
      options={OCCUPANCY_OPTIONS}
      onChange={(v) => updateField("occupancy", v)}
    />
    <FormControls onBack={onBack} onSubmit={onSubmit} submitting={submitting} />
    <ErrorMessage error={error} />
  </>
);

const SuccessPage = ({ success, onClose }) => (
  <div className="hf-success">
    <h4>Saved!</h4>
    <div>
      Database: <code>{success}</code>
    </div>
    <button
      className="hf-btn hf-btn-primary"
      style={{ marginTop: 12 }}
      onClick={onClose}
    >
      Close
    </button>
  </div>
);

// ============================================================
// HouseFormTrigger Component
// ============================================================

export function HouseFormTrigger() {
  const [show, setShow] = useState(false);

  const buttonStyle = {
    background: "linear-gradient(90deg, #ff7a18, #ffb347)",
    color: "white",
    border: "none",
    padding: "10px 16px",
    borderRadius: 10,
    cursor: "pointer",
    boxShadow: "0 6px 18px rgba(0, 0, 0, 0.12)",
  };

  return (
    <>
      <button onClick={() => setShow(true)} style={buttonStyle}>
        Enter house variables
      </button>
      {show && <HouseForm onClose={() => setShow(false)} />}
    </>
  );
}
