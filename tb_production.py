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


def _num(value, default: float) -> float:
    """A blank cell means "use the default"; an entered 0 means 0."""
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def volumetric_in_place_sm3(res, fluid_family: str = "") -> float:
    """In-place volume from a reservoir's volumetrics (0 when they are not filled in)."""
    fam = fluid_family or family(getattr(res, "fluid", "black oil"))
    area = _num(getattr(res, "area_km2", 0.0), 0.0)
    h = _num(getattr(res, "thickness_m", 0.0), 0.0)
    if area <= 0 or h <= 0:
        return 0.0
    ntg = _num(getattr(res, "ntg", None), 0.70)
    phi = _num(getattr(res, "porosity", None), 0.22)
    sw = _num(getattr(res, "water_saturation", None), 0.25)
    fvf = _num(getattr(res, "fvf", None), 0.0)
    if fvf <= 0:
        fvf = 1.25 if fam == "oil" else 0.004
    return stoiip_sm3(area, h, ntg, phi, sw, fvf)


def in_place_sm3(res, fluid_family: str = "") -> float:
    """STOIIP / GIIP in Sm³: the entered volume when there is one, else the volumetrics."""
    entered = _num(getattr(res, "in_place_sm3", 0.0), 0.0)
    if entered > 0:
        return entered
    return volumetric_in_place_sm3(res, fluid_family)


def eur_sm3(res) -> dict:
    """{in_place, recovery_factor, eur} for one reservoir, with the suggestion if none is set."""
    fam = family(getattr(res, "fluid", "black oil"))
    ip = in_place_sm3(res, fam)
    ip_source = ("entered" if _num(getattr(res, "in_place_sm3", 0.0), 0.0) > 0 else
                 ("volumetrics" if ip > 0 else "missing"))
    rf = float(getattr(res, "recovery_factor", 0.0) or 0.0)
    suggested = suggest_recovery_factor(getattr(res, "fluid", "black oil"),
                                        getattr(res, "drive", "") or "")
    if rf <= 0:
        rf = suggested["mid"]
        source = "suggested"
    else:
        source = "entered"
    return dict(in_place_sm3=ip, in_place_source=ip_source, recovery_factor=rf, eur_sm3=ip * rf,
                fluid_family=fam, rf_source=source, suggestion=suggested)


# How a drainage strategy is normally produced, as two numbers:
#   offtake  — plateau rate as a fraction of the EUR per year (production days, i.e. after uptime)
#   end      — fraction of the EUR produced before the decline starts
# Pressure support holds the plateau longer and at a gentler offtake; depletion comes off plateau
# early. Screening defaults to argue with, editable per reservoir.
DRIVE_PROFILE: Dict[str, tuple] = {
    "solution gas / depletion": (0.15, 0.25),
    "gas cap expansion": (0.12, 0.35),
    "natural water drive": (0.12, 0.40),
    "water injection": (0.10, 0.45),
    "gas injection / WAG": (0.10, 0.45),
    "pressure depletion (gas)": (0.10, 0.55),
    "water drive (gas)": (0.10, 0.45),
}


def drive_profile(res) -> dict:
    """Offtake and plateau length for a reservoir: its own values, else its strategy's defaults."""
    drive = getattr(res, "drive", "") or default_drive(getattr(res, "fluid", "black oil"))
    d_off, d_end = DRIVE_PROFILE.get(drive, DRIVE_PROFILE[default_drive(getattr(res, "fluid", "black oil"))])
    off = _num(getattr(res, "plateau_offtake", 0.0), 0.0)
    end = _num(getattr(res, "plateau_end_frac", 0.0), 0.0)
    return dict(drive=drive,
                offtake=off if 0 < off <= 1 else d_off, offtake_source="entered" if 0 < off <= 1 else "strategy",
                end_frac=end if 0 < end < 1 else d_end, end_source="entered" if 0 < end < 1 else "strategy",
                default_offtake=d_off, default_end_frac=d_end)


# ───────────────────────────── number of wells ─────────────────────────────

