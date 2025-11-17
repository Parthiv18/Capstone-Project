from pathlib import Path
import sqlite3
import json
import re
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import math


# ============================
#  ACADEMIC HVAC FORMULAS REFERENCE
# ============================
"""
INTEGRATED ASHRAE & EnergyPlus STANDARDS

1. ZONE HEAT-BALANCE (lumped thermal mass):
   C * dTz/dt = ΣTi-Tz/Rzi + Qsolar + Qinternal + ṁvent*cp*(Tout-Tz) + QHVAC
   
   Where:
   - C = thermal capacitance of zone (J/K)
   - Rzi = thermal resistances (K/W)
   - ṁvent = ventilation mass flow (kg/s)
   - cp = specific heat of air (~1005 J/kg·K)
   - QHVAC = HVAC delivered heat (W)

2. THERMAL RESISTANCE (1D steady-state, Fourier):
   Q = U*A*ΔT where U = k/L
   - k = thermal conductivity (W/m·K)
   - L = layer thickness (m)
   - A = surface area (m²)

3. RC REDUCED-ORDER MODEL (1R1C/2R2C):
   Discrete: T[k+1] = (1 - Δt/RC)*T[k] + (Δt/RC)*T_ext + Q[k]
   Fast simulation for control loops without high-fidelity solvers.

4. CONVECTIVE HEAT TRANSFER (surface):
   Qc = h*A*(Ts - T∞)
   - h = convective coefficient (W/m²·K) [empirical or correlation]

5. INFILTRATION/AIR-EXCHANGE:
   Qinf = ṁinf*cp*(Tout - Tin)
   where ṁinf = ρ*ACH*V/3600 (kg/s)

6. COP (Coefficient of Performance) - realistic part-load:
   For cooling: COP = Qremoved / Winput
   For heating: COP = Qsupplied / Winput
   Use part-load curves; COP varies with outdoor/indoor temps.

7. PSYCHROMETRICS (humidity):
   Saturation vapor pressure (Magnus formula):
   Psat = 610.5 * exp(17.27*T/(T+237.7)) [Pa]
   Humidity Ratio: ω = 0.622*(Pvapor/P_atm - Pvapor)

8. COMFORT (PMV/PPD - Fanger's Model):
   PMV depends on: Ta, Tr (radiant), v (velocity), humidity, M (metabolic), Icl (clothing)
   PPD = 100 - 95*exp(-0.03353*PMV^4 - 0.2179*PMV^2)
   [ASHRAE 55, ISO 7730]
   
9. MPC OBJECTIVE (energy vs comfort):
   J = Σ(α*E(uk) + β*comfort_penalty(xk))
   where E(uk) = Qhvac / COP (energy consumption)
         comfort_penalty = deviation from target + humidity control
"""


# ============================
#  DATABASE PATH
# ============================
def get_db_path() -> Path:
    base = Path(__file__).resolve().parent.parent
    db_path = base / "weather-app" / "database" / "users.db"
    return db_path


