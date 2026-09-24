"""
tb_production.py — How much, for how long, and from how many wells.

The rest of the app answers "what does this concept cost and can it flow". This
module answers the questions in front of that:

* **How much is there?** `stoiip_sm3` / `giip_sm3` from the usual volumetrics.
* **How much comes out?** `suggest_recovery_factor` gives the range a drainage
  strategy is normally worth on the NCS, with the reasoning written out, and
  `eur_sm3` turns it into a recoverable volume.
* **How many wells?** `wells_needed` takes the plateau you want and the rate a
  well can deliver, checks it against the drainage area, and says which of the
  two is binding.
* **What does the profile look like?** `profile` builds plateau-then-decline
  year by year, capped by the host's capacity, and `field_profile` does it for
  a whole layout, reservoir by reservoir.

Everything here is **screening**: a tank of oil, a plateau, a decline curve.
There is no material balance, no simulation grid, no relative permeability, no
well-by-well interference. It is the arithmetic an engineer does on one page
before a simulation exists — useful for comparing concepts, useless as a
reserves statement.

Units: Sm³, Sm³/d, years, fractions (not percentages).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

SM3_PER_BBL = 0.1589873
BOE_PER_SM3_GAS = 1.0 / 1000.0          # 1 000 Sm³ gas ≈ 1 Sm³ o.e. (NCS convention)
DAYS_PER_YEAR = 365.25

# Drive mechanisms, and what each is normally worth as a recovery factor. Ranges
# are the screening ranges quoted for NCS-type reservoirs; they are a starting
# point for discussion with the subsurface team, not an estimate.
DRIVES = ("solution gas / depletion", "gas cap expansion", "natural water drive",
          "water injection", "gas injection / WAG", "pressure depletion (gas)",
          "water drive (gas)")

RECOVERY_RANGES: Dict[str, Dict[str, tuple]] = {
    # oil-type reservoirs: (low, mid, high)
    "oil": {
        "solution gas / depletion": (0.05, 0.12, 0.20),
        "gas cap expansion": (0.20, 0.30, 0.40),
        "natural water drive": (0.35, 0.45, 0.60),
        "water injection": (0.35, 0.45, 0.55),
        "gas injection / WAG": (0.40, 0.50, 0.65),
    },
    # gas and gas condensate
    "gas": {
        "pressure depletion (gas)": (0.60, 0.75, 0.85),
        "water drive (gas)": (0.40, 0.55, 0.70),
        "gas cap expansion": (0.55, 0.70, 0.80),
    },
}

NATURAL_DRIVES = ("solution gas / depletion", "gas cap expansion", "natural water drive",
                  "pressure depletion (gas)", "water drive (gas)")


def family(fluid: str) -> str:
    """'oil' or 'gas' for a reservoir fluid name (tb_fluids.RESERVOIR_FLUIDS)."""
    return "oil" if str(fluid).lower() in ("black oil", "volatile oil", "oil") else "gas"


def default_drive(fluid: str) -> str:
    return "natural water drive" if family(fluid) == "oil" else "pressure depletion (gas)"


def suggest_recovery_factor(fluid: str, drive: str = "", oil_viscosity_cp: Optional[float] = None,
                            permeability_md: Optional[float] = None,
                            subsea_tieback: bool = True) -> dict:
    """Recovery-factor range for this fluid and drainage strategy, and why.

    The modifiers are the ones that move a screening number materially: viscous
    oil and tight rock pull it down, and a subsea tie-back drilled with a
    handful of wells recovers less than the same reservoir under a platform
    with infill drilling.
    """
    fam = family(fluid)
    drive = drive or default_drive(fluid)
    table = RECOVERY_RANGES[fam]
    if drive not in table:
        drive = default_drive(fluid)
    lo, mid, hi = table[drive]
    why = [f"{fam} under {drive}: {lo:.0%}–{hi:.0%} is the usual screening range"]
    adj = 1.0
    if oil_viscosity_cp is not None and fam == "oil":
        if oil_viscosity_cp > 50:
            adj *= 0.65
            why.append(f"{oil_viscosity_cp:.0f} cP oil: viscous fingering and poor sweep, so well below the range")
        elif oil_viscosity_cp > 10:
            adj *= 0.85
            why.append(f"{oil_viscosity_cp:.0f} cP oil: mobility ratio costs sweep efficiency")
    if permeability_md is not None:
        if permeability_md < 10:
            adj *= 0.75
            why.append(f"{permeability_md:.0f} mD: tight rock, each well drains slowly and less of the volume")
        elif permeability_md > 500:
            adj *= 1.05
            why.append(f"{permeability_md:.0f} mD: good rock, sweep is not the limit")
    if subsea_tieback:
        adj *= 0.9
        why.append("subsea tie-back: few wells, no infill drilling once the template slots are used, "
                   "and production stops when the host does")
    if drive in NATURAL_DRIVES and drive != "natural water drive" and fam == "oil":
        why.append("no pressure support: consider water or gas injection before accepting this")
    out = tuple(min(max(x * adj, 0.01), 0.95) for x in (lo, mid, hi))
    return dict(low=out[0], mid=out[1], high=out[2], drive=drive, fluid_family=fam,
                because="; ".join(why))


# ─────────────────────────────── volumetrics ───────────────────────────────

def stoiip_sm3(area_km2: float, thickness_m: float, ntg: float, porosity: float,
               water_saturation: float, bo: float = 1.25) -> float:
    """Oil in place: A·h·NTG·φ·(1−Sw)/Bo, in Sm³."""
    for name, v in (("area", area_km2), ("thickness", thickness_m), ("Bo", bo)):
        if v <= 0:
            raise ValueError(f"{name} must be > 0")
    for name, v in (("NTG", ntg), ("porosity", porosity), ("water saturation", water_saturation)):
        if not 0 <= v <= 1:
            raise ValueError(f"{name} must be a fraction between 0 and 1")
    return area_km2 * 1e6 * thickness_m * ntg * porosity * (1 - water_saturation) / bo


def giip_sm3(area_km2: float, thickness_m: float, ntg: float, porosity: float,
             water_saturation: float, bg: float = 0.004) -> float:
    """Gas in place, in Sm³. Bg is the gas formation volume factor (rm³/Sm³)."""
    if bg <= 0:
        raise ValueError("Bg must be > 0")
    return stoiip_sm3(area_km2, thickness_m, ntg, porosity, water_saturation, bo=bg)


def in_place_sm3(res, fluid_family: str = "") -> float:
    """In-place volume from a reservoir's volumetrics (0 when they are not filled in)."""
    fam = fluid_family or family(getattr(res, "fluid", "black oil"))
    area = float(getattr(res, "area_km2", 0.0) or 0.0)
    h = float(getattr(res, "thickness_m", 0.0) or 0.0)
    if area <= 0 or h <= 0:
        return 0.0
    ntg = float(getattr(res, "ntg", 0.7) or 0.0)
    phi = float(getattr(res, "porosity", 0.22) or 0.0)
    sw = float(getattr(res, "water_saturation", 0.25) or 0.0)
    fvf = float(getattr(res, "fvf", 0.0) or 0.0)
    if fvf <= 0:
        fvf = 1.25 if fam == "oil" else 0.004
    return stoiip_sm3(area, h, ntg, phi, sw, fvf)