def wells_needed(plateau_sm3_d: float, well_rate_sm3_d: float, area_km2: float = 0.0,
                 drainage_area_km2: float = 4.0, uptime: float = 1.0) -> dict:
    """How many wells the plateau needs, and whether drainage or rate is binding.

    Both rates are instantaneous (Sm³/d while flowing), the same convention the
    profile uses: uptime turns a rate into an annual volume, it does not change
    how many wells hold the plateau. Leave `uptime` at 1 unless you mean "hold
    this plateau as a yearly average", which does take more wells.

    `drainage_area_km2` is what one well drains — 2–4 km² is typical for a good
    NCS sandstone, less in tight rock, more for a long horizontal well.
    """
    if well_rate_sm3_d <= 0:
        raise ValueError("the rate per well must be > 0")
    by_rate = math.ceil(plateau_sm3_d / (well_rate_sm3_d * max(uptime, 0.01)))
    by_area = math.ceil(area_km2 / drainage_area_km2) if area_km2 > 0 and drainage_area_km2 > 0 else 0
    n = max(by_rate, by_area, 1)
    binding = ("both" if by_area and by_rate == by_area else
               ("rate" if by_rate > by_area else "drainage area"))
    return dict(wells=n, by_rate=by_rate, by_drainage=by_area, binding=binding,
                note=(f"{by_rate} well(s) to hold {plateau_sm3_d:,.0f} Sm³/d at "
                      f"{well_rate_sm3_d:,.0f} Sm³/d each"
                      + (f" at {uptime:.0%} uptime" if uptime < 0.999 else "")
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
    limit_by_wells: bool = True                 # the plateau can never exceed the wells' design rates

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
    potential = plateau_rate_sm3_d              # what the wells can deliver
    capped = bool(capacity_sm3_d and capacity_sm3_d < potential)
    rows, cum = [], 0.0
    di = s.decline_fraction_per_year
    b = s.hyperbolic_b
    t_decline = 0.0
    for i in range(int(s.max_years)):
        year = s.first_production_year + i
        frac = max(0.0, 1.0 - s.ramp_up_years / 2.0) if (i == 0 and s.ramp_up_years > 0) else 1.0
        if i < s.plateau_years:
            rate_year = potential
        else:
            t_decline += 1.0
            rate_year = (potential * math.exp(-di * (t_decline - 0.5)) if b <= 0 else
                         potential / (1 + b * di * (t_decline - 0.5)) ** (1 / b))
        # A capacity limit does not throw the volume away: the field produces at the limit for as
        # long as the wells can hold it, so the plateau stretches and the decline starts later.
        if capacity_sm3_d:
            rate_year = min(rate_year, capacity_sm3_d)
        rate_year *= frac                        # the ramp belongs to the rate, not only the volume
        vol = rate_year * DAYS_PER_YEAR * s.uptime
        if cum + vol >= eur:                                        # the tank runs out mid-year
            vol = eur - cum
            rate_year = vol / (DAYS_PER_YEAR * s.uptime) if vol > 0 else 0.0
        if rate_year < s.economic_cutoff_sm3_d or vol <= 0:
            break
        cum += vol
        rows.append(dict(year=year, rate_sm3_d=rate_year, volume_sm3=vol, cumulative_sm3=cum,
                         on_plateau=(not capacity_sm3_d and i < s.plateau_years)
                         or bool(capacity_sm3_d and rate_year >= capacity_sm3_d * frac - 1e-9)))
        if cum >= eur - 1e-6:
            break
    plateau = min(potential, capacity_sm3_d) if capacity_sm3_d else potential
    note = ""
    if rows and cum < eur * 0.999:
        note = (f"{cum / eur:.0%} of the EUR is produced inside {len(rows)} years — the rest sits below "
                f"the cut-off rate or beyond the horizon")
    if capped:
        note = (f"Capacity holds the rate at {plateau:,.0f} Sm³/d against a well potential of "
                f"{potential:,.0f} Sm³/d, so the plateau lasts "
                f"{sum(1 for r in rows if r['on_plateau'])} years. " + note).strip()
    return dict(years=rows, eur_sm3=eur, recovered_sm3=cum, plateau_sm3_d=plateau,
                capped_by_capacity=capped, field_life_years=len(rows),
                plateau_years=sum(1 for r in rows if r["on_plateau"]), note=note,
                potential_sm3_d=potential)


def strategy_profile(eur: float, offtake: float, end_frac: float, s: Optional[ProfileSettings] = None,
                     well_potential_sm3_d: float = 0.0, capacity_sm3_d: float = 0.0) -> dict:
    """The simple profile: in place × recovery factor, produced the way the strategy produces it.

    * plateau rate  q = offtake · EUR / (365.25 · uptime)   — capped by the wells and the host
    * plateau until end_frac · EUR has been produced
    * then exponential decline that closes on the EUR exactly: dN/dt = k·(EUR − N),
      k = q_year / (EUR − N_plateau), so the tail holds the rest of the volume and nothing more

    A lower plateau (fewer wells, a bottleneck) does not lose volume: it stays on plateau longer.
    The first `ramp_up_years` ramp linearly from zero.
    """
    s = s or ProfileSettings()
    empty = dict(years=[], eur_sm3=max(eur, 0.0), recovered_sm3=0.0, plateau_sm3_d=0.0,
                 capped_by_capacity=False, field_life_years=0, plateau_years=0, potential_sm3_d=0.0,
                 reservoir_rate_sm3_d=0.0, limited_by="", note="no EUR or no rate")
    if eur <= 0 or offtake <= 0:
        return empty
    end_frac = min(max(end_frac, 0.0), 0.95)
    q_res = offtake * eur / (DAYS_PER_YEAR * s.uptime)          # flowing rate the strategy asks for
    potential, limited = q_res, "reservoir offtake"
    if s.limit_by_wells and well_potential_sm3_d > 0 and well_potential_sm3_d < potential:
        potential, limited = well_potential_sm3_d, "wells"
    q = potential
    capped = bool(capacity_sm3_d and capacity_sm3_d < potential)
    if capped:
        q, limited = capacity_sm3_d, "host capacity"
    if q <= 0:
        return dict(empty, note="no rate")
    q_year = q * DAYS_PER_YEAR * s.uptime                     # Sm³ per year on plateau
    n_p = end_frac * eur
    k = q_year / max(eur - n_p, 1e-9)                          # 1/yr, decline closing on the EUR
    steps = 96
    dt = 1.0 / steps
    cum, rows = 0.0, []
    for i in range(int(s.max_years)):
        start = cum
        for j in range(steps):
            t_mid = i + (j + 0.5) * dt
            m = min(1.0, t_mid / s.ramp_up_years) if s.ramp_up_years > 0 else 1.0
            if cum < n_p:
                cum = min(cum + q_year * m * dt, n_p)
            else:
                cum = eur - (eur - cum) * math.exp(-k * m * dt)
        cum = min(cum, eur)
        vol = cum - start
        rate = vol / (DAYS_PER_YEAR * s.uptime)
        if rate < s.economic_cutoff_sm3_d or vol <= 0:
            cum = start
            break
        rows.append(dict(year=s.first_production_year + i, rate_sm3_d=rate, volume_sm3=vol,
                         cumulative_sm3=cum, on_plateau=cum <= n_p + 1e-6))
        if cum >= eur * (1 - 1e-9):
            break
    note = ""
    if rows and cum < eur * 0.995:
        note = (f"{cum / eur:.0%} of the EUR is produced inside {len(rows)} years — the rest sits below "
                f"the cut-off rate or beyond the horizon")
    if limited != "reservoir offtake":
        note = (f"The {limited} hold{'s' if limited == 'host capacity' else ''} the plateau at {q:,.0f} Sm³/d "
                f"against {q_res:,.0f} Sm³/d the strategy's offtake would take, so the plateau lasts longer. "
                + note).strip()
    return dict(years=rows, eur_sm3=eur, recovered_sm3=cum, plateau_sm3_d=q, capped_by_capacity=capped,
                field_life_years=len(rows), plateau_years=sum(1 for r in rows if r["on_plateau"]),
                potential_sm3_d=potential, reservoir_rate_sm3_d=q_res, limited_by=limited,
                offtake=offtake, end_frac=end_frac, note=note)


# ─────────────────────────── manual well profiles ───────────────────────────
# A well can carry its own profile (from a simulation run, or a partner's forecast) in place of
# its share of the calculated one. Rows: year, oil_sm3_d, gas_ksm3_d, water_sm3_d — calendar-day
# averages over the year (uptime already in them). Gas or water left blank follow the fluid's GOR
# and water cut. A year below 1900 is counted from first production (1 = the first year), so the
# profile moves with the schedule; a calendar year stays where it is.

MANUAL_COLUMNS = ("year", "oil_sm3_d", "gas_ksm3_d", "water_sm3_d")


def _blank(v) -> bool:
    if v is None or v == "":
        return True
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def clean_manual_profile(rows) -> List[dict]:
    """Plain, sorted rows with a year and at least one rate. Raises on a nonsense entry."""
    out, seen = [], set()
    for r in rows or []:
        r = dict(r)
        if _blank(r.get("year")):
            continue
        y = int(float(r["year"]))
        vals = {}
        for c_ in MANUAL_COLUMNS[1:]:
            v = r.get(c_)
            if _blank(v):
                vals[c_] = None
            else:
                v = float(v)
                if v < 0:
                    raise ValueError(f"year {y}: {c_} cannot be negative")
                vals[c_] = v
        if all(v is None for v in vals.values()):
            continue
        if y in seen:
            raise ValueError(f"year {y} appears twice")
        seen.add(y)
        out.append(dict(year=y, **vals))
    return sorted(out, key=lambda d: d["year"])


def manual_profile(layout, well_id: str) -> List[dict]:
    """The well's manual profile when it is switched on, else []."""
    a = layout.nodes[well_id].attrs if well_id in layout.nodes else {}
    if a.get("profile_mode") != "manual":
        return []
    try:
        return clean_manual_profile(a.get("manual_profile") or [])
    except (TypeError, ValueError):
        return []


def set_manual_profile(layout, well_id: str, rows, use: bool = True) -> List[dict]:
    rows = clean_manual_profile(rows)
    a = layout.nodes[well_id].attrs
    a["manual_profile"] = rows
    a["profile_mode"] = "manual" if (use and rows) else "calculated"
    return rows


def read_profile_csv(text: str) -> List[dict]:
    """A pasted or uploaded CSV/TSV with a header: year plus any of oil, gas (kSm³/d), water."""
    import csv, io
    sample = text.strip()
    dialect = "\t" if "\t" in sample.splitlines()[0] else (";" if sample.count(";") > sample.count(",") else ",")
    rd = csv.DictReader(io.StringIO(sample), delimiter=dialect)
    alias = {"year": "year", "yr": "year", "oil": "oil_sm3_d", "oil_sm3_d": "oil_sm3_d",
             "condensate": "oil_sm3_d", "liquid": "oil_sm3_d", "gas": "gas_ksm3_d",
             "gas_ksm3_d": "gas_ksm3_d", "water": "water_sm3_d", "water_sm3_d": "water_sm3_d"}
    rows = []
    for r in rd:
        row = {}
        for k, v in r.items():
            key = str(k or "").strip().lower().split(" ")[0].split("(")[0]
            if key in alias:
                row[alias[key]] = None if str(v).strip() == "" else str(v).strip().replace(",", ".") \
                    if dialect != "," else (None if str(v).strip() == "" else str(v).strip())
        rows.append(row)
    return clean_manual_profile(rows)


def gas_to_oil_equivalent(gas_sm3: float) -> float:
    """Sm³ of gas as Sm³ oil equivalent (1 000 Sm³ gas = 1 Sm³ o.e.)."""
    return gas_sm3 * BOE_PER_SM3_GAS


PHASES = (("oil_sm3", "oil_sm3_d", 1.0), ("gas_sm3", "gas_msm3_d", 1e-6), ("water_sm3", "water_sm3_d", 1.0))


def fit_to_capacity(rows: List[dict], liquid_cap_sm3_d: float = 0.0, gas_cap_msm3_d: float = 0.0,
                    uptime: float = 0.92, last_year: Optional[int] = None) -> dict:
    """Reshuffle a yearly profile so no year exceeds the host's liquid (oil + water) or gas capacity.

    Year by year, the volume offered is that year's own production plus whatever was held back
    before. If it does not fit, every phase is scaled by the same factor (so the mix — GOR, water
    cut — is what the wells were producing) and the rest is carried forward. Years are added after
    the last one until the backlog is produced, up to `last_year`; only what is still held back
    then is lost, and it is reported. Rates are rates while producing (volume / (365.25·uptime)).
    """
    per_day = DAYS_PER_YEAR * uptime
    rows = [dict(r) for r in sorted(rows, key=lambda d: d["year"])]
    if not rows or not (liquid_cap_sm3_d or gas_cap_msm3_d):
        for r in rows:
            r["boe_sm3"] = r["oil_sm3"] + gas_to_oil_equivalent(r["gas_sm3"])
            r.setdefault("held_back_boe_sm3", 0.0)
        return dict(years=rows, capped_years=[], reshuffled_boe_sm3=0.0, lost_boe_sm3=0.0, extended_years=0)
    last_year = last_year if last_year is not None else rows[-1]["year"] + 50
    by_year = {r["year"]: r for r in rows}
    backlog = dict(oil_sm3=0.0, gas_sm3=0.0, water_sm3=0.0)
    out, capped, extended = [], [], 0
    y = rows[0]["year"]
    while True:
        own = by_year.get(y)
        if own is None and (y > rows[-1]["year"]):
            if sum(backlog.values()) <= 1e-6 or y > last_year:
                break
            own = dict(year=y, oil_sm3=0.0, gas_sm3=0.0, water_sm3=0.0)
            extended += 1
        elif own is None:
            own = dict(year=y, oil_sm3=0.0, gas_sm3=0.0, water_sm3=0.0)
        offered = {k: own.get(k, 0.0) + backlog[k] for k in backlog}
        k_ = 1.0
        liq_d = (offered["oil_sm3"] + offered["water_sm3"]) / per_day
        gas_d = offered["gas_sm3"] / per_day / 1e6
        if liquid_cap_sm3_d and liq_d > liquid_cap_sm3_d:
            k_ = min(k_, liquid_cap_sm3_d / liq_d)
        if gas_cap_msm3_d and gas_d > gas_cap_msm3_d:
            k_ = min(k_, gas_cap_msm3_d / gas_d)
        if y > last_year:
            k_ = 0.0
        row = dict(own)
        for vol_k, rate_k, unit in PHASES:
            row[vol_k] = offered[vol_k] * k_
            row[rate_k] = row[vol_k] / per_day * unit
            backlog[vol_k] = offered[vol_k] - row[vol_k]
        held = backlog["oil_sm3"] + gas_to_oil_equivalent(backlog["gas_sm3"])
        row["held_back_boe_sm3"] = held
        row["boe_sm3"] = row["oil_sm3"] + gas_to_oil_equivalent(row["gas_sm3"])
        if k_ < 1.0 - 1e-9:
            capped.append(y)
        if row["boe_sm3"] > 0 or y <= rows[-1]["year"]:
            out.append(row)
        y += 1
    lost = backlog["oil_sm3"] + gas_to_oil_equivalent(backlog["gas_sm3"])
    # volume produced later than offered: each year's shortfall against its own production
    moved = sum(max(0.0, (by_year.get(r["year"], {}).get("oil_sm3", 0.0)
                          + gas_to_oil_equivalent(by_year.get(r["year"], {}).get("gas_sm3", 0.0))) - r["boe_sm3"])
                for r in out)
    return dict(years=out, capped_years=capped, reshuffled_boe_sm3=moved,
                lost_boe_sm3=lost if lost > 1e-6 else 0.0, extended_years=extended)


def field_profile(layout, settings: Optional[ProfileSettings] = None, fa_settings=None,
                  well_rates: Optional[Dict[str, float]] = None) -> dict:
    """The whole layout's profile: one stream per reservoir, one per manual well, then the field.

    A reservoir's stream is in place × recovery factor, produced at its drainage strategy's
    offtake (capped by its wells' design rates and its share of the host's capacity). A well with
    a manual profile is taken out of that: it brings its own rows, and the reservoir's calculated
    stream keeps the EUR share of the wells that are left.
    """
    import tb_fluids
    import tb_flowassurance as tb_fa
    s = settings or ProfileSettings()
    fas = fa_settings or tb_fa.FASettings()
    res = tb_fluids.reservoirs(layout)
    w_in = tb_fa.well_inputs(layout)
    rates = dict(well_rates or {w: v.oil_sm3_d for w, v in w_in.items()})
    producers = [w for w in layout.nodes if layout.kind(w) == "well" and not tb_fluids.is_injector(layout, w)]
    for w in producers:
        rates.setdefault(w, 0.0)
    manual = {w: manual_profile(layout, w) for w in producers}
    manual = {w: rows for w, rows in manual.items() if rows}
    groups: Dict[str, List[str]] = {}
    for wid in producers:
        groups.setdefault(layout.nodes[wid].attrs.get("reservoir") or "(unassigned)", []).append(wid)
    streams, unassigned, mismatch, over_eur = [], [], [], []
    plans = []
    for rname, wells in sorted(groups.items()):
        r = res.get(rname)
        calc_wells = [w for w in wells if w not in manual]
        if r is None:
            if calc_wells:
                unassigned.append(rname)
            continue
        e = eur_sm3(r)
        fam = e["fluid_family"]
        gor = float(getattr(r, "gor_sm3_sm3", 0.0) or 0.0)
        wc = min(max(float(getattr(r, "water_cut", 0.0) or 0.0), 0.0), 0.95)
        q_w = {w: rates.get(w, 0.0) for w in wells if w in w_in}
        q_tot = sum(q_w.values())
        if q_tot > 0 and gor > 0:
            well_gor = sum(w_in[w].gor_sm3_sm3 * q for w, q in q_w.items()) / q_tot
            if abs(well_gor - gor) / gor > 0.2:
                mismatch.append(f"{rname}: the wells carry a GOR of {well_gor:,.0f} Sm³/Sm³, the reservoir "
                                f"{gor:,.0f} — apply the reservoir to its wells so the profile and the "
                                f"flow solve describe the same fluid")
        # streams are carried as liquid (oil, or condensate for gas), the gas rebuilt from the GOR
        eur_liquid = e["eur_sm3"] if fam == "oil" else (e["eur_sm3"] / gor if gor > 0 else 0.0)
        share_calc = len(calc_wells) / len(wells) if wells else 0.0
        dp = drive_profile(r)
        plans.append(dict(r=r, rname=rname, wells=wells, calc_wells=calc_wells, e=e, fam=fam, gor=gor, wc=wc,
                          eur_liquid=eur_liquid, eur_calc=eur_liquid * share_calc, share_calc=share_calc,
                          dp=dp, potential=sum(rates.get(w, 0.0) for w in calc_wells)))
    # Host capacity is applied once, to the field total (fit_to_capacity below): shares fixed per
    # reservoir would leave one reservoir's spare capacity idle while another is held back.
    for pl in plans:
        rname, gor, wc = pl["rname"], pl["gor"], pl["wc"]
        p = dict(years=[], recovered_sm3=0.0, plateau_sm3_d=0.0, capped_by_capacity=False, plateau_years=0,
                 potential_sm3_d=0.0, limited_by="", note="all wells on manual profiles")
        cap_liquid = 0.0
        if pl["eur_calc"] > 0:
            p = strategy_profile(pl["eur_calc"], pl["dp"]["offtake"], pl["dp"]["end_frac"], s,
                                 well_potential_sm3_d=pl["potential"])
        streams.append(dict(pl["e"], reservoir=rname, kind="calculated", wells=pl["calc_wells"],
                            manual_wells=[w for w in pl["wells"] if w in manual],
                            fluid=getattr(pl["r"], "fluid", ""), gor_sm3_sm3=gor, water_cut=wc,
                            plateau_sm3_d=p["plateau_sm3_d"], eur_liquid_sm3=pl["eur_liquid"],
                            eur_calc_sm3=pl["eur_calc"], drive=pl["dp"]["drive"],
                            offtake=pl["dp"]["offtake"], end_frac=pl["dp"]["end_frac"],
                            host_capacity_sm3_d=cap_liquid, profile=p))
    # manual wells: their own rows, converted to the same yearly shape
    for wid, rows in sorted(manual.items()):
        rname = layout.nodes[wid].attrs.get("reservoir") or ""
        r = res.get(rname)
        wi = w_in.get(wid)
        gor = float(getattr(r, "gor_sm3_sm3", 0.0) or 0.0) if r else float(getattr(wi, "gor_sm3_sm3", 0.0) or 0.0)
        wc = (float(getattr(r, "water_cut", 0.0) or 0.0) if r else float(getattr(wi, "water_cut", 0.0) or 0.0))
        wc = min(max(wc, 0.0), 0.95)
        yrows, cum = [], 0.0
        for row in rows:
            year = row["year"] if row["year"] >= 1900 else s.first_production_year + row["year"] - 1
            oil_d = row["oil_sm3_d"] or 0.0
            gas_d = row["gas_ksm3_d"] * 1e3 if row["gas_ksm3_d"] is not None else oil_d * gor
            wat_d = row["water_sm3_d"] if row["water_sm3_d"] is not None else oil_d * wc / (1.0 - wc)
            vol = oil_d * DAYS_PER_YEAR
            cum += vol
            yrows.append(dict(year=year, rate_sm3_d=vol / (DAYS_PER_YEAR * s.uptime), volume_sm3=vol,
                              cumulative_sm3=cum, gas_sm3=gas_d * DAYS_PER_YEAR,
                              water_sm3=wat_d * DAYS_PER_YEAR, on_plateau=False))
        streams.append(dict(reservoir=rname or "(no reservoir)", kind="manual", wells=[wid], manual_wells=[wid],
                            fluid=getattr(r, "fluid", "") if r else "", gor_sm3_sm3=gor, water_cut=wc,
                            eur_sm3=0.0, eur_liquid_sm3=0.0, in_place_sm3=0.0, recovery_factor=0.0,
                            plateau_sm3_d=max((y["rate_sm3_d"] for y in yrows), default=0.0),
                            profile=dict(years=yrows, recovered_sm3=cum, capped_by_capacity=False,
                                         plateau_years=0, limited_by="manual", note="manual profile")))
    # a reservoir must not give more than its EUR, however it is split between manual and calculated
    for pl in plans:
        got = sum(st_["profile"]["recovered_sm3"] for st_ in streams
                  if st_["reservoir"] == pl["rname"])
        if pl["eur_liquid"] > 0 and got > pl["eur_liquid"] * 1.02:
            over_eur.append(f"{pl['rname']}: the profiles produce {got / 1e6:,.2f} MSm³ of liquid against an EUR of "
                            f"{pl['eur_liquid'] / 1e6:,.2f} MSm³ — the manual well profiles take more than "
                            f"their share")
    years: Dict[int, dict] = {}
    for st_ in streams:
        gor, wc_ = st_["gor_sm3_sm3"], st_["water_cut"]
        for row in st_["profile"]["years"]:
            y = years.setdefault(row["year"], dict(year=row["year"], oil_sm3_d=0.0, gas_msm3_d=0.0,
                                                   water_sm3_d=0.0, oil_sm3=0.0, gas_sm3=0.0,
                                                   water_sm3=0.0, boe_sm3=0.0))
            per_day = DAYS_PER_YEAR * s.uptime
            gas = row["gas_sm3"] if "gas_sm3" in row else row["volume_sm3"] * gor
            wat = row["water_sm3"] if "water_sm3" in row else row["volume_sm3"] * wc_ / (1.0 - wc_)
            y["oil_sm3_d"] += row["rate_sm3_d"]
            y["oil_sm3"] += row["volume_sm3"]
            y["gas_sm3"] += gas
            y["gas_msm3_d"] += gas / per_day / 1e6
            y["water_sm3"] += wat
            y["water_sm3_d"] += wat / per_day
    # Field level: the streams together may still ask for more than the host takes (manual
    # profiles, several reservoirs, rounding). What does not fit is held back and produced in the
    # next years with spare capacity — the field stays on plateau longer — never thrown away.
    first_ = min(years) if years else s.first_production_year
    fit = fit_to_capacity(sorted(years.values(), key=lambda d: d["year"]),
                          fas.host_liquid_capacity_sm3_d, fas.host_gas_capacity_msm3_d, s.uptime,
                          last_year=max([first_ + int(s.max_years) - 1] + list(years)))
    years = {r["year"]: r for r in fit["years"]}
    capped_years, deferred = fit["capped_years"], fit["lost_boe_sm3"]
    rows = sorted(years.values(), key=lambda d: d["year"])
    has_calc = any(st_["kind"] == "calculated" and st_["profile"]["years"] for st_ in streams)
    return dict(streams=streams, years=rows, unassigned_reservoirs=sorted(set(unassigned)),
                manual_wells=sorted(manual),
                total_oil_sm3=sum(r["oil_sm3"] for r in rows),
                total_gas_sm3=sum(r["gas_sm3"] for r in rows),
                total_water_sm3=sum(r["water_sm3"] for r in rows),
                total_boe_sm3=sum(r["boe_sm3"] for r in rows),
                capped_years=sorted(set(capped_years)), deferred_boe_sm3=deferred,
                reshuffled_boe_sm3=fit["reshuffled_boe_sm3"], extended_years=fit["extended_years"],
                fluid_mismatch=mismatch, over_eur=over_eur,
                first_year=rows[0]["year"] if rows else s.first_production_year,
                last_year=rows[-1]["year"] if rows else s.first_production_year,
                note=("" if (has_calc or manual) else
                      "No in-place volume: enter STOIIP/GIIP (or the volumetrics) and a recovery factor "
                      "for a reservoir, and assign the wells to it — or give a well a manual profile."))
