"""
tb_economics.py — What the concept is worth, not only what it costs.

Takes a CAPEX profile (tb_cost.phase_costs) and a production profile
(tb_production.field_profile) and runs the arithmetic a concept is judged on:

* **OPEX** — a fixed annual cost, a variable cost per barrel of oil equivalent,
  the host's **tariff**, intervention vessel days, and the chemical bill that
  falls out of the hydrate-inhibitor sizing.
* **Abandonment** — a provision, spent in the year after the last production.
* **Cash flow** — revenue at your oil and gas prices, less OPEX, tariff, CAPEX
  and abandonment, optionally after NCS petroleum tax.
* **The numbers people ask for** — NPV at a discount rate, IRR, payback year,
  break-even oil price (the price at which NPV is zero), unit technical cost,
  and CAPEX per barrel of oil equivalent.
* **`tornado`** — one run per input, each moved to its low and high value, so
  you can see which assumption actually decides the answer.

Screening economics: real terms (no inflation), annual periods, no financing,
no depreciation schedule beyond the simple uplift, no portfolio effects. The
tax model is the NCS headline rate applied to a simplified base — good enough
to see whether tax changes the ranking, not a tax calculation.

Units: USD unless a name says otherwise, Sm³ for volumes, fractions for rates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

BBL_PER_SM3 = 6.2898
BOE_PER_SM3_GAS = 1.0 / 1000.0          # 1 000 Sm³ gas = 1 Sm³ o.e.


@dataclass
class EconomicSettings:
    """Prices, costs and the discount rate. Everything real (no inflation)."""
    oil_price_usd_bbl: float = 70.0
    gas_price_usd_sm3: float = 0.28              # ≈ 8 USD/MMBtu
    discount_rate: float = 0.08
    reference_year: int = 0                      # 0 = the first year with money in it
    # operating cost
    opex_fixed_musd_yr: float = 8.0              # share of host and operator's fixed cost
    opex_var_usd_boe: float = 6.0                # variable cost with volume
    tariff_usd_boe: float = 8.0                  # what the host charges to process and export
    intervention_days_yr: float = 4.0
    intervention_day_rate_usd: float = 250_000.0
    chemical_musd_yr: float = 0.0                # from the inhibitor sizing, if you have it
    # end of life
    abandonment_frac_of_capex: float = 0.12
    abandonment_musd: float = 0.0                # overrides the fraction when > 0
    # tax (NCS: 22 % corporate + 56 % special = 78 %, with an uplift on investment)
    apply_tax: bool = False
    tax_rate: float = 0.78
    uplift_frac: float = 0.125                   # extra deduction on investment, once

    def __post_init__(self):
        if self.discount_rate <= -1:
            raise ValueError("discount rate must be greater than -100 %")
        for name in ("oil_price_usd_bbl", "gas_price_usd_sm3", "opex_var_usd_boe", "tariff_usd_boe"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if not 0 <= self.tax_rate < 1:
            raise ValueError("tax rate must be a fraction below 1")


def revenue_usd(oil_sm3: float, gas_sm3: float, s: EconomicSettings) -> float:
    return oil_sm3 * BBL_PER_SM3 * s.oil_price_usd_bbl + gas_sm3 * s.gas_price_usd_sm3


def boe(oil_sm3: float, gas_sm3: float) -> float:
    """Sm³ of oil equivalent."""
    return oil_sm3 + gas_sm3 * BOE_PER_SM3_GAS


def cashflow(production_years: List[dict], capex_by_year: Dict[int, float],
             s: Optional[EconomicSettings] = None) -> dict:
    """Year-by-year cash flow, and the measures taken from it.

    `production_years` is `tb_production.field_profile()["years"]`;
    `capex_by_year` is `tb_cost.phase_costs()["annual"]` (USD).
    """
    s = s or EconomicSettings()
    prod = {int(r["year"]): r for r in production_years}
    capex = {int(y): float(v) for y, v in (capex_by_year or {}).items()}
    if not prod and not capex:
        return dict(years=[], npv_usd=0.0, irr=None, payback_year=None, capex_usd=0.0,
                    breakeven_oil_usd_bbl=None, unit_technical_cost_usd_boe=None,
                    total_boe_sm3=0.0, note="nothing to evaluate")
    last_prod = max(prod) if prod else max(capex)
    abandon_year = last_prod + 1
    total_capex = sum(capex.values())
    aband = (s.abandonment_musd * 1e6) if s.abandonment_musd > 0 else s.abandonment_frac_of_capex * total_capex
    years = sorted(set(list(prod) + list(capex) + [abandon_year]))
    ref = s.reference_year or years[0]
    rows = []
    for y in years:
        p = prod.get(y)
        oil = p["oil_sm3"] if p else 0.0
        gas = p["gas_sm3"] if p else 0.0
        b = boe(oil, gas)
        rev = revenue_usd(oil, gas, s)
        opex = 0.0
        if p:
            opex = (s.opex_fixed_musd_yr * 1e6 + s.chemical_musd_yr * 1e6
                    + s.intervention_days_yr * s.intervention_day_rate_usd
                    + s.opex_var_usd_boe * b * BBL_PER_SM3)
        tariff = s.tariff_usd_boe * b * BBL_PER_SM3 if p else 0.0
        cap = capex.get(y, 0.0)
        ab = aband if y == abandon_year else 0.0
        pre_tax = rev - opex - tariff - cap - ab
        tax = 0.0
        if s.apply_tax:
            base = rev - opex - tariff - cap * (1 + s.uplift_frac) - ab
            tax = max(base, 0.0) * s.tax_rate
        net = pre_tax - tax
        df = 1.0 / (1.0 + s.discount_rate) ** (y - ref)
        rows.append(dict(year=y, oil_sm3=oil, gas_sm3=gas, boe_sm3=b, revenue_usd=rev, opex_usd=opex,
                         tariff_usd=tariff, capex_usd=cap, abandonment_usd=ab, tax_usd=tax,
                         net_usd=net, discount_factor=df, discounted_usd=net * df))
    npv = sum(r["discounted_usd"] for r in rows)
    cum = 0.0
    payback = None
    for r in rows:
        cum += r["net_usd"]
        if payback is None and cum > 0:
            payback = r["year"]
    total_boe = sum(r["boe_sm3"] for r in rows)
    net_cost = sum(r["capex_usd"] + r["opex_usd"] + r["tariff_usd"] + r["abandonment_usd"] for r in rows)
    return dict(years=rows, npv_usd=npv, irr=irr([r["net_usd"] for r in rows]),
                payback_year=payback, capex_usd=total_capex, abandonment_usd=aband,
                total_boe_sm3=total_boe,
                unit_technical_cost_usd_boe=(net_cost / (total_boe * BBL_PER_SM3)) if total_boe > 0 else None,
                capex_usd_boe=(total_capex / (total_boe * BBL_PER_SM3)) if total_boe > 0 else None,
                breakeven_oil_usd_bbl=breakeven_oil_price(production_years, capex_by_year, s),
                reference_year=ref, note="")


def irr(net_by_year: List[float], lo: float = -0.9, hi: float = 5.0) -> Optional[float]:
    """Internal rate of return, or None when the cash flow never changes sign."""
    if not net_by_year or all(v >= 0 for v in net_by_year) or all(v <= 0 for v in net_by_year):
        return None

    def npv_at(r):
        return sum(v / (1 + r) ** i for i, v in enumerate(net_by_year))

    f_lo, f_hi = npv_at(lo), npv_at(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv_at(mid)
        if abs(f_mid) < 1.0:
            return mid
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def breakeven_oil_price(production_years: List[dict], capex_by_year: Dict[int, float],
                        s: Optional[EconomicSettings] = None) -> Optional[float]:
    """The oil price at which NPV is zero, with the gas price moved in proportion."""
    s = s or EconomicSettings()
    if not production_years:
        return None
    base_oil = max(s.oil_price_usd_bbl, 1e-6)

    def npv_at_price(price):
        import dataclasses as _dc
        k = price / base_oil
        s2 = _dc.replace(s, oil_price_usd_bbl=price, gas_price_usd_sm3=s.gas_price_usd_sm3 * k)
        return sum(r["discounted_usd"] for r in cashflow_rows_only(production_years, capex_by_year, s2))

    lo, hi = 1.0, 400.0
    f_lo, f_hi = npv_at_price(lo), npv_at_price(hi)
    if f_lo > 0:
        return lo
    if f_hi < 0:
        return None                      # not economic at any sensible price
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv_at_price(mid) > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def cashflow_rows_only(production_years, capex_by_year, s) -> List[dict]:
    """The rows of `cashflow` without the measures — used by the break-even search."""
    import dataclasses as _dc
    s2 = _dc.replace(s)
    prod = {int(r["year"]): r for r in production_years}
    capex = {int(y): float(v) for y, v in (capex_by_year or {}).items()}
    if not prod and not capex:
        return []
    last_prod = max(prod) if prod else max(capex)
    abandon_year = last_prod + 1
    total_capex = sum(capex.values())
    aband = (s2.abandonment_musd * 1e6) if s2.abandonment_musd > 0 else s2.abandonment_frac_of_capex * total_capex
    years = sorted(set(list(prod) + list(capex) + [abandon_year]))
    ref = s2.reference_year or years[0]
    rows = []
    for y in years:
        p = prod.get(y)
        oil = p["oil_sm3"] if p else 0.0
        gas = p["gas_sm3"] if p else 0.0
        b = boe(oil, gas)
        rev = revenue_usd(oil, gas, s2)
        opex = ((s2.opex_fixed_musd_yr * 1e6 + s2.chemical_musd_yr * 1e6
                 + s2.intervention_days_yr * s2.intervention_day_rate_usd
                 + s2.opex_var_usd_boe * b * BBL_PER_SM3) if p else 0.0)
        tariff = s2.tariff_usd_boe * b * BBL_PER_SM3 if p else 0.0
        cap = capex.get(y, 0.0)
        ab = aband if y == abandon_year else 0.0
        pre_tax = rev - opex - tariff - cap - ab
        tax = 0.0
        if s2.apply_tax:
            base = rev - opex - tariff - cap * (1 + s2.uplift_frac) - ab
            tax = max(base, 0.0) * s2.tax_rate
        net = pre_tax - tax
        rows.append(dict(year=y, net_usd=net, discounted_usd=net / (1 + s2.discount_rate) ** (y - ref)))
    return rows


# ──────────────────────────────── sensitivity ──────────────────────────────

@dataclass
class Swing:
    """One input, and how far it is moved either way."""
    name: str
    low: float
    high: float
    apply: str          # "economics" | "capex" | "production"
    field_name: str = ""


DEFAULT_SWINGS = (
    Swing("Oil price (USD/bbl)", 45.0, 95.0, "economics", "oil_price_usd_bbl"),
    Swing("Gas price (USD/Sm³)", 0.15, 0.45, "economics", "gas_price_usd_sm3"),
    Swing("CAPEX (×)", 0.8, 1.4, "capex"),
    Swing("Recoverable volume (×)", 0.7, 1.3, "production"),
    Swing("Host tariff (USD/boe)", 4.0, 14.0, "economics", "tariff_usd_boe"),
    Swing("Fixed OPEX (MUSD/yr)", 5.0, 15.0, "economics", "opex_fixed_musd_yr"),
    Swing("Discount rate", 0.06, 0.12, "economics", "discount_rate"),
)


def tornado(production_years: List[dict], capex_by_year: Dict[int, float],
            s: Optional[EconomicSettings] = None, swings=DEFAULT_SWINGS) -> List[dict]:
    """NPV with each input at its low and high value, ranked by how much it moves the answer."""
    import dataclasses as _dc
    s = s or EconomicSettings()
    base = cashflow(production_years, capex_by_year, s)["npv_usd"]
    rows = []
    for sw in swings:
        vals = {}
        for side, v in (("low", sw.low), ("high", sw.high)):
            if sw.apply == "economics":
                vals[side] = cashflow(production_years, capex_by_year,
                                      _dc.replace(s, **{sw.field_name: v}))["npv_usd"]
            elif sw.apply == "capex":
                vals[side] = cashflow(production_years,
                                      {y: c * v for y, c in (capex_by_year or {}).items()}, s)["npv_usd"]
            else:
                scaled = [dict(r, oil_sm3=r["oil_sm3"] * v, gas_sm3=r["gas_sm3"] * v,
                               boe_sm3=r["boe_sm3"] * v) for r in production_years]
                vals[side] = cashflow(scaled, capex_by_year, s)["npv_usd"]
        rows.append(dict(input=sw.name, low_value=sw.low, high_value=sw.high,
                         npv_low_usd=vals["low"], npv_high_usd=vals["high"], base_npv_usd=base,
                         swing_usd=abs(vals["high"] - vals["low"])))
    return sorted(rows, key=lambda r: r["swing_usd"], reverse=True)


def chemical_cost_musd_yr(inhibitor_recommendation: Optional[dict]) -> float:
    """Annual chemical cost from tb_chemistry.recommend_inhibitor, or 0."""
    if not inhibitor_recommendation:
        return 0.0
    duties = inhibitor_recommendation.get("duties") or {}
    pick = inhibitor_recommendation.get("recommended", "")
    for name, duty in duties.items():
        if name.lower() in str(pick).lower():
            return float(getattr(duty, "annual_cost_musd", 0.0) or 0.0)
    return 0.0


def summary_line(cf: dict, currency_factor: float = 1e-6, unit: str = "MUSD") -> str:
    """One sentence a reviewer can read out."""
    if not cf.get("years"):
        return "No production profile and no CAPEX: nothing to evaluate."
    npv = cf["npv_usd"] * currency_factor
    be = cf.get("breakeven_oil_usd_bbl")
    parts = [f"NPV {npv:,.0f} {unit}"]
    if cf.get("irr") is not None:
        parts.append(f"IRR {cf['irr']:.0%}")
    if be:
        parts.append(f"break-even {be:,.0f} USD/bbl")
    if cf.get("payback_year"):
        parts.append(f"payback {cf['payback_year']}")
    if cf.get("unit_technical_cost_usd_boe"):
        parts.append(f"unit technical cost {cf['unit_technical_cost_usd_boe']:,.1f} USD/boe")
    return " · ".join(parts)