def eur_sm3(res) -> dict:
    """{in_place, recovery_factor, eur} for one reservoir, with the suggestion if none is set."""
    fam = family(getattr(res, "fluid", "black oil"))
    ip = in_place_sm3(res, fam)
    rf = float(getattr(res, "recovery_factor", 0.0) or 0.0)
    suggested = suggest_recovery_factor(getattr(res, "fluid", "black oil"),
                                        getattr(res, "drive", "") or "")
    if rf <= 0:
        rf = suggested["mid"]
        source = "suggested"
    else:
        source = "entered"
    return dict(in_place_sm3=ip, recovery_factor=rf, eur_sm3=ip * rf, fluid_family=fam,
                rf_source=source, suggestion=suggested)


# ───────────────────────────── number of wells ─────────────────────────────

def wells_needed(plateau_sm3_d: float, well_rate_sm3_d: float, area_km2: float = 0.0,
                 drainage_area_km2: float = 4.0, uptime: float = 0.92) -> dict:
    """How many wells the plateau needs, and whether drainage or rate is binding.

    `drainage_area_km2` is what one well drains — 2–4 km² is typical for a good
    NCS sandstone, less in tight rock, more for a long horizontal well.
    """
    if well_rate_sm3_d <= 0:
        raise ValueError("the rate per well must be > 0")
    by_rate = math.ceil(plateau_sm3_d / (well_rate_sm3_d * max(uptime, 0.01)))
    by_area = math.ceil(area_km2 / drainage_area_km2) if area_km2 > 0 and drainage_area_km2 > 0 else 0
    n = max(by_rate, by_area, 1)
    binding = "rate" if by_rate >= max(by_area, 1) else "drainage area"
    return dict(wells=n, by_rate=by_rate, by_drainage=by_area, binding=binding,
                note=(f"{by_rate} well(s) to hold {plateau_sm3_d:,.0f} Sm³/d at "
                      f"{well_rate_sm3_d:,.0f} Sm³/d each and {uptime:.0%} uptime"
                      + (f"; {by_area} to drain {area_km2:.1f} km² at {drainage_area_km2:.1f} km² per well"
                         if by_area else "")))


