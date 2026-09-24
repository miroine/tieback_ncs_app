"""
tb_basis.py — Design basis checklist.

Walks the project and reports, item by item, what the design basis currently
rests on: what you have entered, what is still running on a default, and what is
missing. Everything is reported in SI units as used on the NCS — m, bar, °C,
Sm³/d, MSm³/d, tonn, MNOK — whatever the engine uses internally.

Status values:
    ok       — entered for this project
    default  — running on the built-in assumption; confirm or replace it
    missing  — no value; the result depends on a guess
    action   — something to resolve before the numbers can be trusted
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import tb_cost
import tb_flowassurance as tb_fa
import tb_map
import tb_schedule

BARA_PER_PSI = 1.0 / 14.503774
SM3_PER_STB = 0.158987
IN_TO_MM = 25.4

UNITS_NOTE = ("Alle verdier i SI-enheter: m, bar(a), °C, Sm³/d, MSm³/d, tonn, W/m²K. "
              "Kostnader i MNOK (og MUSD).")


def _row(category, item, value, status, note=""):
    return dict(category=category, item=item, value=value, status=status, note=note)


def design_basis(layout, cost_settings: Optional[tb_cost.CostSettings] = None,
                 sched_settings: Optional[tb_schedule.ScheduleSettings] = None,
                 fa_settings: Optional[tb_fa.FASettings] = None,
                 nok_per_usd: float = 10.5,
                 catalog_source: str = "", chem_inputs=None) -> List[dict]:
    cost = cost_settings or tb_cost.CostSettings()
    sched = sched_settings or tb_schedule.ScheduleSettings()
    fas = fa_settings or tb_fa.FASettings()
    cat = layout.catalog
    rows: List[dict] = []
    findings = layout.validate()
    wells = [n for n in layout.nodes if layout.kind(n) == "well"]
    subsea = [n for n in layout.nodes.values() if layout.kind(n.node_id) != "host"]
    lines = [e for e in layout.edges.values() if cat.get(e.item_id).is_linear]

    # ── 1. Lokasjon og grunnlagsdata ──
    rows.append(_row("Location and survey", "Coordinate datum", layout.settings.datum,
                     "ok" if layout.settings.datum in ("WGS84", "ED50") else "action",
                     "ED50 layouts are converted to WGS84 for the map and the NCS layers"))
    blank_depth = [n.node_id for n in subsea if n.water_depth_m <= 0]
    rows.append(_row("Location and survey", "Water depth on subsea equipment",
                     f"{len(subsea) - len(blank_depth)} of {len(subsea)} set",
                     "ok" if not blank_depth else "missing",
                     "Fill from EMODnet or the survey: " + ", ".join(blank_depth[:6]) if blank_depth else ""))
    with_profile = [e.edge_id for e in lines if e.attrs.get("seabed_profile")]
    flow_lines = [e for e in lines if cat.get(e.item_id).category in ("flowline", "utility_line")]
    rows.append(_row("Location and survey", "Seabed profile along routes",
                     f"{len(with_profile)} of {len(flow_lines)} lines",
                     "ok" if flow_lines and len(with_profile) == len(flow_lines) else "missing",
                     "Without a profile the line is modelled on a straight slope between end depths"))
    rows.append(_row("Location and survey", "Route allowance",
                     f"{layout.settings.route_allowance_frac * 100:.1f} % + "
                     f"{layout.settings.end_allowance_m:.0f} m per line",
                     "default" if abs(layout.settings.route_allowance_frac - 0.03) < 1e-9 else "ok"))
    rows.append(_row("Location and survey", "Minimum lay bend radius",
                     f"{layout.settings.min_bend_radius_m:,.0f} m",
                     "default" if abs(layout.settings.min_bend_radius_m - 400) < 1e-9 else "ok"))

    # ── 2. Reservoar og brønnstrøm ──
    win = tb_fa.well_inputs(layout)
    if not wells:
        rows.append(_row("Reservoir and well stream", "Wells", "none", "missing",
                         "Add wells before the flow assurance results mean anything"))
    else:
        total_oil = sum(w.oil_sm3_d for w in win.values())
        rows.append(_row("Reservoir and well stream", "Design oil/condensate rate",
                         f"{total_oil:,.0f} Sm³/d over {len(wells)} wells",
                         "ok" if total_oil > 0 else "missing"))
        gor = [w.gor_sm3_sm3 for w in win.values()]
        rows.append(_row("Reservoir and well stream", "GOR",
                         f"{min(gor):,.0f}–{max(gor):,.0f} Sm³/Sm³" if gor else "—",
                         "default" if all(abs(g - 250.0) < 1e-9 for g in gor) else "ok"))
        wc = [w.water_cut for w in win.values()]
        rows.append(_row("Reservoir and well stream", "Water cut",
                         f"{min(wc) * 100:.0f}–{max(wc) * 100:.0f} %" if wc else "—",
                         "default" if all(abs(x - 0.10) < 1e-9 for x in wc) else "ok",
                         "Late-life water cut drives hydrate and slugging risk"))
        api = [w.api for w in win.values()]
        dens = [141.5 / (a + 131.5) * 1000 for a in api]
        rows.append(_row("Reservoir and well stream", "Oil density",
                         f"{min(dens):.0f}–{max(dens):.0f} kg/Sm³ ({min(api):.0f}–{max(api):.0f} °API)",
                         "default" if all(abs(a - 40.0) < 1e-9 for a in api) else "ok"))
        sg = [w.gas_sg for w in win.values()]
        rows.append(_row("Reservoir and well stream", "Gas gravity",
                         f"{min(sg):.2f}–{max(sg):.2f} (rel. luft)",
                         "default" if all(abs(x - 0.70) < 1e-9 for x in sg) else "ok",
                         "Sets the hydrate curve and the gas density"))
        wht = [w.wht_c for w in win.values()]
        rows.append(_row("Reservoir and well stream", "Flowing wellhead temperature",
                         f"{min(wht):.0f}–{max(wht):.0f} °C",
                         "default" if all(abs(x - 70.0) < 1e-9 for x in wht) else "ok"))
        whp = [w.max_whp_bara for w in win.values()]
        rows.append(_row("Reservoir and well stream", "Available wellhead pressure",
                         f"{min(whp):.0f}–{max(whp):.0f} bara",
                         "default" if all(abs(x - 150.0) < 1e-9 for x in whp) else "ok"))
        sitp = [layout.nodes[w].sitp_psi * BARA_PER_PSI for w in wells]
        rows.append(_row("Reservoir and well stream", "Shut-in tubing pressure",
                         f"{min(sitp):.0f}–{max(sitp):.0f} bara" if any(sitp) else "not set",
                         "ok" if all(x > 0 for x in sitp) else "missing",
                         "Drives the pressure rating check on every component"))
        iprs = {w: (layout.nodes[w].attrs.get("ipr") or {}) for w in wells}
        with_ipr = [w for w, d in iprs.items() if d and d.get("use", True)]
        on_default = [w for w in with_ipr
                      if abs(float(iprs[w].get("reservoir_bara", 350)) - 350) < 1e-9
                      and abs(float(iprs[w].get("pi_sm3_d_bar", 12)) - 12) < 1e-9]
        rows.append(_row("Reservoir and well stream", "Inflow performance (IPR)",
                         f"{len(with_ipr)} of {len(wells)} wells",
                         "missing" if not with_ipr else ("default" if on_default else "ok"),
                         ("Without an IPR the rates are typed in, not deliverability-based" if not with_ipr else
                          "Still on the default 350 bara / 12 Sm³/d/bar: " + ", ".join(on_default[:6])
                          if on_default else "")))
        # reservoir pressure and temperature: from the reservoirs, else from the wells' IPR data
        try:
            import tb_fluids
            res = tb_fluids.reservoirs(layout)
        except Exception:  # noqa: BLE001
            res = {}
        if res:
            pr = [r.pres_bara for r in res.values()]
            tr = [r.tres_c for r in res.values()]
            src = "from " + ", ".join(res)
        else:
            pr = [float(d["reservoir_bara"]) for d in iprs.values() if d.get("reservoir_bara")]
            tr = [float(d["reservoir_t_c"]) for d in iprs.values() if d.get("reservoir_t_c")]
            src = "from the wells' IPR data" if pr else ""
        rows.append(_row("Reservoir and well stream", "Reservoir pressure",
                         f"{min(pr):.0f}–{max(pr):.0f} bara" if pr else "not set",
                         "ok" if pr else "missing", src or "Enter it per reservoir, or in the well IPR table"))
        rows.append(_row("Reservoir and well stream", "Reservoir temperature",
                         f"{min(tr):.0f}–{max(tr):.0f} °C" if tr else "not set",
                         "ok" if tr else "missing", src or "Sets the wellhead temperature and the tubing profile"))

        # ── contaminants: CO₂, H₂S, mercury ──
        import tb_chemistry
        ci = chem_inputs

        def pick(attr_ci, attr_res):
            v = getattr(ci, attr_ci, None) if ci is not None else None
            if v is not None:
                return float(v), "entered"
            vals = [getattr(r, attr_res, None) for r in res.values()]
            vals = [float(x) for x in vals if x is not None]
            if vals:
                return max(vals), ("default" if max(vals) == 0 else "entered")
            return None, "missing"
        co2, co2_s = pick("co2_mol_pct", "co2_mol_pct")
        h2s, h2s_s = pick("h2s_ppm", "h2s_ppm")
        hg, hg_s = pick("mercury_ug_nm3", "mercury_ug_nm3")
        p_design = max(sitp) if any(sitp) else (max(pr) if pr else 0.0)
        crows = (tb_chemistry.contaminants(co2, h2s, hg, p_design)
                 if hasattr(tb_chemistry, "contaminants") else [])      # an older tb_chemistry.py
        for row_, st_ in zip(crows, (co2_s, h2s_s, hg_s)):
            status = row_["status"] if st_ != "default" else "default"
            note = row_["note"] + f" · limit: {row_['limit']}"
            if st_ == "default":
                note = "0 is the reservoir default — confirm from the gas analysis · " + note
            rows.append(_row("Contaminants", row_["item"], row_["value"], status, note))

        # ── pressure protection ──
        prot = layout.pressure_protection() if hasattr(layout, "pressure_protection") else []
        need = [r for r in prot if r["verdict"] == "HIPPS or fully rated"]
        hipps_used = [r for r in prot if r["verdict"] == "HIPPS in place"]
        rows.append(_row("Reservoir and well stream", "Pressure protection (HIPPS or fully rated)",
                         ("fully rated" if prot and not need and not hipps_used else
                          f"HIPPS on {len(hipps_used)} well path(s)" if not need else
                          f"{len(need)} well path(s) under-rated") if prot else "—",
                         "action" if need else ("ok" if prot else "missing"),
                         ("Fit HIPPS at " + ", ".join(sorted({r['hipps_node'] or '?' for r in need}))
                          + " or upgrade " + ", ".join(sorted({x for r in need for x in r['upgrade']})[:6])
                          + " to the shut-in pressure") if need else ""))

    # ── 3. Rørledninger og stigerør ──
    no_dia = [e.edge_id for e in lines if cat.get(e.item_id).cost_basis == "per_inch_m" and not e.diameter_in]
    dias = sorted({e.diameter_in * IN_TO_MM for e in lines if e.diameter_in})
    rows.append(_row("Lines and risers", "Line sizes",
                     ", ".join(f"{d:.0f} mm" for d in dias) if dias else "—",
                     "ok" if lines and not no_dia else "missing",
                     "Missing diameter: " + ", ".join(no_dia) if no_dia else ""))
    total_km = sum(layout.edge_length(e) for e in lines) / 1000.0
    rows.append(_row("Lines and risers", "Total line length", f"{total_km:,.1f} km",
                     "ok" if total_km > 0 else "missing"))
    u_over = [e.edge_id for e in lines if e.attrs.get("u_w_m2k") is not None]
    rows.append(_row("Lines and risers", "Heat transfer (U-value)",
                     f"{len(u_over)} of {len(lines)} lines specified",
                     "ok" if u_over else "default",
                     "Defaults by pipe type: 15 W/m²K bare CS, 5 flexible, 1 pipe-in-pipe"))
    rows.append(_row("Lines and risers", "Pipe roughness", f"{fas.roughness_in * IN_TO_MM:.3f} mm",
                     "default" if abs(fas.roughness_in - 0.0018) < 1e-9 else "ok"))

    # ── 4. Strømningssikkerhet ──
    rows.append(_row("Flow assurance", "Host arrival pressure", f"{fas.arrival_bara:.0f} bara",
                     "default" if abs(fas.arrival_bara - 30.0) < 1e-9 else "ok",
                     "Separator inlet / riser top — confirm with the host operator"))
    rows.append(_row("Flow assurance", "Seabed temperature", f"{fas.seabed_temp_c:.1f} °C",
                     "default" if abs(fas.seabed_temp_c - 6.0) < 1e-9 else "ok"))
    rows.append(_row("Flow assurance", "Hydrate inhibition",
                     "none" if fas.inhibitor == "None" else f"{fas.inhibitor} {fas.inhibitor_wt_pct:.0f} wt %",
                     "ok" if fas.inhibitor != "None" else "action",
                     "Check the margin at turndown, not only at design rate"))
    rows.append(_row("Flow assurance", "No-touch (cool-down) time", f"{fas.no_touch_hours:.0f} h",
                     "default" if abs(fas.no_touch_hours - 8.0) < 1e-9 else "ok"))
    rows.append(_row("Flow assurance", "Joule-Thomson cooling", "included" if fas.include_jt else "not included",
                     "ok" if fas.include_jt else "action"))
    rows.append(_row("Flow assurance", "Host capacity",
                     f"{fas.host_liquid_capacity_sm3_d:,.0f} Sm³/d liquid, "
                     f"{fas.host_gas_capacity_msm3_d:.1f} MSm³/d gas",
                     "default" if abs(fas.host_liquid_capacity_sm3_d - 15000) < 1e-9 else "ok",
                     "Spare capacity and tariff are the usual tie-in show-stoppers"))
    rows.append(_row("Flow assurance", "Erosional velocity basis", f"API RP 14E C = {fas.erosional_c:.0f}",
                     "default" if abs(fas.erosional_c - 100) < 1e-9 else "ok"))

    # ── 5. Kostnad ──
    rows.append(_row("Cost basis", "Cost library", catalog_source or "built-in indicative rates",
                     "ok" if catalog_source else "action",
                     "Load your own rates before quoting any number outside a screening"))
    rows.append(_row("Cost basis", "Contingency", f"{cost.contingency_frac * 100:.0f} %",
                     "default" if abs(cost.contingency_frac - 0.15) < 1e-9 else "ok"))
    rows.append(_row("Cost basis", "Owner's cost", f"{cost.owners_cost_frac * 100:.0f} %",
                     "default" if abs(cost.owners_cost_frac - 0.05) < 1e-9 else "ok"))
    rows.append(_row("Cost basis", "Weather factor", f"{cost.weather_factor:.2f}",
                     "default" if abs(cost.weather_factor - 1.25) < 1e-9 else "ok",
                     "Multiplies offshore duration in both cost and schedule"))
    try:
        est = tb_cost.estimate(layout, cost)
        rows.append(_row("Cost basis", "CAPEX incl. contingency",
                         f"{est['total_usd'] * nok_per_usd / 1e6:,.0f} MNOK "
                         f"({est['total_usd'] / 1e6:,.0f} MUSD)", "ok",
                         f"Exchange rate {nok_per_usd:.2f} NOK/USD. Drilling and completion excluded"))
    except Exception:  # noqa: BLE001
        pass

    # ── 6. Framdrift ──
    rows.append(_row("Schedule", "DG2 date", f"{sched.dg2_date:%d.%m.%Y}",
                     "default" if sched.dg2_date.year == 2027 and sched.dg2_date.month == 1 else "ok"))
    rows.append(_row("Schedule", "PDO approval allowance", f"{sched.pdo_approval_days:.0f} days",
                     "default" if abs(sched.pdo_approval_days - 180) < 1e-9 else "ok"))
    rows.append(_row("Schedule", "Marine season window",
                     f"{sched.marine_window.start_mmdd[1]}.{sched.marine_window.start_mmdd[0]}–"
                     f"{sched.marine_window.end_mmdd[1]}.{sched.marine_window.end_mmdd[0]}",
                     "default" if sched.marine_window.start_mmdd == (4, 1) else "ok"))
    rows.append(_row("Schedule", "Drilling", f"{sched.drill_days_per_well:.0f} days/well, {sched.rigs} rig(s)",
                     "default" if abs(sched.drill_days_per_well - 55) < 1e-9 else "ok"))

    # ── 7. Kontroller ──
    errs = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warning"]
    rows.append(_row("Design checks", "Layout errors", f"{len(errs)}", "ok" if not errs else "action",
                     "; ".join(f"{f.element_id}: {f.message}" for f in errs[:3])))
    rows.append(_row("Design checks", "Layout warnings", f"{len(warns)}", "ok" if not warns else "default",
                     "; ".join(f"{f.element_id}: {f.message}" for f in warns[:3])))
    return rows


def summary(rows: List[dict]) -> Dict[str, int]:
    out = {"ok": 0, "default": 0, "missing": 0, "action": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def outstanding(rows: List[dict]) -> List[dict]:
    """The items that still need a decision or a number."""
    return [r for r in rows if r["status"] in ("missing", "action")]