def fetch_all_users(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT username, user_weather, user_house FROM users")
    rows = cur.fetchall()
    conn.close()
    return rows


# ============================
#  HOUSE PARSER
# ============================
def parse_house(text: str) -> dict:
    if not text:
        return {}

    # Accept JSON input
    try:
        if text.strip().startswith("{"):
            return json.loads(text)
    except:
        pass

    # Accept "key: value" text format
    d = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", v):
            v = float(v)
        d[k] = v
    return d


# ============================
#  WEATHER PARSER
# ============================
def parse_weather(text: str) -> pd.DataFrame:
    if not text:
        return pd.DataFrame()

    try:
        obj = json.loads(text)
        rows = obj.get("rows", obj if isinstance(obj, list) else None)

        parsed = []
        if rows:
            for r in rows:
                dt = r.get("date")
                if not dt:
                    continue
                try:
                    dt = datetime.fromisoformat(dt.replace(" EST", ""))
                except:
                    continue

                parsed.append({
                    "date": dt,
                    "temp": float(r.get("temperature_2m", 0)),
                    "apparent": float(r.get("apparent_temperature", 0)),
                    "humidity": float(r.get("humidity_2m", 50)),
                    "solar": float(r.get("solar_radiation", 0)),
                    "windspeed_10m": float(r.get("windspeed_10m", 0)),
                    "dew_point_2m": float(r.get("dew_point_2m", 0)),
                    "precipitation": float(r.get("precipitation", 0)),
                    "rain": float(r.get("rain", 0)),
                    "snowfall": float(r.get("snowfall", 0)),
                })

        return pd.DataFrame(parsed)

    except Exception:
        return pd.DataFrame()


# ============================
#  ACADEMIC HVAC HELPER FUNCTIONS
# ============================

def calculate_saturation_vapor_pressure(T_celsius: float) -> float:
    """
    Magnus formula for saturation vapor pressure.
    ASHRAE Fundamentals - Psychrometrics
    
    Psat = 610.5 * exp(17.27*T / (T+237.7)) [Pa]
    Clamps temperature to reasonable range (-50 to 70°C) to prevent overflow.
    """
    try:
        # Clamp temperature to prevent exponential overflow
        T_safe = max(-50.0, min(70.0, T_celsius))
        exponent = (17.27 * T_safe) / (T_safe + 237.7)
        # Further clamp exponent to prevent overflow
        exponent = max(-100, min(50, exponent))
        return 610.5 * math.exp(exponent)
    except (OverflowError, ValueError):
        return 610.5 if T_celsius >= 0 else 100.0


def calculate_humidity_ratio(relative_humidity: float, T_celsius: float, P_atm: float = 101325) -> float:
    """
    Humidity ratio (kg_water/kg_dry_air) from RH and temperature.
    ASHRAE Fundamentals - Psychrometrics
    
    ω = 0.622 * (P_vapor / (P_atm - P_vapor))
    where P_vapor = RH/100 * P_sat(T)
    """
    try:
        Psat = calculate_saturation_vapor_pressure(T_celsius)
        Pvapor = (relative_humidity / 100.0) * Psat
        omega = 0.622 * Pvapor / (P_atm - Pvapor)
        return max(0, omega)
    except:
        return 0.01  # default


def calculate_operative_temperature(T_air: float, T_radiant: float, 
                                   h_r: float = 4.7, h_c: float = 3.5) -> float:
    """
    Operative temperature (comfort reference).
    ASHRAE 55 - Thermal Comfort
    
    T_op = (h_r*T_r + h_c*T_a) / (h_r + h_c)
    
    where h_r, h_c are radiative and convective coefficients (W/m²·K)
    Defaults: h_r=4.7, h_c=3.5 (typical for indoor environments)
    """
    if h_r + h_c == 0:
        return (T_air + T_radiant) / 2
    return (h_r * T_radiant + h_c * T_air) / (h_r + h_c)


def calculate_pmv_ppd(T_air: float, T_radiant: float, v_air: float,
                     rh: float, M_met: float = 1.1, Icl_clo: float = 0.6) -> dict:
    """
    Fanger's PMV (Predicted Mean Vote) and PPD (Predicted Percentage Dissatisfied).
    ASHRAE 55, ISO 7730
    
    Simplified implementation with numerical stability safeguards.
    
    Args:
        T_air: air temperature (°C)
        T_radiant: mean radiant temperature (°C)
        v_air: air velocity (m/s), minimum 0.1 m/s
        rh: relative humidity (%)
        M_met: metabolic rate (met): 1.0=sedentary, 1.2=light, 1.5=moderate
        Icl_clo: clothing insulation (clo): 0.5=light, 1.0=typical, 1.5=heavy
    
    Returns:
        dict with 'PMV', 'PPD', 'comfort_band' (good/acceptable/poor)
    """
    try:
        # Input validation and clamping
        T_air = max(-50.0, min(70.0, T_air))
        T_radiant = max(-50.0, min(70.0, T_radiant))
        v_air = max(0.1, min(5.0, v_air))
        rh = max(10.0, min(95.0, rh))
        M_met = max(0.8, min(2.0, M_met))
        Icl_clo = max(0.0, min(2.0, Icl_clo))
        
        # Operative temperature (weighted average with safeguards)
        T_op = calculate_operative_temperature(T_air, T_radiant)
        T_op = max(-50.0, min(70.0, T_op))
        
        # Prevent division by zero in clothing calculation
        if abs(T_op - T_air) < 0.01:
            T_op = T_air + 0.5
        
        # Psychrometric data
        pa = (rh / 100.0) * calculate_saturation_vapor_pressure(T_air)
        pa = max(0, pa)
        
        # Metabolic rate (W/m²)
        M = M_met * 58.15
        
        # Clothing surface area factor
        Icl = Icl_clo * 0.155
        if Icl < 0.078:
            fcl = 1.0 + 1.29 * Icl
        else:
            fcl = 1.0 + 1.05 * Icl
        fcl = max(1.0, min(2.5, fcl))
        
        # Convective heat transfer coefficient
        hcv = 5.0 if v_air < 1.0 else 5.0 + 2.5 * (v_air - 1.0)
        hcv = max(3.0, min(50.0, hcv))
        
        # Simplified PMV calculation (avoid full Fanger iteration)
        # Using practical approximation based on metabolic rate and comfort deviation
        metabolic_load = M - 58.15
        comfort_deviation = abs(T_op - 22.0)  # 22°C is neutral point
        humidity_effect = (rh - 50.0) / 50.0  # normalized humidity deviation
        
        # Simplified PMV (prevents extreme values)
        PMV = (0.303 * math.exp(-0.036 * M) + 0.028 * metabolic_load) \
              + 0.2 * comfort_deviation * (1.0 if T_op < 22.0 else -1.0) \
              + 0.05 * humidity_effect
        
        # Clamp PMV to realistic range
        PMV = max(-3.0, min(3.0, PMV))
        
        # PPD from PMV formula
        ppd_exp = -0.03353 * (PMV ** 4) - 0.2179 * (PMV ** 2)
        ppd_exp = max(-100, min(50, ppd_exp))  # clamp exponent
        PPD = 100.0 - 95.0 * math.exp(ppd_exp)
        PPD = min(100.0, max(0.0, PPD))
        
        # Comfort band classification
        if abs(PMV) < 0.5 and PPD < 10:
            comfort_band = "good"
        elif abs(PMV) < 1.0 and PPD < 25:
            comfort_band = "acceptable"
        else:
            comfort_band = "poor"
        
        return {
            "PMV": round(PMV, 3),
            "PPD": round(PPD, 1),
            "comfort_band": comfort_band,
            "operative_temp": round(T_op, 2)
        }
    except Exception as e:
        return {
            "PMV": 0.0,
            "PPD": 50.0,
            "comfort_band": "unknown",
            "operative_temp": (T_air + T_radiant) / 2,
            "error": str(e)
        }


def calculate_cop_cooling(T_outdoor: float, T_indoor: float, part_load_ratio: float = 1.0,
                          hvac_age: float = 5) -> float:
    """
    COP for cooling (realistic part-load behavior).
    Based on ASHRAE equipment curves.
    
    COP_cooling = baseline * f_T * f_partload * f_age
    
    Args:
        T_outdoor: outdoor dry-bulb (°C)
        T_indoor: indoor set point (°C)
        part_load_ratio: 0-1, fraction of max capacity
        hvac_age: age of HVAC system (years)
    
    Returns:
        COP value (efficiency)
    """
    try:
        # Baseline COP at reference conditions (35°C outdoor, 24°C indoor)
        cop_ref = 3.0
        
        # Temperature correction (EER/COP increases at lower outdoor, decreases at higher indoor)
        deltaT = T_outdoor - T_indoor
        T_effect = 1.0 - 0.02 * max(0, deltaT - 15)  # degrades ~2% per °C above 15°C difference
        
        # Part-load penalty: max COP at ~75% load, degrades outside
        if 0.5 <= part_load_ratio <= 1.0:
            pl_effect = 0.9 + 0.4 * (1.0 - abs(part_load_ratio - 0.75) / 0.25)
        else:
            pl_effect = 0.7 + 0.3 * part_load_ratio / 0.5
        
        # Age degradation (1% per year typical)
        age_effect = max(0.7, 1.0 - 0.01 * hvac_age)
        
        cop = cop_ref * T_effect * pl_effect * age_effect
        return max(1.5, min(4.5, cop))
    except:
        return 3.0


def calculate_cop_heating(T_outdoor: float, T_indoor: float, part_load_ratio: float = 1.0,
                         hvac_age: float = 5, is_heatpump: bool = False) -> float:
    """
    COP for heating.
    For gas furnace: efficiency ~0.90. For heat pump: COP ~2.5-3.5 depending on outdoor temp.
    
    Args:
        T_outdoor: outdoor dry-bulb (°C)
        T_indoor: indoor set point (°C)
        part_load_ratio: 0-1, fraction of max capacity
        hvac_age: age of system (years)
        is_heatpump: True for heat pump, False for furnace/resistance
    """
    try:
        if is_heatpump:
            # Heat pump COP degrades sharply at cold temps
            cop_ref = 3.0  # at 0°C outdoor
            
            # Outdoor temp effect (COP drops ~5% per 5°C below 0°C)
            if T_outdoor < 0:
                T_effect = 1.0 - 0.01 * (0 - T_outdoor) / 1.0  # rapid drop below freezing
            else:
                T_effect = 1.0 + 0.02 * T_outdoor
            
            # Part-load penalty
            pl_effect = 0.85 + 0.3 * part_load_ratio
            
            # Age degradation
            age_effect = max(0.65, 1.0 - 0.01 * hvac_age)
            
            cop = cop_ref * T_effect * pl_effect * age_effect
            return max(1.2, min(4.0, cop))
        else:
            # Furnace or resistance heating (efficiency only, not COP)
            # COP_furnace ≈ 0.95 (95% AFUE typical)
            # Simplified as 1 / efficiency
            age_effect = max(0.75, 1.0 - 0.005 * hvac_age)  # slow degradation
            efficiency = 0.90 * age_effect
            return 1.0 / efficiency if efficiency > 0 else 1.1
    except:
        return 3.0 if is_heatpump else 1.1


def calculate_infiltration_load(T_outdoor: float, T_indoor: float, volume_m3: float,
                               ACH_per_hour: float = 0.5, wind_speed: float = 0) -> float:
    """
    Infiltration/air-exchange heat load.
    ASHRAE - Infiltration calculation
    
    Qinf = ṁinf * cp * (Tout - Tin)
    where ṁinf = ρ * ACH * V / 3600
    
    Args:
        T_outdoor, T_indoor: temperatures (°C)
        volume_m3: room/zone volume (m³)
        ACH_per_hour: air changes per hour (0.3-1.5 typical)
        wind_speed: wind speed (m/s) [increases infiltration]
    
    Returns:
        Heat load (W) [negative = cooling load, positive = heating load]
    """
    try:
        # Air density at sea level, ~20°C
        rho = 1.2  # kg/m³
        cp = 1005  # J/kg·K
        
        # Wind effect on ACH (typical correlation)
        ACH_effective = ACH_per_hour + 0.02 * wind_speed
        
        # Mass flow rate (kg/s)
        mdot_inf = rho * ACH_effective * volume_m3 / 3600.0
        
        # Heat load
        Q_inf = mdot_inf * cp * (T_outdoor - T_indoor)
        return Q_inf
    except:
        return 0


def calculate_solar_gain(solar_radiation: float, window_area: float, SHGC: float = 0.6,
                        solar_multiplier: float = 1.0) -> float:
    """
    Solar heat gain through windows.
    ASHRAE - Solar radiation
    
    Qsolar = SR * A * SHGC
    
    Args:
        solar_radiation: W/m² (global horizontal irradiance)
        window_area: m²
        SHGC: Solar Heat Gain Coefficient (0.3-0.8 typical)
        solar_multiplier: orientation/shading factor (0-1)
    
    Returns:
        Heat gain (W)
    """
    try:
        return max(0, solar_radiation * window_area * SHGC * solar_multiplier)
    except:
        return 0


def calculate_zone_heat_balance_RC(T_zone_prev: float, T_outdoor: float, T_radiant: float,
                                  Q_internal: float, Q_solar: float, Q_hvac: float,
                                  Q_inf: float, dt_hour: float,
                                  C_thermal: float = 1e6, R_total: float = 0.01) -> float:
    """
    Simplified RC (resistance-capacitance) zone heat balance.
    ASHRAE/EnergyPlus Zone Heat Balance - discrete time.
    
    T_zone[k+1] = (1 - dt/RC) * T_zone[k] + (dt/RC) * T_eff + (dt/C) * Q_total
    
    Includes numerical safeguards to prevent overflow and NaN.
    
    Args:
        T_zone_prev: previous zone temperature (°C)
        T_outdoor: outdoor air temperature (°C)
        T_radiant: radiant temperature (°C)
        Q_internal: internal heat gains (W)
        Q_solar: solar gains (W)
        Q_hvac: HVAC delivered heat (W)
        Q_inf: infiltration load (W)
        dt_hour: time step (hours, typically 1)
        C_thermal: zone thermal capacitance (J/K), default ~1 MJ/K
        R_total: total thermal resistance (K/W), default 0.01
    
    Returns:
        New zone temperature (°C)
    """
    try:
        # Input validation and clamping
        T_zone_prev = max(-50.0, min(70.0, T_zone_prev))
        T_outdoor = max(-50.0, min(70.0, T_outdoor))
        T_radiant = max(-50.0, min(70.0, T_radiant))
        dt_hour = max(0.01, min(24.0, dt_hour))
        
        # Clamp capacitance and resistance to reasonable ranges
        # C_thermal: 0.1 MJ/K to 10 MJ/K (typical 0.5 MJ/K)
        C_thermal = max(0.1e6, min(10e6, C_thermal))
        # R_total: 0.001 to 0.1 K/W (typical 0.01)
        R_total = max(0.001, min(0.1, R_total))
        
        dt_seconds = dt_hour * 3600.0
        # Time constant (seconds) = R (K/W) * C (J/K)
        # Do NOT divide by 3600 here — R*C already yields seconds when C is J/K and R is K/W.
        RC_const = R_total * C_thermal
        
        # Ensure RC constant is positive and reasonable
        RC_const = max(100.0, min(100000.0, RC_const))
        
        # Time constant ratio (prevent extreme weighting)
        alpha = dt_seconds / RC_const
        alpha = max(0.001, min(1.0, alpha))  # 0.1% to 100% per hour
        
        # Effective driving temperature (weighted average)
        T_eff = 0.5 * T_outdoor + 0.3 * T_radiant + 0.2 * T_zone_prev
        T_eff = max(-50.0, min(70.0, T_eff))
        
        # Total heat input (W)
        Q_total = Q_internal + Q_solar + Q_hvac + Q_inf
        Q_total = max(-100000, min(100000, Q_total))  # clamp to realistic range
        
        # Heat contribution (ΔT from heat)
        dT_heat = (dt_seconds / C_thermal) * Q_total
        dT_heat = max(-50.0, min(50.0, dT_heat))  # cap temperature change per hour
        
        # RC model update with safeguards
        T_zone_new = (1.0 - alpha) * T_zone_prev \
                     + alpha * T_eff \
                     + dT_heat
        
        # Final clamp to reasonable range
        T_zone_new = max(-50.0, min(70.0, T_zone_new))
        
        return T_zone_new
    except (OverflowError, ValueError, ZeroDivisionError):
        # Return previous temperature if calculation fails
        return max(-50.0, min(70.0, T_zone_prev))


def calculate_mpc_cost(Q_hvac: float, COP: float, T_zone: float, T_desired: float,
                      rh: float, rh_desired: float = 45.0,
                      alpha: float = 0.7, beta: float = 0.3) -> float:
    """
    MPC-inspired cost function: balance energy consumption vs comfort.
    
    J = α * E_consumption + β * comfort_penalty
    
    where E = Q_hvac / COP (Wh)
          comfort_penalty = temp_dev² + humidity_dev² (normalized)
    
    Args:
        Q_hvac: HVAC load (W)
        COP: coefficient of performance
        T_zone: current zone temperature (°C)
        T_desired: desired setpoint (°C)
        rh: current relative humidity (%)
        rh_desired: target humidity (%)
        alpha, beta: weights (0-1, sum ~1)
    
    Returns:
        Cost value (lower is better)
    """
    try:
        # Energy cost (normalized to 0-10 scale)
        E_consumption = abs(Q_hvac) / COP if COP > 0 else 0  # Wh per hour
        E_cost = min(10, E_consumption / 5000)  # 5 kW baseline
        
        # Comfort penalty
        temp_dev = abs(T_zone - T_desired) / 2.0  # normalize to ~1°C = 0.5
        humidity_dev = abs(rh - rh_desired) / 50.0  # normalize to 50% RH = 1
        
        comfort_penalty = min(10, temp_dev + 0.5 * humidity_dev)
        
        # Combined cost
        J = alpha * E_cost + beta * comfort_penalty
        return J
    except:
        return 10.0


# ============================
#  PREDICTIVE HVAC MODEL (Academic Integration)
# ============================
def compute_hvac_intervals(df: pd.DataFrame, house_vars: dict, merge: bool = True) -> pd.DataFrame:
    """
    Compute realistic HVAC schedules using academic formulas.
    Integrates ASHRAE zone heat balance, COP curves, psychrometrics, PMV/PPD comfort,
    and MPC-inspired cost optimization.
    """
    if df.empty:
        return pd.DataFrame(columns=["date", "time on", "time off", "current_temp", "hvac temp set", "status", "energy_kwh", "pmv", "ppd", "comfort"])

    df = df.sort_values("date").reset_index(drop=True)

    # ======================================
    # BUILDING PARAMETERS (from user input)
    # ======================================
    desired_temp = float(house_vars.get("personal_comfort", 22))
    desired_temp = max(15.0, min(28.0, desired_temp))  # clamp comfort temp
    
    # Thermal properties
    insulation_map = {"poor": 1.6, "average": 1.0, "good": 0.75, "excellent": 0.55}
    insulation = insulation_map.get(str(house_vars.get("insulation_quality", "average")).lower(), 1.0)
    insulation = max(0.55, min(1.6, insulation))
    
    home_size_m2 = float(house_vars.get("home_size", 100))
    home_size_m2 = max(50.0, min(500.0, home_size_m2))  # clamp home size
    home_volume_m3 = home_size_m2 * 2.8  # typical ceiling height
    
    # HVAC system properties
    hvac_age = float(house_vars.get("hvac_age", 5))
    hvac_age = max(0, min(20, hvac_age))  # clamp to 0-20 years
    is_heatpump = str(house_vars.get("hvac_type", "furnace")).lower() == "heat_pump"
    
    # RC thermal model parameters with realistic ranges
    # Thermal capacitance: 0.3-0.8 MJ/K for typical home
    # Normalized to 100 m² baseline = 0.5 MJ/K
    C_thermal = (home_size_m2 / 100.0) * 0.5e6 * (1.0 / insulation)
    C_thermal = max(0.1e6, min(5.0e6, C_thermal))  # clamp to 0.1-5 MJ/K
    
    # Total thermal resistance: 0.005-0.05 K/W for typical envelope
    R_total = 0.01 * (insulation / 1.0) * (100.0 / home_size_m2)
    R_total = max(0.001, min(0.1, R_total))  # clamp to 0.001-0.1 K/W
    
    # Window/infiltration properties
    window_area_m2 = float(house_vars.get("window_area", home_size_m2 * 0.15))
    window_area_m2 = max(5.0, min(home_size_m2 * 0.3, window_area_m2))  # clamp to 5-30% of floor area
    SHGC = float(house_vars.get("SHGC", 0.6))
    SHGC = max(0.2, min(0.8, SHGC))
    ACH = float(house_vars.get("infiltration_rate", 0.5))  # air changes per hour
    ACH = max(0.3, min(1.5, ACH))  # clamp to 0.3-1.5 ACH
    
    # Occupancy and comfort
    occupancy = str(house_vars.get("occupancy", "home_daytime")).lower()
    
    # Occupancy comfort bands
    occupancy_band = {"home_daytime": 0.65, "away_daytime": 1.3, "night": 1.1}.get(occupancy, 1.0)
    
    # Metabolic rate and clothing for PMV (ASHRAE)
    metabolic_rate_met = {"sedentary": 1.0, "light": 1.2, "moderate": 1.5}.get(
        str(house_vars.get("activity_level", "light")).lower(), 1.2
    )
    clothing_clo = {"light": 0.5, "normal": 0.6, "heavy": 1.0}.get(
        str(house_vars.get("clothing_level", "normal")).lower(), 0.6
    )
    
    # Humidity target
    rh_desired = float(house_vars.get("target_humidity", 45.0))
    
    # MPC weights (α + β ≈ 1)
    alpha_energy = 0.65  # energy weight
    beta_comfort = 0.35  # comfort weight
    
    # ======================================
    # INITIALIZE STATE TRACKING
    # ======================================
    current_mode = "off"
    T_zone = desired_temp  # zone temperature state
    hvac_setpoints = []
    hvac_statuses = []
    energy_consumption = []  # kWh per hour
    pmv_list = []
    ppd_list = []
    comfort_list = []
    outdoor_temps = []  # track outdoor temperature from weather data
    
    # ======================================
    # MAIN HOURLY SIMULATION LOOP
    # ======================================
    for i in range(len(df)):
        row = df.loc[i]
        
        # Weather data
        T_outdoor = row["temp"]
        T_apparent = row["apparent"]
        humidity = row["humidity"]
        dew_point = row["dew_point_2m"]
        solar_radiation = row["solar"]
        wind_speed = row["windspeed_10m"]
        precipitation = row["precipitation"]
        
        # ======================
        # 1) CALCULATE LOADS
        # ======================
        
        # Infiltration load (ASHRAE)
        Q_inf = calculate_infiltration_load(
            T_outdoor, T_zone, home_volume_m3, ACH, wind_speed
        )
        
        # Solar gain (ASHRAE)
        solar_multiplier = 0.7 if 150 < solar_radiation < 400 else \
                          0.9 if solar_radiation >= 400 else 0.3
        Q_solar = calculate_solar_gain(
            solar_radiation, window_area_m2, SHGC, solar_multiplier
        )
        
        # Internal gains (people, equipment, lights)
        # Simplified: occupancy-dependent
        if occupancy == "away_daytime":
            Q_internal = 200  # W (minimal)
        elif occupancy == "night":
            Q_internal = 150  # W (sleeping occupants + standby)
        else:
            Q_internal = 500  # W (daytime activity)
        
        # Radiant temperature (simplified: average of outdoor + indoor)
        T_radiant = 0.4 * T_outdoor + 0.6 * T_zone
        
        # ======================
        # 2) PSYCHROMETRIC CALCS
        # ======================
        omega = calculate_humidity_ratio(humidity, T_outdoor)
        
        # ======================
        # 3) HVAC MODE DECISION & SETPOINT
        # ======================
        
        # Predictive trend (look-ahead 12h)
        T_now = T_zone
        T_1h = df["temp"].iloc[i + 1] if i + 1 < len(df) else T_outdoor
        T_3h = df["temp"].iloc[i + 3] if i + 3 < len(df) else T_outdoor
        T_6h = df["temp"].iloc[i + 6] if i + 6 < len(df) else T_outdoor
        T_12h = df["temp"].iloc[i + 12] if i + 12 < len(df) else T_outdoor
        
        trend = (
            0.45 * (T_1h - T_now) +
            0.30 * (T_3h - T_now) +
            0.15 * (T_6h - T_now) +
            0.10 * (T_12h - T_now)
        )
        
        # Comfort band by time of day
        hour = row["date"].hour
        if 6 <= hour < 10:
            comfort_band = 0.55 * occupancy_band
        elif 10 <= hour < 22:
            comfort_band = 1.0 * occupancy_band
        else:
            comfort_band = 1.4 * occupancy_band
        
        # Hysteresis (prevent cycling)
        hysteresis = comfort_band * 0.45
        
        # Mode transition logic
        if current_mode == "heating":
            if T_zone >= desired_temp - (comfort_band - hysteresis):
                current_mode = "off"
        elif current_mode == "cooling":
            if T_zone <= desired_temp + (comfort_band - hysteresis):
                current_mode = "off"
        else:
            if T_zone <= desired_temp - comfort_band:
                current_mode = "heating"
            elif T_zone >= desired_temp + comfort_band:
                current_mode = "cooling"
            else:
                current_mode = "off"
        
        # HVAC setpoint calculation
        setpoint = ""
        Q_hvac = 0
        COP_effective = 1.0
        
        if current_mode == "heating":
            # COP for heating
            part_load = min(1.0, max(0.5, abs(T_zone - desired_temp) / 5.0))
            COP_effective = calculate_cop_heating(
                T_outdoor, desired_temp, part_load, hvac_age, is_heatpump
            )
            
            # Setpoint with weather modulation
            setpoint = desired_temp - 0.3 - 0.02 * wind_speed - 0.15 * trend
            if humidity > 75:
                setpoint -= 0.1  # reduce heating if humid
            
            # Smart setpoint limits
            max_heat = 23 + max(0, (10 - T_outdoor) * 0.10)
            max_heat = min(25.5, max_heat)
            max_heat += min(1.0, wind_speed * 0.02 + precipitation * 0.15)
            
            setpoint = min(max_heat, max(desired_temp - 3, setpoint))
            
            # Calculate HVAC load needed
            Q_hvac = (desired_temp - T_zone) * home_size_m2 * 50  # rough load calc
            Q_hvac = max(0, Q_hvac - Q_solar - Q_internal)
        
        elif current_mode == "cooling":
            # COP for cooling
            part_load = min(1.0, max(0.5, abs(T_zone - desired_temp) / 5.0))
            COP_effective = calculate_cop_cooling(
                T_outdoor, desired_temp, part_load, hvac_age
            )
            
            # Setpoint with weather modulation
            setpoint = desired_temp + 0.6 - 0.12 * trend
            if humidity > 70:
                setpoint += 0.2  # increase cooling setpoint if humid (dehumidify)
            if solar_radiation > 150:
                setpoint += (solar_radiation / 400) * 0.35
            
            # Smart setpoint limits
            min_cool = max(18, 20 - max(0, (T_apparent - 25) * 0.08))
            setpoint = max(min_cool, min(26, setpoint))
            
            # Calculate HVAC load needed
            Q_hvac = (T_zone - desired_temp) * home_size_m2 * 50
            Q_hvac = max(0, Q_hvac - Q_solar - Q_internal)
        
        # ======================
        # 4) RC ZONE HEAT BALANCE
        # ======================
        T_zone = calculate_zone_heat_balance_RC(
            T_zone, T_outdoor, T_radiant,
            Q_internal, Q_solar, Q_hvac if current_mode != "off" else 0,
            Q_inf, dt_hour=1.0,
            C_thermal=C_thermal, R_total=R_total
        )
        
        # ======================
        # 5) HVAC ON/OFF DECISION (MPC-based)
        # ======================
        hvac_on = False
        
        if current_mode != "off":
            # MPC cost function
            cost_on = calculate_mpc_cost(
                Q_hvac, COP_effective, T_zone, desired_temp,
                humidity, rh_desired, alpha_energy, beta_comfort
            )
            cost_off = calculate_mpc_cost(
                0, 1.0, T_zone, desired_temp,
                humidity, rh_desired, alpha_energy, beta_comfort
            )
            
            # Turn on if cost reduction > threshold
            hvac_on = cost_on < cost_off * 0.85
        
        hvac_status = "on" if hvac_on else "off"
        
        # ======================
        # 6) COMFORT METRICS (PMV/PPD)
        # ======================
        T_op = calculate_operative_temperature(T_zone, T_radiant)
        
        comfort_result = calculate_pmv_ppd(
            T_zone, T_radiant, max(0.1, wind_speed * 0.3),  # indoor air velocity
            humidity, metabolic_rate_met, clothing_clo
        )
        
        pmv = comfort_result.get("PMV", 0)
        ppd = comfort_result.get("PPD", 50)
        comfort_band_name = comfort_result.get("comfort_band", "unknown")
        
        # ======================
        # 7) ENERGY ACCOUNTING
        # ======================
        if hvac_on and COP_effective > 0:
            energy_kWh = Q_hvac / (COP_effective * 1000)  # Wh → kWh
        else:
            energy_kWh = 0
        
        # Store results
        hvac_setpoints.append(round(setpoint, 2) if isinstance(setpoint, (int, float)) else "")
        hvac_statuses.append(hvac_status)
        energy_consumption.append(round(energy_kWh, 3))
        pmv_list.append(pmv)
        ppd_list.append(ppd)
        comfort_list.append(comfort_band_name)
        outdoor_temps.append(round(T_outdoor, 2))  # store outdoor temperature
    
    # ======================================
    # BUILD HOURLY DATAFRAME
    # ======================================
    hourly = []
    for i in range(len(df)):
        dt = df.loc[i, "date"]
        end_dt = dt + timedelta(hours=1)
        sp = hvac_setpoints[i]
        
        hourly.append({
            "date": dt.date(),
            "time on": dt.strftime("%H:%M"),
            "time off": end_dt.strftime("%H:%M"),
            "current_temp": outdoor_temps[i],
            "hvac temp set": sp,
            "status": hvac_statuses[i],
            "energy_kwh": energy_consumption[i],
            "pmv": round(pmv_list[i], 2),
            "ppd": round(ppd_list[i], 1),
            "comfort": comfort_list[i],
        })
    
    hourly_df = pd.DataFrame(hourly)
    if not merge:
        return hourly_df
    
    # ======================================
    # MERGE CONSECUTIVE SAME-STATUS INTERVALS
    # ======================================
    intervals = []
    if len(hourly_df) > 0:
        start = 0
        cur = hourly_df.iloc[0]["status"]
        
        def build_interval(s, e, status):
            start_dt = df.loc[s, "date"]
            end_dt = df.loc[e, "date"] + timedelta(hours=1)
            
            # Average setpoints, energy, comfort metrics
            vals = [v for v in hvac_setpoints[s:e + 1] if isinstance(v, (int, float))]
            sp = round(sum(vals) / len(vals), 2) if vals else ""
            
            # Average outdoor temperature during interval
            avg_outdoor_temp = round(sum(outdoor_temps[s:e + 1]) / (e - s + 1), 2)
            
            total_energy = sum(energy_consumption[s:e + 1])
            avg_pmv = round(sum(pmv_list[s:e + 1]) / (e - s + 1), 2)
            avg_ppd = round(sum(ppd_list[s:e + 1]) / (e - s + 1), 1)
            
            # Most common comfort band
            comfort_bands = comfort_list[s:e + 1]
            comfort_mode = max(set(comfort_bands), key=comfort_bands.count) if comfort_bands else "unknown"
            
            return {
                "date": start_dt.date(),
                "time on": start_dt.strftime("%H:%M"),
                "time off": end_dt.strftime("%H:%M"),
                "current_temp": avg_outdoor_temp,
                "hvac temp set": sp,
                "status": status,
                "energy_kwh": round(total_energy, 3),
                "pmv": avg_pmv,
                "ppd": avg_ppd,
                "comfort": comfort_mode,
            }
        
        for i in range(1, len(hourly_df)):
            if hourly_df.iloc[i]["status"] != cur:
                intervals.append(build_interval(start, i - 1, cur))
                start = i
                cur = hourly_df.iloc[i]["status"]
        
        intervals.append(build_interval(start, len(hourly_df) - 1, cur))
    
    return pd.DataFrame(intervals) if intervals else hourly_df


# ============================
#  EXPORT TO EXCEL
# ============================
def generate_schedules():
    db_path = get_db_path()
    rows = fetch_all_users(db_path)
    output_excel = Path("model") / "hvac_schedules.xlsx"

    sheets = {}

    for username, user_weather, user_house in rows:
        house_vars = parse_house(user_house or "")
        df_weather = parse_weather(user_weather or "")

        merged = compute_hvac_intervals(df_weather, house_vars, merge=True)
        hourly = compute_hvac_intervals(df_weather, house_vars, merge=False)

        # Format dates
        def fmt(df_in):
            if not df_in.empty and "date" in df_in.columns:
                df_in = df_in.copy()
                df_in["date"] = df_in["date"].apply(lambda d: d.strftime("%m/%d/%Y"))
            return df_in

        merged = fmt(merged)
        hourly = fmt(hourly)

        # Select columns in order of importance
        desired_cols = ["date", "time on", "time off", "current_temp", "hvac temp set", "status", 
                       "energy_kwh", "pmv", "ppd", "comfort"]
        merged = merged.reindex(columns=desired_cols)
        hourly = hourly.reindex(columns=desired_cols)

        uname = str(username)[:27]
        sheets[uname] = merged if not merged.empty else pd.DataFrame(
            [{"date": "", "time on": "", "time off": "", "current_temp": 0, "hvac temp set": "", 
              "status": "", "energy_kwh": 0, "pmv": 0, "ppd": 0, "comfort": ""}]
        )
        sheets[f"{uname}_hourly"] = hourly if not hourly.empty else pd.DataFrame(
            [{"date": "", "time on": "", "time off": "", "current_temp": 0, "hvac temp set": "", 
              "status": "", "energy_kwh": 0, "pmv": 0, "ppd": 0, "comfort": ""}]
        )

    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        for s, df_s in sheets.items():
            df_s.to_excel(writer, sheet_name=s, index=False)

    return {"excel": output_excel, "sheets": list(sheets.keys())}


# ============================
#  MAIN
# ============================
if __name__ == "__main__":
    res = generate_schedules()
    print("Done. Results:", res)