# ────────────────────────────── the profile ────────────────────────────────

@dataclass
class ProfileSettings:
    """How the field is produced, once it is on stream."""
    first_production_year: int = 2030
    plateau_years: float = 3.0
    decline_fraction_per_year: float = 0.15     # exponential decline after plateau
    hyperbolic_b: float = 0.0                   # 0 = exponential; 0.3–0.7 is typical for oil
    uptime: float = 0.92
    economic_cutoff_sm3_d: float = 40.0         # field rate below which production stops
    max_years: int = 30
    ramp_up_years: float = 0.5                  # first year at a fraction of plateau

    def __post_init__(self):
        if not 0 < self.uptime <= 1:
            raise ValueError("uptime must be in (0, 1]")
        if self.decline_fraction_per_year <= 0 or self.decline_fraction_per_year >= 1:
            raise ValueError("decline must be a fraction between 0 and 1")
        if self.plateau_years < 0 or self.max_years <= 0:
            raise ValueError("plateau and horizon must be positive")
        if not 0 <= self.hyperbolic_b < 1.5:
            raise ValueError("hyperbolic b must be in [0, 1.5)")


def profile(eur: float, plateau_rate_sm3_d: float, s: Optional[ProfileSettings] = None,
            capacity_sm3_d: float = 0.0) -> dict:
    """Plateau then decline, year by year, never producing more than the EUR.

    `capacity_sm3_d` caps the rate (the host's capacity, or a line's). The
    result says how much of the EUR was actually recovered inside the horizon,
    so a plateau that cannot be held, or a cut-off that stops production early,
    shows up as a number rather than being quietly rounded away.
    """
    s = s or ProfileSettings()
    if eur <= 0 or plateau_rate_sm3_d <= 0:
        return dict(years=[], eur_sm3=max(eur, 0.0), recovered_sm3=0.0, plateau_sm3_d=0.0,
                    capped_by_capacity=False, field_life_years=0, note="no EUR or no rate")
    plateau = plateau_rate_sm3_d
    capped = bool(capacity_sm3_d and capacity_sm3_d < plateau)
    if capped:
        plateau = capacity_sm3_d
    rows, cum, rate = [], 0.0, plateau
    di = s.decline_fraction_per_year
    b = s.hyperbolic_b
    t_decline = 0.0
    for i in range(int(s.max_years)):
        year = s.first_production_year + i
        if i == 0 and s.ramp_up_years > 0:
            frac = max(0.0, 1.0 - s.ramp_up_years / 2.0)          # average over the first year
        else:
            frac = 1.0
        if i < s.plateau_years:
            rate_year = plateau
        else:
            t_decline += 1.0
            rate_year = (plateau * math.exp(-di * (t_decline - 0.5)) if b <= 0 else
                         plateau / (1 + b * di * (t_decline - 0.5)) ** (1 / b))
        rate_year = min(rate_year, capacity_sm3_d) if capacity_sm3_d else rate_year
        vol = rate_year * frac * DAYS_PER_YEAR * s.uptime
        if cum + vol >= eur:                                        # the tank runs out mid-year
            vol = eur - cum
            rate_year = vol / (DAYS_PER_YEAR * s.uptime) if vol > 0 else 0.0
        if rate_year < s.economic_cutoff_sm3_d or vol <= 0:
            break
        cum += vol
        rows.append(dict(year=year, rate_sm3_d=rate_year, volume_sm3=vol, cumulative_sm3=cum,
                         on_plateau=i < s.plateau_years))
        if cum >= eur - 1e-6:
            break
    note = ""
    if rows and cum < eur * 0.999:
        note = (f"{cum / eur:.0%} of the EUR is produced inside {len(rows)} years — the rest sits below "
                f"the cut-off rate or beyond the horizon")
    if capped:
        note = (f"Plateau limited to {plateau:,.0f} Sm³/d by capacity. " + note).strip()
    return dict(years=rows, eur_sm3=eur, recovered_sm3=cum, plateau_sm3_d=plateau,
                capped_by_capacity=capped, field_life_years=len(rows),
                plateau_years=min(s.plateau_years, len(rows)), note=note)


def gas_to_oil_equivalent(gas_sm3: float) -> float:
    """Sm³ of gas as Sm³ oil equivalent (1 000 Sm³ gas = 1 Sm³ o.e.)."""
    return gas_sm3 * BOE_PER_SM3_GAS


