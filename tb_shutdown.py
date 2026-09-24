"""
tb_shutdown.py — Planned shutdown and blowdown (depressurisation) screening.

Two questions a tie-back concept has to answer before DG2:

1. **Planned shutdown.** What has to happen, in what order, so the line is safe
   to leave: inhibit or displace the fluid in it, close in the wells, and — if
   the stop will outlast the cool-down time — depressurise. `planned_shutdown`
   sizes each step (inhibitor volume, displacement volume, time) against the
   injection and pumping capacity you give it.

2. **Blowdown.** Can the line be brought below the hydrate pressure at seabed
   temperature, and how long does it take? `blowdown` works out the hydrate-free
   pressure, the lowest pressure the host can actually reach at the seabed (flare
   back-pressure plus the liquid head standing in the riser — often the show
   stopper on a deep or liquid-rich tie-back), and the venting time through the
   host's blowdown restriction.

Method, deliberately simple and stated so it can be argued with:

* The line is one lumped, isothermal volume at seabed temperature, starting at
  the settle-out pressure (the volume-weighted mean of the flowing pressures).
* Gas vents through an orifice at the host — choked while the line is above the
  critical pressure ratio, subsonic below it — and never faster than the flare
  allows. Z from Brill & Beggs; the hydrate curve from Towler & Mokhatab, the
  same correlation the rest of the app uses.
* Gas that comes out of solution as an oil line depressurises is not included,
  and neither is friction along a long line, which makes the far end lag the
  host. Both make a real blowdown slower: treat the time as a lower bound and
  confirm it with a transient simulation (OLGA/LedaFlow).

Units: bara, °C, m³, hours.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import tb_multiphase as mp
import tb_thermal as th

R = 8.314462                 # J/(mol·K)
MW_AIR = 28.9647             # g/mol
G = 9.80665
PSIA_PER_BAR = 14.503774
RHO_WATER = 1030.0


@dataclass
class ShutdownSettings:
    hydrate_margin_c: float = 3.0            # stay this far outside the hydrate curve
    blowdown_orifice_mm: float = 50.0        # effective restriction at the host blowdown valve
    discharge_coefficient: float = 0.8
    flare_back_pressure_bara: float = 3.0
    flare_max_kg_s: float = 20.0             # what the host flare can take from this line
    gas_k: float = 1.27                      # cp/cv
    inhibitor_injection_m3_h: float = 2.0    # umbilical chemical-injection capacity
    displacement_rate_m3_h: float = 100.0    # dead oil / diesel pumping from the host
    isolation_hours: float = 0.5             # close wells, SSIV and host ESD valves
    lean_meg_wt_pct: float = 90.0

    def __post_init__(self):
        for f in ("blowdown_orifice_mm", "discharge_coefficient", "flare_max_kg_s",
                  "inhibitor_injection_m3_h", "displacement_rate_m3_h"):
            if getattr(self, f) <= 0:
                raise ValueError(f"{f} must be > 0")
        if self.flare_back_pressure_bara < 1.0:
            raise ValueError("flare back-pressure must be at least 1 bara")


# ───────────────────────────── hydrate pressure ────────────────────────────

def hydrate_pressure_bara(t_c: float, gas_sg: float) -> float:
    """Pressure at which hydrates form at temperature `t_c` (Towler & Mokhatab inverted).

    Below this pressure the line is outside the hydrate region at that temperature.
    """
    t_f = th.c_to_f(t_c)
    lg = math.log(max(gas_sg, 0.55))
    lp = (t_f + 20.35 - 34.27 * lg) / (13.47 - 1.675 * lg)
    return math.exp(lp) / PSIA_PER_BAR


# ─────────────────────────────── inventory ─────────────────────────────────

def _liquid_density(api: float, water_cut: float) -> float:
    rho_o = 141.5 / (api + 131.5) * 1000.0
    wc = min(max(water_cut, 0.0), 1.0)
    return rho_o * (1 - wc) + RHO_WATER * wc


def inventory(layout, result, settings=None, wells=None) -> dict:
    """Volume, liquid and pressure of the production system the host would blow down."""
    import tb_flowassurance as tb_fa
    fas = settings or tb_fa.FASettings()
    w_in = wells if wells is not None else tb_fa.well_inputs(layout)
    tot = sum(w.oil_sm3_d for w in w_in.values()) or 1.0
    api = sum(w.api * w.oil_sm3_d for w in w_in.values()) / tot if w_in else 35.0
    sg = sum(w.gas_sg * w.oil_sm3_d for w in w_in.values()) / tot if w_in else 0.7
    water = sum(w.oil_sm3_d * w.water_cut / max(1 - w.water_cut, 1e-6) for w in w_in.values())
    wc = water / (tot + water) if w_in else 0.1
    vol = liq = pv = 0.0
    riser_vol = riser_liq = 0.0
    depth = 0.0
    cat = layout.catalog
    for eid, r in result.edges.items():
        v = math.pi / 4 * (r.d_in * 0.0254) ** 2 * r.length_m
        vol += v
        liq += min(r.liquid_inventory_m3, v)
        pv += v * (r.p_in_bara + r.p_out_bara) / 2
        if cat.get(layout.edges[eid].item_id).category == "riser":
            riser_vol += v
            riser_liq += min(r.liquid_inventory_m3, v)
            for nid in (layout.edges[eid].from_node, layout.edges[eid].to_node):
                depth = max(depth, layout.nodes[nid].water_depth_m)
    if riser_vol and not depth:
        depth = fas.default_water_depth_m
    cooldowns = [r.cooldown_h for r in result.edges.values() if not r.heated and not math.isinf(r.cooldown_h)]
    return dict(volume_m3=vol, liquid_m3=liq, gas_volume_m3=max(vol - liq, 0.05 * vol),
                water_m3=liq * wc, settle_out_bara=pv / vol if vol else 0.0,
                riser_volume_m3=riser_vol, riser_holdup=(riser_liq / riser_vol) if riser_vol else 0.0,
                riser_depth_m=depth, rho_liquid=_liquid_density(api, wc), gas_sg=sg, api=api, water_cut=wc,
                cooldown_h=min(cooldowns) if cooldowns else math.inf,
                seabed_temp_c=fas.seabed_temp_c)


# ──────────────────────────────── blowdown ─────────────────────────────────

def _mass_flow(p_bara, p_back_bara, t_k, sg, area_m2, cd, k, z):
    """Gas mass flow through the restriction (kg/s): choked or subsonic."""
    m = MW_AIR * sg / 1000.0
    p = p_bara * 1e5
    pb = p_back_bara * 1e5
    if p <= pb:
        return 0.0
    crit = (2 / (k + 1)) ** (k / (k - 1))
    if pb / p <= crit:
        return cd * area_m2 * p * math.sqrt(k * m / (z * R * t_k) * (2 / (k + 1)) ** ((k + 1) / (k - 1)))
    r = pb / p
    return cd * area_m2 * p * math.sqrt(2 * m / (z * R * t_k) * k / (k - 1) * (r ** (2 / k) - r ** ((k + 1) / k)))


def pressure_floor(inv: dict, s: ShutdownSettings) -> dict:
    """Lowest pressure the host can reach at the seabed end of the riser (bara)."""
    head_full = inv["rho_liquid"] * G * inv["riser_depth_m"] / 1e5
    head_flow = head_full * inv["riser_holdup"]
    liquid_fills_riser = inv["liquid_m3"] >= inv["riser_volume_m3"] > 0
    worst = s.flare_back_pressure_bara + (head_full if liquid_fills_riser else
                                          head_full * min(inv["liquid_m3"] / max(inv["riser_volume_m3"], 1e-9), 1.0))
    return dict(floor_worst_bara=worst, floor_flowing_bara=s.flare_back_pressure_bara + head_flow,
                liquid_head_full_bar=head_full, liquid_fills_riser=liquid_fills_riser)


def blowdown(inv: dict, s: Optional[ShutdownSettings] = None, steps: int = 200) -> dict:
    """Depressurise the lumped line from settle-out to the hydrate-free pressure."""
    s = s or ShutdownSettings()
    t_c = inv["seabed_temp_c"]
    t_k = t_c + 273.15
    t_f = th.c_to_f(t_c)
    sg = inv["gas_sg"]
    # hydrate-free with margin: the hydrate temperature at the line pressure must sit
    # `hydrate_margin_c` below seabed temperature
    p_hyd = hydrate_pressure_bara(t_c - s.hydrate_margin_c, sg)
    fl = pressure_floor(inv, s)
    p0 = inv["settle_out_bara"]
    target = max(p_hyd, fl["floor_worst_bara"], s.flare_back_pressure_bara * 1.05)
    area = math.pi / 4 * (s.blowdown_orifice_mm / 1000.0) ** 2
    m_gas = MW_AIR * sg / 1000.0
    v = inv["gas_volume_m3"]
    curve = [(0.0, p0)]
    t_s = 0.0
    peak = 0.0
    if p0 > target and v > 0:
        ps = [p0 - (p0 - target) * i / steps for i in range(steps + 1)]
        for p1, p2 in zip(ps[:-1], ps[1:]):
            z1 = mp.z_factor(p1 * PSIA_PER_BAR, t_f, sg)
            z2 = mp.z_factor(p2 * PSIA_PER_BAR, t_f, sg)
            dm = v * m_gas / (R * t_k) * (p1 * 1e5 / z1 - p2 * 1e5 / z2)
            pm = (p1 + p2) / 2
            zm = mp.z_factor(pm * PSIA_PER_BAR, t_f, sg)
            q = min(_mass_flow(pm, s.flare_back_pressure_bara, t_k, sg, area, s.discharge_coefficient,
                               s.gas_k, zm), s.flare_max_kg_s)
            peak = max(peak, q)
            if q <= 0:
                t_s = math.inf
                break
            t_s += dm / q
            curve.append((t_s / 3600.0, p2))
    reaches = fl["floor_worst_bara"] < p_hyd
    hours = t_s / 3600.0
    if p0 <= p_hyd:
        verdict = (f"The line settles out at {p0:.0f} bara, already below the hydrate-free pressure of "
                   f"{p_hyd:.0f} bara at {t_c:.0f} °C — no blowdown needed for hydrates.")
    elif reaches:
        verdict = (f"Blowdown from {p0:.0f} to {p_hyd:.0f} bara takes about {hours:.1f} h through a "
                   f"{s.blowdown_orifice_mm:.0f} mm restriction (peak {peak:.1f} kg/s to flare).")
    else:
        verdict = (f"The host cannot blow the line down below the hydrate pressure: {p_hyd:.0f} bara is needed "
                   f"at {t_c:.0f} °C (with {s.hydrate_margin_c:.0f} °C margin), but flare back-pressure plus {fl['liquid_head_full_bar']:.0f} bar of "
                   f"liquid standing in the {inv['riser_depth_m']:.0f} m riser leaves {fl['floor_worst_bara']:.0f} "
                   f"bara at the seabed. Plan inhibition or displacement instead, subsea liquid removal "
                   f"(pump or separate depressurisation line), or two-sided blowdown.")
    return dict(settle_out_bara=p0, hydrate_pressure_bara=p_hyd, target_bara=target, hours=hours,
                peak_kg_s=peak, reaches_hydrate_free=reaches or p0 <= p_hyd,
                gas_mass_te=v * m_gas / (R * t_k) * (p0 * 1e5 / mp.z_factor(p0 * PSIA_PER_BAR, t_f, sg)) / 1000.0
                if p0 > 0 else 0.0,
                curve=curve, verdict=verdict, **fl)


# ───────────────────────────── planned shutdown ────────────────────────────

def inhibitor_for_shutdown(inv: dict, inhibitor: str, s: ShutdownSettings, subcooling_c: float) -> dict:
    """Inhibitor needed to treat the water standing in the line for a shutdown."""
    import tb_chemistry as ch
    if inhibitor not in ("MEG", "Methanol"):
        inhibitor = "MEG"
    if subcooling_c <= 0 or inv["water_m3"] <= 0:
        return dict(inhibitor=inhibitor, wt_pct=0.0, volume_m3=0.0, hours=0.0)
    wt = (ch.nielsen_bucklin_wt_pct(subcooling_c) if inhibitor == "Methanol"
          else ch.hammerschmidt_wt_pct(subcooling_c, "MEG"))
    wt = min(wt, 85.0)
    water_kg = inv["water_m3"] * RHO_WATER
    pure_kg = water_kg * wt / (100.0 - wt)
    lean = s.lean_meg_wt_pct / 100.0 if inhibitor == "MEG" else 1.0
    vol = pure_kg / lean / ch.INHIBITOR_DENSITY[inhibitor]
    return dict(inhibitor=inhibitor, wt_pct=wt, volume_m3=vol, hours=vol / s.inhibitor_injection_m3_h)


def planned_shutdown(inv: dict, s: Optional[ShutdownSettings] = None, inhibitor: str = "MEG",
                     continuous_wt_pct: float = 0.0, no_touch_h: float = 8.0) -> dict:
    """The planned-shutdown sequence for this line, each step sized."""
    s = s or ShutdownSettings()
    t_sea = inv["seabed_temp_c"]
    p_set = inv["settle_out_bara"]
    t_hyd = th.f_to_c(th.hydrate_temperature_f(p_set * PSIA_PER_BAR, inv["gas_sg"])) if p_set > 1 else -math.inf
    subcool = t_hyd - t_sea + s.hydrate_margin_c
    inh = inhibitor_for_shutdown(inv, inhibitor, s, subcool)
    already = continuous_wt_pct >= inh["wt_pct"] > 0
    bd = blowdown(inv, s)
    oil_line = inv["api"] < 45
    steps = []
    if subcool <= 0:
        steps.append(dict(step=1, action="No hydrate treatment needed",
                          detail=f"At the settle-out pressure ({p_set:.0f} bara) the hydrate temperature is "
                                 f"{t_hyd:.1f} °C, below seabed {t_sea:.1f} °C with margin.", hours=0.0))
    elif already:
        steps.append(dict(step=1, action=f"Keep continuous {inhibitor} running to shut-in",
                          detail=f"{continuous_wt_pct:.0f} wt % already covers the {inh['wt_pct']:.0f} wt % the "
                                 f"standing water needs at seabed temperature.", hours=0.0))
    else:
        steps.append(dict(step=1, action=f"Inhibit the line with {inhibitor} before shut-in",
                          detail=f"{inh['volume_m3']:.0f} m³ of {inhibitor} treats the {inv['water_m3']:.0f} m³ of "
                                 f"water in the line to {inh['wt_pct']:.0f} wt % ({subcool:.1f} °C subcooling at "
                                 f"{p_set:.0f} bara, incl. {s.hydrate_margin_c:.0f} °C margin), at "
                                 f"{s.inhibitor_injection_m3_h:.1f} m³/h.", hours=inh["hours"]))
        if oil_line:
            disp_h = inv["volume_m3"] / s.displacement_rate_m3_h
            steps.append(dict(step=1, action="…or displace the line with dead oil / diesel",
                              detail=f"{inv['volume_m3']:.0f} m³ at {s.displacement_rate_m3_h:.0f} m³/h — the "
                                     f"alternative when inhibitor volume or gelling (pour point above seabed "
                                     f"temperature) rules it out.", hours=disp_h))
    steps.append(dict(step=2, action="Close in wells, SSIV and host inlet (ESD valves)",
                      detail="Trees first, then the subsea isolation valve, then the host.",
                      hours=s.isolation_hours))
    if subcool > 0 and not already:
        if bd["reaches_hydrate_free"]:
            steps.append(dict(step=3, action="Depressurise if the stop outlasts the cool-down time",
                              detail=bd["verdict"], hours=bd["hours"]))
        else:
            steps.append(dict(step=3, action="Blowdown alone will not make the line hydrate-free",
                              detail=bd["verdict"], hours=math.nan))
    else:
        steps.append(dict(step=3, action="Depressurisation not required for hydrates",
                          detail="Line inhibited or outside the hydrate region at seabed temperature.", hours=0.0))
    before = sum(x["hours"] for x in steps if x["step"] == 1 and not x["action"].startswith("…"))
    cd = inv["cooldown_h"]
    ok = before + s.isolation_hours <= max(no_touch_h, 0.0) or subcool <= 0 or already
    verdict = ("Pre-shutdown steps fit inside the no-touch time." if ok else
               f"Inhibiting takes {before:.1f} h — longer than the {no_touch_h:.0f} h no-touch time: raise the "
               f"injection capacity, or start inhibiting before the planned stop.")
    return dict(steps=steps, subcooling_c=subcool, hydrate_temp_c=t_hyd, inhibitor=inh, blowdown=bd,
                cooldown_h=cd, verdict=verdict, pre_shutdown_h=before)
