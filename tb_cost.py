"""
tb_cost.py — CAPEX estimate for TieBack Studio.

Deterministic build-up per element:
    q            = 1 (unit) | L (per_m) | L·D (per_inch_m)
    procurement  = rate_proc · q
    fabrication  = rate_fab  · q
    engineering  = eng_frac · (procurement + fabrication)
    offshore days= install_days (unit)  |  install_days_per_km · L/1000 (linear)
    installation = days · weather_factor · spread day rate
Project-level adders:
    mob/demob    = one per (spread, phase) that is used
    survey & pre-commissioning = frac · installation
    owner's cost = frac · (all of the above)
    contingency  = frac · base (deterministic estimate only)

Per-element user control: `factor` (multiplier) and `override_usd`
(replaces the element's direct cost: proc+fab+eng+install).

Monte Carlo: triangular multipliers per item (proc/fab/eng) and per spread
(shared across all elements on that spread in an iteration → correlated
marine exposure). Reported percentiles are *non-exceedance*: P10 low, P90 high.
Contingency is excluded from the simulation (the distribution replaces it).

Internal currency: USD.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

CATEGORIES = ("Procurement", "Fabrication", "Engineering & PM", "Installation",
              "Mob/demob", "Survey & pre-commissioning", "Owner's costs", "Contingency")


@dataclass
class CostSettings:
    weather_factor: float = 1.25
    survey_precomm_frac: float = 0.08
    owners_cost_frac: float = 0.05
    contingency_frac: float = 0.15
    piggyback_install_frac: float = 0.3   # strapped lines add this share of their own lay time
    element_factor: Dict[str, float] = field(default_factory=dict)
    element_override_usd: Dict[str, float] = field(default_factory=dict)
    currency_rate: float = 1.0     # display multiplier only (e.g. NOK per USD)


def _q(item, row) -> float:
    if item.cost_basis == "unit":
        return 1.0
    if item.cost_basis == "per_m":
        return row["length_m"]
    return row["length_m"] * row["diameter_in"]


def _offshore_days(item, row) -> float:
    if item.is_linear:
        return item.install_days * row["length_m"] / 1000.0
    return item.install_days


HIPPS_ITEM = "hipps_mod"


def hipps_rows(layout) -> List[dict]:
    """HIPPS ticked on a structure costs a HIPPS valve skid, priced as the catalogue's HIPPS module.

    A node that *is* a HIPPS module is already costed as itself. The row carries
    `element_id` "<node>_HIPPS" and is installed with its structure.
    """
    cat = layout.catalog
    if HIPPS_ITEM not in cat.items:
        return []
    rows = []
    for n in layout.nodes.values():
        if n.hipps and n.item_id != HIPPS_ITEM and cat.get(n.item_id).category != "host":
            it = cat.get(HIPPS_ITEM)
            rows.append(dict(element_id=f"{n.node_id}_HIPPS", label=f"HIPPS on {n.label or n.node_id}",
                             item_id=HIPPS_ITEM, item=it.name, category=it.category, basis="unit",
                             quantity=1.0, length_m=0.0, diameter_in=0.0, phase=n.phase, piggyback_on=None))
    return rows


def estimate(layout, settings: Optional[CostSettings] = None) -> dict:
    s = settings or CostSettings()
    cat = layout.catalog
    lines = []
    spreads_used = set()
    for row in layout.quantities() + hipps_rows(layout):
        it = cat.get(row["item_id"])
        q = _q(it, row)
        proc = it.procurement_usd * q
        fab = it.fabrication_usd * q
        eng = it.engineering_frac * (proc + fab)
        days = _offshore_days(it, row) * (1.0 if it.install_spread == "host" else s.weather_factor)
        piggy = bool(row.get("piggyback_on"))
        if piggy:
            days *= s.piggyback_install_frac      # laid with the carrier, not a separate campaign
        spread = cat.spreads[it.install_spread]
        inst = days * spread.day_rate_usd
        if days > 0 and not piggy:
            spreads_used.add((it.install_spread, row["phase"]))
        f = s.element_factor.get(row["element_id"], 1.0)
        proc, fab, eng, inst = proc * f, fab * f, eng * f, inst * f
        direct = proc + fab + eng + inst
        overridden = row["element_id"] in s.element_override_usd
        if overridden:
            target = s.element_override_usd[row["element_id"]]
            k = target / direct if direct > 0 else 0.0
            if direct > 0:
                proc, fab, eng, inst = proc * k, fab * k, eng * k, inst * k
            else:
                proc = target
        lines.append(dict(row, spread=it.install_spread, piggyback=piggy, quantity_basis=q, offshore_days=days,
                          procurement=proc, fabrication=fab, engineering=eng, installation=inst,
                          direct=proc + fab + eng + inst, overridden=overridden, factor=f))

    mob = defaultdict(float)
    for key, ph in spreads_used:
        mob[ph] += cat.spreads[key].mob_demob_usd
    tot = defaultdict(float)
    for ln in lines:
        tot["Procurement"] += ln["procurement"]
        tot["Fabrication"] += ln["fabrication"]
        tot["Engineering & PM"] += ln["engineering"]
        tot["Installation"] += ln["installation"]
    tot["Mob/demob"] = sum(mob.values())
    tot["Survey & pre-commissioning"] = s.survey_precomm_frac * tot["Installation"]
    pre_owner = sum(tot[c] for c in CATEGORIES[:6])
    tot["Owner's costs"] = s.owners_cost_frac * pre_owner
    base = pre_owner + tot["Owner's costs"]
    tot["Contingency"] = s.contingency_frac * base
    return dict(lines=lines, by_category={c: tot[c] for c in CATEGORIES}, base_usd=base,
                total_usd=base + tot["Contingency"], mob_by_phase=dict(mob),
                spreads_used=sorted(spreads_used))


def _tri(rng, lo, ml, hi, n):
    if hi - lo < 1e-12:
        return np.full(n, ml)
    return rng.triangular(lo, ml, hi, n)


def monte_carlo(layout, settings: Optional[CostSettings] = None, n: int = 5000, seed: int = 42) -> dict:
    """Probabilistic base cost (excl. contingency)."""
    s = settings or CostSettings()
    det = estimate(layout, s)
    cat = layout.catalog
    rng = np.random.default_rng(seed)
    spread_mult = {k: _tri(rng, *sp.uncertainty, n) for k, sp in cat.spreads.items()}
    item_mult: Dict[str, np.ndarray] = {}
    total = np.zeros(n)
    install_total = np.zeros(n)
    for ln in det["lines"]:
        it = cat.get(ln["item_id"])
        if ln["item_id"] not in item_mult:
            item_mult[ln["item_id"]] = _tri(rng, *it.uncertainty, n)
        m_item = item_mult[ln["item_id"]]
        m_spread = spread_mult[ln["spread"]]
        total += (ln["procurement"] + ln["fabrication"] + ln["engineering"]) * m_item
        inst = ln["installation"] * m_spread
        total += inst
        install_total += inst
    mob = det["by_category"]["Mob/demob"]
    # mob/demob scales with average spread uncertainty of used spreads
    used = {k for k, _ in det["spreads_used"]}
    mob_mult = np.mean([spread_mult[k] for k in used], axis=0) if used else np.ones(n)
    total += mob * mob_mult
    total += s.survey_precomm_frac * install_total
    total *= (1.0 + s.owners_cost_frac)
    pct = {f"P{p}": float(np.percentile(total, p)) for p in (10, 50, 90)}
    return dict(samples=total, mean=float(total.mean()), **pct,
                deterministic_base=det["base_usd"], deterministic_total=det["total_usd"])


# ───────────────────────────── time phasing ────────────────────────────────


def _month_index(d: dt.date, origin: dt.date) -> float:
    return (d.year - origin.year) * 12 + (d.month - origin.month) + (d.day - 1) / 31.0


def _spread(profile: np.ndarray, start: float, end: float, amount: float):
    """Uniformly distribute `amount` over fractional month interval [start, end)."""
    if amount == 0:
        return
    if end - start < 1e-9:
        profile[int(min(max(start, 0), len(profile) - 1))] += amount
        return
    rate = amount / (end - start)
    for m in range(int(np.floor(start)), int(np.ceil(end))):
        lo, hi = max(start, m), min(end, m + 1)
        if hi > lo and 0 <= m < len(profile):
            profile[m] += rate * (hi - lo)


def phase_costs(estimate_result: dict, schedule, element_map: dict, settings: Optional[CostSettings] = None,
                award_frac: float = 0.15) -> dict:
    """Monthly and annual CAPEX profile (USD) consistent with the estimate totals.

    Procurement: `award_frac` at procurement start, remainder linear over the
    procurement activity. Fabrication: linear over the second half of it.
    Engineering: linear over procurement. Installation/mob/survey: over the
    installation activity. Owner's costs + contingency: pro-rata to the
    resulting direct profile.
    """
    s = settings or CostSettings()
    acts = schedule.activities
    origin = dt.date(schedule.start_date.year, 1, 1)
    horizon = int(np.ceil(_month_index(schedule.finish, origin))) + 2
    prof = np.zeros(horizon)
    unphased = 0.0
    for ln in estimate_result["lines"]:
        m = element_map.get(ln["element_id"]) or element_map.get(str(ln["element_id"]).rsplit("_HIPPS", 1)[0], {})
        pa, ia = acts.get(m.get("procure")), acts.get(m.get("install"))
        if pa is not None:
            a0, a1 = _month_index(pa.es, origin), _month_index(pa.ef, origin)
            _spread(prof, a0, a0, ln["procurement"] * award_frac)
            _spread(prof, a0, a1, ln["procurement"] * (1 - award_frac) + ln["engineering"])
            _spread(prof, (a0 + a1) / 2, a1, ln["fabrication"])
        else:
            unphased += ln["procurement"] + ln["engineering"] + ln["fabrication"]
        if ia is not None:
            i0, i1 = _month_index(ia.es, origin), _month_index(ia.ef, origin)
            inst = ln["installation"] * (1 + s.survey_precomm_frac)
            _spread(prof, i0, i1, inst)
        else:
            unphased += ln["installation"] * (1 + s.survey_precomm_frac)
    # mob/demob at the first installation activity of each phase
    for ph, amt in estimate_result["mob_by_phase"].items():
        inst_acts = [a for a in acts.values() if a.phase == ph and a.group == "Installation"]
        if inst_acts:
            first = min(inst_acts, key=lambda a: a.es)
            _spread(prof, _month_index(first.es, origin), _month_index(first.ef, origin), amt)
        else:
            unphased += amt
    if unphased > 0:  # fall back: spread over whole project
        _spread(prof, 0, _month_index(schedule.finish, origin), unphased)
    direct_total = prof.sum()
    target = estimate_result["total_usd"]
    if direct_total > 0:
        prof *= target / direct_total
    years = [origin.year + i // 12 for i in range(horizon)]
    annual = defaultdict(float)
    for y, v in zip(years, prof):
        annual[y] += float(v)
    return dict(monthly=prof, origin=origin, annual=dict(sorted(annual.items())),
                total_usd=float(prof.sum()))