def field_profile(layout, settings: Optional[ProfileSettings] = None, fa_settings=None,
                  well_rates: Optional[Dict[str, float]] = None) -> dict:
    """The whole layout's profile: one stream per reservoir, then the field.

    Plateau comes from the wells' design rates (what the flow-assurance model
    says they deliver), EUR from each reservoir's volumetrics and recovery
    factor, and the cap from the host's liquid and gas capacity.
    """
    import tb_fluids
    import tb_flowassurance as tb_fa
    s = settings or ProfileSettings()
    fas = fa_settings or tb_fa.FASettings()
    res = tb_fluids.reservoirs(layout)
    w_in = tb_fa.well_inputs(layout)
    rates = well_rates or {w: v.oil_sm3_d for w, v in w_in.items()}
    # producers only, grouped by the reservoir they are assigned to
    groups: Dict[str, List[str]] = {}
    for wid in rates:
        if wid not in layout.nodes or layout.kind(wid) != "well":
            continue
        if tb_fluids.is_injector(layout, wid):
            continue
        groups.setdefault(layout.nodes[wid].attrs.get("reservoir") or "(unassigned)", []).append(wid)
    streams, unassigned = [], []
    for rname, wells in sorted(groups.items()):
        plateau = sum(rates.get(w, 0.0) for w in wells)
        r = res.get(rname)
        if r is None:
            unassigned.append(rname)
            continue
        e = eur_sm3(r)
        fam = e["fluid_family"]
        cap = (fas.host_liquid_capacity_sm3_d if fam == "oil"
               else fas.host_gas_capacity_msm3_d * 1e6 / max(sum(1 for _ in groups), 1))
        gor = float(getattr(r, "gor_sm3_sm3", 0.0) or 0.0)
        p = profile(e["eur_sm3"], plateau, s, capacity_sm3_d=0.0)
        streams.append(dict(e, reservoir=rname, wells=wells, fluid=getattr(r, "fluid", ""),
                            gor_sm3_sm3=gor, plateau_sm3_d=plateau,
                            host_capacity_sm3_d=cap, profile=p))
    years: Dict[int, dict] = {}
    for st_ in streams:
        gor = st_["gor_sm3_sm3"]
        for row in st_["profile"]["years"]:
            y = years.setdefault(row["year"], dict(year=row["year"], oil_sm3_d=0.0, gas_msm3_d=0.0,
                                                   oil_sm3=0.0, gas_sm3=0.0, boe_sm3=0.0))
            if st_["fluid_family"] == "oil":
                y["oil_sm3_d"] += row["rate_sm3_d"]
                y["oil_sm3"] += row["volume_sm3"]
                y["gas_msm3_d"] += row["rate_sm3_d"] * gor / 1e6
                y["gas_sm3"] += row["volume_sm3"] * gor
            else:
                y["gas_msm3_d"] += row["rate_sm3_d"] * max(gor, 1.0) / 1e6
                y["gas_sm3"] += row["volume_sm3"] * max(gor, 1.0)
                y["oil_sm3_d"] += row["rate_sm3_d"]              # condensate carried as liquid
                y["oil_sm3"] += row["volume_sm3"]
    # host capacity applies to the field, not to one reservoir
    capped_years = []
    for y in sorted(years.values(), key=lambda d: d["year"]):
        if fas.host_liquid_capacity_sm3_d and y["oil_sm3_d"] > fas.host_liquid_capacity_sm3_d:
            k = fas.host_liquid_capacity_sm3_d / y["oil_sm3_d"]
            for f_ in ("oil_sm3_d", "oil_sm3", "gas_msm3_d", "gas_sm3"):
                y[f_] *= k
            capped_years.append(y["year"])
        if fas.host_gas_capacity_msm3_d and y["gas_msm3_d"] > fas.host_gas_capacity_msm3_d:
            k = fas.host_gas_capacity_msm3_d / y["gas_msm3_d"]
            for f_ in ("oil_sm3_d", "oil_sm3", "gas_msm3_d", "gas_sm3"):
                y[f_] *= k
            capped_years.append(y["year"])
        y["boe_sm3"] = y["oil_sm3"] + gas_to_oil_equivalent(y["gas_sm3"])
    rows = sorted(years.values(), key=lambda d: d["year"])
    return dict(streams=streams, years=rows, unassigned_reservoirs=sorted(set(unassigned)),
                total_oil_sm3=sum(r["oil_sm3"] for r in rows),
                total_gas_sm3=sum(r["gas_sm3"] for r in rows),
                total_boe_sm3=sum(r["boe_sm3"] for r in rows),
                capped_years=sorted(set(capped_years)),
                first_year=rows[0]["year"] if rows else s.first_production_year,
                last_year=rows[-1]["year"] if rows else s.first_production_year,
                note=("No reservoir volumetrics: set area, thickness, porosity and a recovery factor "
                      "under Reservoirs and well fluids." if not streams else ""))
