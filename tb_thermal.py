"""
tb_thermal.py — Pipeline thermal and hydrate screening for TieBack Studio.

* Steady-state temperature along a pipe with constant overall heat-transfer
  coefficient U (referenced to the inner diameter) and ambient T_a:
        dT/dx = −U·π·D / (ṁ·c_p) · (T − T_a)
  ⇒     T(x) = T_a + (T_in − T_a)·exp(−x/λ),   λ = ṁ·c_p / (U·π·D)
  Joule-Thomson cooling and frictional heating are NOT included — for gas
  lines with large pressure drop JT cooling makes the real arrival colder.
* Shut-in cool-down, lumped capacitance per unit length (fluid + steel wall):
        t = τ·ln((T0 − T_a)/(T_h − T_a)),  τ = (Σ m'·c_p) / (U·π·D)
* Hydrate formation temperature: Towler & Mokhatab (2005),
        T[°F] = 13.47 ln p + 34.27 ln γg − 1.675 ln p · ln γg − 20.35   (p psia)
  valid for sweet natural gas, γg ≈ 0.55–1.0.
* Thermodynamic inhibitor depression: Hammerschmidt (1934),
        ΔT[°F] = K·W / (M·(100 − W))
  K = 2335 (methanol), 2700 (MEG); W = wt % in the aqueous phase.

Units: SI for heat transfer (W/m²K, m, kg/s, J/kgK); °F/psia at the
hydrate-correlation boundary (converted helpers provided).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

CP_OIL = 2000.0      # J/kg·K
CP_GAS = 2300.0
CP_WATER = 4180.0
CP_STEEL = 480.0
RHO_STEEL = 7850.0

INHIBITORS = {"None": (0.0, 1.0), "Methanol": (2335.0, 32.04), "MEG": (2700.0, 62.07)}


def f_to_c(t_f):
    return (t_f - 32.0) / 1.8


def c_to_f(t_c):
    return t_c * 1.8 + 32.0


def decay_length_m(m_dot_kg_s: float, cp_j_kgk: float, u_w_m2k: float, d_m: float) -> float:
    if u_w_m2k <= 0:
        return math.inf
    if m_dot_kg_s <= 0:
        return 0.0
    return m_dot_kg_s * cp_j_kgk / (u_w_m2k * math.pi * d_m)


def temperature_at(x_m: float, t_in: float, t_amb: float, lam_m: float) -> float:
    """Steady-state temperature (any consistent T unit) at distance x."""
    if math.isinf(lam_m):
        return t_in
    if lam_m <= 0:
        return t_amb
    return t_amb + (t_in - t_amb) * math.exp(-x_m / lam_m)


def mix_temperature(streams) -> float:
    """Adiabatic mixing of (m_dot·cp, T) pairs."""
    cap = sum(c for c, _ in streams)
    if cap <= 0:
        return sum(t for _, t in streams) / max(len(streams), 1)
    return sum(c * t for c, t in streams) / cap


@dataclass
class StreamMass:
    oil_kg_s: float
    gas_kg_s: float
    water_kg_s: float

    @property
    def total(self):
        return self.oil_kg_s + self.gas_kg_s + self.water_kg_s

    @property
    def capacity_w_k(self):
        return self.oil_kg_s * CP_OIL + self.gas_kg_s * CP_GAS + self.water_kg_s * CP_WATER

    @property
    def cp(self):
        return self.capacity_w_k / self.total if self.total > 0 else CP_OIL


def stream_mass(q_oil_stb_d, q_water_stb_d, gor_scf_stb, api, gas_sg) -> StreamMass:
    rho_o = 141.5 / (api + 131.5) * 999.0                     # kg/m³ stock tank
    oil = q_oil_stb_d * 0.158987 * rho_o / 86400.0
    gas = q_oil_stb_d * gor_scf_stb * 0.0764 * 0.45359 * gas_sg / 86400.0
    water = q_water_stb_d * 0.158987 * 1025.0 / 86400.0
    return StreamMass(oil, gas, water)


def cooldown_hours(t0_c: float, t_hydrate_c: float, t_amb_c: float, u_w_m2k: float, d_in_m: float,
                   wall_m: float, rho_fluid_kg_m3: float, cp_fluid: float) -> float:
    """Time for the settled fluid to cool from t0 to the hydrate temperature."""
    if t0_c <= t_hydrate_c:
        return 0.0
    if t_hydrate_c <= t_amb_c or u_w_m2k <= 0:
        return math.inf
    a_fluid = math.pi * d_in_m ** 2 / 4.0
    a_steel = math.pi * ((d_in_m + 2 * wall_m) ** 2 - d_in_m ** 2) / 4.0
    cap = a_fluid * rho_fluid_kg_m3 * cp_fluid + a_steel * RHO_STEEL * CP_STEEL   # J/(m·K)
    tau_s = cap / (u_w_m2k * math.pi * d_in_m)
    return tau_s * math.log((t0_c - t_amb_c) / (t_hydrate_c - t_amb_c)) / 3600.0


# ───────────────────────── Joule-Thomson expansion ─────────────────────────

R_UNIVERSAL = 8.314462           # J/(mol·K)
MW_AIR_G = 28.9647               # g/mol
JT_LIQUID_K_PER_BAR = -0.02      # liquids warm slightly on expansion (friction-dominated)


def jt_coefficient_gas_k_per_bar(p_psia: float, t_f: float, gas_sg: float, z_fn, cp_j_kgk: float = CP_GAS) -> float:
    """Real-gas Joule-Thomson coefficient, K/bar (positive = cools on expansion).

        μ_JT = (R_specific · T²) / (p · c_p · Z) · (∂Z/∂T)_p

    `z_fn(p_psia, t_f, gas_sg)` supplies the compressibility factor (tb_multiphase.z_factor),
    differentiated numerically at constant pressure.
    """
    z = z_fn(p_psia, t_f, gas_sg)
    dz_dt_k = (z_fn(p_psia, t_f + 1.0, gas_sg) - z_fn(p_psia, t_f - 1.0, gas_sg)) / 2.0 * 1.8
    r_specific = R_UNIVERSAL / (MW_AIR_G * gas_sg / 1000.0)
    t_k = (t_f + 459.67) / 1.8
    mu_k_per_pa = r_specific * t_k ** 2 / (max(p_psia, 1.0) * 6894.757 * cp_j_kgk * max(z, 0.1)) * dz_dt_k
    return mu_k_per_pa * 1e5


def jt_coefficient_mixture_k_per_bar(p_psia, t_f, gas_sg, gas_mass_frac: float, z_fn) -> float:
    """Mass-weighted JT coefficient for the flowing mixture."""
    g = min(max(gas_mass_frac, 0.0), 1.0)
    return g * jt_coefficient_gas_k_per_bar(p_psia, t_f, gas_sg, z_fn) + (1 - g) * JT_LIQUID_K_PER_BAR


def hydrate_temperature_f(p_psia: float, gas_sg: float) -> float:
    """Towler & Mokhatab (2005) hydrate formation temperature, °F."""
    if p_psia <= 14.7:
        return -math.inf
    lp, lg = math.log(p_psia), math.log(gas_sg)
    return 13.47 * lp + 34.27 * lg - 1.675 * lp * lg - 20.35


def hammerschmidt_depression_f(inhibitor: str, wt_pct: float) -> float:
    k, m = INHIBITORS[inhibitor]
    if k == 0 or wt_pct <= 0:
        return 0.0
    if not 0 < wt_pct < 100:
        raise ValueError("inhibitor wt % must be in (0, 100)")
    return k * wt_pct / (m * (100.0 - wt_pct))


def hydrate_margin_c(t_c: float, p_psia: float, gas_sg: float, inhibitor="None", wt_pct=0.0) -> float:
    """Positive = warmer than the (inhibited) hydrate curve by this many °C."""
    t_h_f = hydrate_temperature_f(p_psia, gas_sg) - hammerschmidt_depression_f(inhibitor, wt_pct)
    return t_c - f_to_c(t_h_f)
