"""
tb_multiphase.py — Multiphase pressure gradient for TieBack Studio.

Ported from PVT Studio `nodal.py` (Beggs & Brill 1973, revised 1979, with the
same fluid-property stack), keeping its structure and field-unit interface.
The port corrects errors found while reviewing the source — each is covered
by a test in test_multiphase.py:

  C1  Two-phase friction multiplier S (Beggs & Brill 1973, eq. for S):
        S = ln(y) / (−0.0523 + 3.182 ln y − 0.8725 (ln y)² + 0.01853 (ln y)⁴)
        except 1 < y < 1.2, where S = ln(2.2y − 1.2).
      Source applied ln(2.2y−1.2) for y > 1.2 (friction ×4.6 at y = 4) and
      clipped the denominator with max(·, 1e-6), so for y < 1 (negative
      denominator) S → −∞ and two-phase friction vanished.
  C2  Flow pattern for λ_L < 0.01: segregated only when Fr < L1, otherwise
      distributed. Source forced "segregated" for every very dry flow.
  C3  Liquid velocity number N_Lv = 1.938 v_SL (ρ_L/σ)^¼ (field units,
      σ in dyn/cm). Source used v_SL (ρ_L/(g·σ·10⁻³))^¼ (≈20 % high).
  C4  Standing Rs = γg [(p/18.2 + 1.4)·10^(0.0125 API − 0.00091 T)]^1.2048
      (source raised γg to the 1.2048 power), and Standing Bo uses
      γo = 141.5/(API + 131.5) (source used (API+141.5)/131.5).
  A1  Payne et al. (1979) holdup correction (×0.924 uphill, ×0.685 downhill)
      applied — documented in PVT Studio but absent from its code.

Units (engine-internal, field): psia, °F, ft, in, STB/d, Mscf/d, lb/ft³, cP,
dyn/cm. Screening-grade empirical correlation; not a mechanistic model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict

G_C = 32.174
PSI_TO_PSF = 144.0
P_SC, T_SC_R = 14.696, 519.67


# ─────────────────────────────── fluid properties ───────────────────────────

def sutton_pseudocritical(gas_sg: float):
    """Sutton (1985) pseudo-critical T (°R) and p (psia) for sweet natural gas."""
    tpc = 169.2 + 349.5 * gas_sg - 74.0 * gas_sg ** 2
    ppc = 756.8 - 131.0 * gas_sg - 3.6 * gas_sg ** 2
    return tpc, ppc


# Brill & Beggs was fitted over roughly 1.2 < Tpr < 2.4 and Ppr < 15. Outside that the
# terms grow without bound, so the reduced properties are clamped to the edge of the
# fit: the answer there is the boundary value, not a silent extrapolation to nonsense.
Z_PPR_MAX = 15.0
Z_TPR_RANGE = (1.05, 3.0)


def z_factor(p_psia: float, t_f: float, gas_sg: float) -> float:
    """Brill & Beggs (1974) explicit Z-factor (screening accuracy)."""
    tpc, ppc = sutton_pseudocritical(gas_sg)
    tpr = min(max((t_f + 459.67) / tpc, Z_TPR_RANGE[0]), Z_TPR_RANGE[1])
    ppr = min(max(p_psia, 0.0) / ppc, Z_PPR_MAX)
    a = 1.39 * math.sqrt(max(tpr - 0.92, 0.0)) - 0.36 * tpr - 0.101
    b = ((0.62 - 0.23 * tpr) * ppr
         + (0.066 / max(tpr - 0.86, 0.05) - 0.037) * ppr ** 2
         + 0.32 * ppr ** 6 / 10 ** (9.0 * (tpr - 1.0)))
    c = 0.132 - 0.32 * math.log10(tpr)
    d = 10 ** (0.3106 - 0.49 * tpr + 0.1824 * tpr ** 2)
    # exp(-b), not 1/exp(b): a large negative b used to underflow exp() to zero and
    # divide by it, which crashed the whole page from deep inside a pressure march
    z = a + (1.0 - a) * math.exp(-max(min(b, 50.0), -50.0)) + c * min(ppr ** d, 1e6)
    return max(0.3, min(z, 1.4))


def gas_density(p_psia, t_f, gas_sg, z) -> float:
    return p_psia * 28.9647 * gas_sg / (max(z, 0.1) * 10.7316 * (t_f + 459.67))


def gas_viscosity_lee(p_psia, t_f, gas_sg, z) -> float:
    """Lee, Gonzalez & Eakin (1966), cP."""
    t_r = t_f + 459.67
    m = 28.9647 * gas_sg
    rho = gas_density(p_psia, t_f, gas_sg, z) * 0.016018  # g/cm³
    k = (9.4 + 0.02 * m) * t_r ** 1.5 / (209.0 + 19.0 * m + t_r)
    x = 3.5 + 986.0 / t_r + 0.01 * m
    y = 2.4 - 0.2 * x
    return 1e-4 * k * math.exp(x * rho ** y)


def oil_sg(api: float) -> float:
    return 141.5 / (api + 131.5)


def standing_rs(p_psia, t_f, api, gas_sg) -> float:
    """Standing (1947) solution GOR, scf/STB. [C4]"""
    return gas_sg * ((p_psia / 18.2 + 1.4) * 10 ** (0.0125 * api - 0.00091 * t_f)) ** 1.2048


def standing_bo(rs, t_f, api, gas_sg) -> float:
    """Standing (1947) oil FVF, rb/STB. [C4]"""
    f = rs * math.sqrt(gas_sg / oil_sg(api)) + 1.25 * t_f
    return 0.9759 + 0.00012 * f ** 1.2


def dead_oil_viscosity(api, t_f) -> float:
    """Beggs & Robinson (1975) dead-oil viscosity, cP."""
    t_f = max(t_f, 50.0)
    x = 10 ** (3.0324 - 0.02023 * api) * t_f ** (-1.163)
    return max(0.1, 10 ** x - 1.0)


def live_oil_density(api, gas_sg, rs, bo) -> float:
    rho_st = oil_sg(api) * 62.428
    return (rho_st * 5.6146 + rs * gas_sg * 0.0764) / max(bo * 5.6146, 0.01)


def water_density(salinity_wt_pct, t_f) -> float:
    return 62.4 * (1.0 - 0.0001 * (t_f - 60.0)) * (1.0 + 0.00747 * salinity_wt_pct)


def water_viscosity(t_f) -> float:
    """Screening brine viscosity, cP (≈1.0 at 68 °F, ≈0.3 at 180 °F)."""
    return max(math.exp(-0.0118 * (t_f - 68.0)), 0.2)


@dataclass
class Fluid:
    """Black-oil stream description at standard conditions."""
    api: float = 38.0              # condensate/oil gravity
    gas_sg: float = 0.70
    gor_scf_stb: float = 1500.0    # produced GOR (per STB oil/condensate)
    water_cut: float = 0.10        # fraction of liquid at standard conditions
    salinity_wt_pct: float = 3.5
    sigma_dyn_cm: float = 25.0     # gas–liquid interfacial tension

    def __post_init__(self):
        if not 5.0 <= self.api <= 80.0:
            raise ValueError("API outside 5–80")
        if not 0.55 <= self.gas_sg <= 1.5:
            raise ValueError("gas SG outside 0.55–1.5")
        if not 0.0 <= self.water_cut < 1.0:
            raise ValueError("water cut must be in [0, 1)")
        if self.gor_scf_stb < 0:
            raise ValueError("GOR must be >= 0")


def local_properties(fluid: Fluid, q_oil_stb_d: float, q_water_stb_d: float,
                     p_psia: float, t_f: float) -> Dict[str, float]:
    """In-situ phase properties and volumetric rates (black-oil split)."""
    p = max(p_psia, 1.0)
    z = z_factor(p, t_f, fluid.gas_sg)
    rs = min(standing_rs(p, t_f, fluid.api, fluid.gas_sg), fluid.gor_scf_stb)
    bo = standing_bo(rs, t_f, fluid.api, fluid.gas_sg)
    free_gas_scf_d = max(fluid.gor_scf_stb - rs, 0.0) * q_oil_stb_d
    bg = P_SC / p * (t_f + 459.67) / T_SC_R * z                       # ft³/scf
    q_gas_ft3_s = free_gas_scf_d * bg / 86400.0
    q_liq_ft3_s = (q_oil_stb_d * bo + q_water_stb_d * 1.0) * 5.6146 / 86400.0
    rho_o = live_oil_density(fluid.api, fluid.gas_sg, rs, bo)
    rho_w = water_density(fluid.salinity_wt_pct, t_f)
    q_o_res, q_w_res = q_oil_stb_d * bo, q_water_stb_d
    wf = q_w_res / (q_o_res + q_w_res) if (q_o_res + q_w_res) > 0 else 0.0
    return dict(z=z, rs=rs, bo=bo,
                rho_g=gas_density(p, t_f, fluid.gas_sg, z), mu_g=gas_viscosity_lee(p, t_f, fluid.gas_sg, z),
                rho_l=rho_o * (1 - wf) + rho_w * wf,
                mu_l=dead_oil_viscosity(fluid.api, t_f) * (1 - wf) + water_viscosity(t_f) * wf,
                q_gas_ft3_s=q_gas_ft3_s, q_liq_ft3_s=q_liq_ft3_s)


# ───────────────────────────────── friction ─────────────────────────────────

def haaland_friction(re: float, rel_roughness: float) -> float:
    """Darcy friction factor: laminar 64/Re, else Haaland (1983)."""
    if re <= 0:
        return 0.0
    if re < 2300.0:
        return 64.0 / re
    inv = -1.8 * math.log10((rel_roughness / 3.7) ** 1.11 + 6.9 / re)
    return 1.0 / inv ** 2


def s_factor(y: float) -> float:
    """Beggs & Brill two-phase friction exponent S. [C1]"""
    if y <= 0:
        return 0.0
    if 1.0 < y < 1.2:
        return math.log(2.2 * y - 1.2)
    x = math.log(y)
    denom = -0.0523 + 3.182 * x - 0.8725 * x ** 2 + 0.01853 * x ** 4
    if abs(denom) < 1e-9:
        return 0.0
    return x / denom


# ─────────────────────────────── Beggs & Brill ──────────────────────────────

PATTERN_COEF = {"segregated": (0.98, 0.4846, 0.0868),
                "intermittent": (0.845, 0.5351, 0.0173),
                "distributed": (1.065, 0.5824, 0.0609)}
UPHILL_DEFG = {"segregated": (0.011, -3.768, 3.539, -1.614),
               "intermittent": (2.96, 0.305, -0.4473, 0.0978)}
DOWNHILL_DEFG = (4.70, -0.3692, 0.1244, -0.5056)


def flow_pattern(lam: float, fr: float) -> str:
    """Beggs & Brill (1977 revised boundaries) horizontal flow-pattern map. [C2]"""
    if lam <= 0:
        return "segregated" if fr < 316.0 else "distributed"   # λ→0 limit of L1 = 316 λ^0.302 → 0
    l1 = 316.0 * lam ** 0.302
    l2 = 0.0009252 * lam ** -2.4684
    l3 = 0.10 * lam ** -1.4516
    l4 = 0.5 * lam ** -6.738
    if (lam < 0.01 and fr < l1) or (lam >= 0.01 and fr < l2):
        return "segregated"
    if lam >= 0.01 and l2 <= fr <= l3:
        return "transition"
    if (0.01 <= lam < 0.4 and l3 < fr <= l1) or (lam >= 0.4 and l3 < fr <= l4):
        return "intermittent"
    return "distributed"


def _horizontal_holdup(pattern, lam, fr):
    def hl(p):
        a, b, c = PATTERN_COEF[p]
        return a * lam ** b / max(fr, 1e-9) ** c
    if pattern == "transition":
        l2 = 0.0009252 * lam ** -2.4684
        l3 = 0.10 * lam ** -1.4516
        a = min(max((l3 - fr) / (l3 - l2), 0.0), 1.0) if l3 != l2 else 0.5
        return a * hl("segregated") + (1 - a) * hl("intermittent")
    return hl(pattern)


def liquid_velocity_number(v_sl, rho_l, sigma) -> float:
    """N_Lv = 1.938 v_SL (ρ_L/σ)^¼ — ft/s, lb/ft³, dyn/cm. [C3]"""
    return 1.938 * v_sl * (rho_l / sigma) ** 0.25


def beggs_brill(v_sl, v_sg, rho_l, rho_g, mu_l, mu_g, d_in, theta_deg, rel_roughness, p_psia,
                sigma=25.0, payne=True) -> Dict[str, float]:
    """Pressure gradient (psi/ft, positive = pressure increases upstream) for flow
    upward at `theta_deg` above horizontal (negative = downhill)."""
    d_ft = d_in / 12.0
    v_m = v_sl + v_sg
    s_th = math.sin(math.radians(theta_deg))
    if v_m <= 0:
        return dict(dpdl=rho_l * s_th / PSI_TO_PSF, dpdl_fric=0.0, dpdl_hydro=rho_l * s_th / PSI_TO_PSF,
                    ek=0.0, holdup=1.0, lam=1.0, pattern="no flow", v_m=0.0, fr=0.0, rho_s=rho_l, rho_ns=rho_l)
    lam = v_sl / v_m
    fr = v_m ** 2 / (G_C * d_ft)
    if lam <= 1e-9:                     # single-phase gas
        pattern, h_l = "single-phase gas", 0.0
    elif lam >= 1 - 1e-9:               # single-phase liquid
        pattern, h_l = "single-phase liquid", 1.0
    else:
        pattern = flow_pattern(lam, fr)
        h0 = max(_horizontal_holdup(pattern, lam, fr), lam)
        h0 = min(h0, 1.0)
        if pattern == "distributed" and theta_deg >= 0:
            c = 0.0
        else:
            key = "segregated" if pattern == "transition" else pattern
            d_, e_, f_, g_ = DOWNHILL_DEFG if theta_deg < 0 else UPHILL_DEFG[key]
            nlv = liquid_velocity_number(v_sl, rho_l, sigma)
            arg = d_ * lam ** e_ * max(nlv, 1e-12) ** f_ * fr ** g_
            c = max((1.0 - lam) * math.log(arg), 0.0) if arg > 0 else 0.0
        s18 = math.sin(math.radians(1.8 * theta_deg))
        b_ang = 1.0 + c * (s18 - s18 ** 3 / 3.0)
        h_l = h0 * b_ang
        if payne and theta_deg != 0:
            h_l *= 0.924 if theta_deg > 0 else 0.685
        h_l = min(max(h_l, lam), 1.0)
    rho_ns = rho_l * lam + rho_g * (1 - lam)
    rho_s = rho_l * h_l + rho_g * (1 - h_l)
    mu_ns = mu_l * lam + mu_g * (1 - lam)
    re_ns = 1488.0 * rho_ns * v_m * d_ft / max(mu_ns, 1e-9)
    f_n = haaland_friction(re_ns, rel_roughness)
    if 0 < lam < 1 and h_l > 0:
        f_tp = f_n * math.exp(s_factor(lam / h_l ** 2))
    else:
        f_tp = f_n
    dp_f = f_tp * rho_ns * v_m ** 2 / (2.0 * G_C * d_ft) / PSI_TO_PSF
    dp_h = rho_s * s_th / PSI_TO_PSF
    ek = min(rho_s * v_m * v_sg / (G_C * max(p_psia, 1.0) * PSI_TO_PSF), 0.9)
    return dict(dpdl=(dp_f + dp_h) / (1.0 - ek), dpdl_fric=dp_f, dpdl_hydro=dp_h, ek=ek, holdup=h_l,
                lam=lam, pattern=pattern, v_m=v_m, fr=fr, rho_s=rho_s, rho_ns=rho_ns, f_tp=f_tp, re_ns=re_ns)


def segment_gradient(fluid: Fluid, q_oil_stb_d, q_water_stb_d, p_psia, t_f, d_in, theta_deg,
                     rel_roughness=0.0006, payne=True) -> Dict[str, float]:
    """Local properties + Beggs-Brill gradient for one pipe station."""
    props = local_properties(fluid, q_oil_stb_d, q_water_stb_d, p_psia, t_f)
    area = math.pi * (d_in / 12.0) ** 2 / 4.0
    v_sl, v_sg = props["q_liq_ft3_s"] / area, props["q_gas_ft3_s"] / area
    out = beggs_brill(v_sl, v_sg, props["rho_l"], props["rho_g"], props["mu_l"], props["mu_g"],
                      d_in, theta_deg, rel_roughness, p_psia, fluid.sigma_dyn_cm, payne)
    out.update(v_sl=v_sl, v_sg=v_sg, **{k: props[k] for k in ("rho_l", "rho_g", "mu_l", "mu_g", "z", "rs", "bo")})
    return out


def erosional_velocity_ft_s(rho_mix_lb_ft3: float, c_factor: float = 100.0) -> float:
    """API RP 14E erosional velocity V_e = C/√ρ_m (ft/s)."""
    return c_factor / math.sqrt(max(rho_mix_lb_ft3, 0.01))
