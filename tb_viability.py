"""
tb_viability.py — Is this concept viable?

The design basis checklist (tb_basis) asks what the numbers rest on. This one
asks whether the concept stands up: can every well deliver into the host, does
the fluid arrive outside the hydrate region, does the schedule hang together,
is anything over a vessel or rating limit.

Each criterion returns pass / attention / fail / not assessed, the value it was
judged on, the threshold used, and what to do about it. SI units throughout
(m, bar, °C, Sm³/d, MSm³/d, h).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import tb_cost
import tb_flowassurance as tb_fa
import tb_schedule

PASS, ATTENTION, FAIL, NA = "pass", "attention", "fail", "not assessed"
ORDER = {FAIL: 0, ATTENTION: 1, NA: 2, PASS: 3}


def _c(group, criterion, status, value="", threshold="", action=""):
    return dict(group=group, criterion=criterion, status=status, value=str(value),
                threshold=str(threshold), action=action)


def viability(layout, cost_settings: Optional[tb_cost.CostSettings] = None,
              sched_settings: Optional[tb_schedule.ScheduleSettings] = None,
              fa_settings: Optional[tb_fa.FASettings] = None,
              turndown_fraction: float = 0.5) -> List[dict]:
    cost = cost_settings or tb_cost.CostSettings()
    sched = sched_settings or tb_schedule.ScheduleSettings()
    fas = fa_settings or tb_fa.FASettings()
    cat = layout.catalog
    out: List[dict] = []
    findings = layout.validate()
    wells = [n for n in layout.nodes if layout.kind(n) == "well"]
    hosts = [n for n in layout.nodes if layout.kind(n) == "host"]

    # ── 1. Layout integrity ──
    errs = [f for f in findings if f.severity == "error"]
    out.append(_c("Layout", "No blocking design errors", PASS if not errs else FAIL,
                  f"{len(errs)} errors", "0",
                  "; ".join(f"{f.element_id}: {f.message}" for f in errs[:3])))
    out.append(_c("Layout", "Host in the layout", PASS if hosts else FAIL,
                  ", ".join(layout.nodes[h].label or h for h in hosts) or "none", "at least one",
                  "Screen a tie-in host and add it to the layout" if not hosts else ""))
    unrouted = [f.element_id for f in findings if f.code == "NO_PRODUCTION_PATH"]
    out.append(_c("Layout", "Every well routed to a host", PASS if not unrouted else FAIL,
                  f"{len(wells) - len(unrouted)} of {len(wells)}", "all",
                  "Connect: " + ", ".join(unrouted[:5]) if unrouted else ""))
    no_ctrl = [f.element_id for f in findings if f.code == "NO_CONTROL"]
    out.append(_c("Layout", "Control and chemical supply to every well", PASS if not no_ctrl else ATTENTION,
                  f"{len(wells) - len(no_ctrl)} of {len(wells)}", "all",
                  "Run an umbilical to: " + ", ".join(no_ctrl[:5]) if no_ctrl else ""))
    slots = [f for f in findings if f.code == "SLOTS_EXCEEDED"]
    out.append(_c("Layout", "Slot capacity not exceeded", PASS if not slots else FAIL,
                  f"{len(slots)} structures over capacity", "0",
                  "Add a template or move wells to another structure" if slots else ""))
    ratings = [f for f in findings if f.code == "RATING"]
    out.append(_c("Layout", "Pressure ratings cover shut-in pressure", PASS if not ratings else FAIL,
                  f"{len(ratings)} components under-rated", "0",
                  "Upgrade the component or add HIPPS" if ratings else ""))
    lifts = [f for f in findings if f.code == "LIFT_CAPACITY"]
    out.append(_c("Installation", "Structures within vessel lift capacity", PASS if not lifts else ATTENTION,
                  f"{len(lifts)} over the hook limit", "0",
                  "Choose a larger spread or split the structure" if lifts else ""))
    bends = [f for f in findings if f.code == "BEND_RADIUS"]
    out.append(_c("Installation", "Route bends within lay radius", PASS if not bends else ATTENTION,
                  f"{len(bends)} tight bends", f"≥ {layout.settings.min_bend_radius_m:,.0f} m",
                  "Smooth the route or move the bend" if bends else ""))

    # ── 2. Flow assurance ──
    res = None
    if wells and hosts:
        try:
            res = tb_fa.solve(layout, fas)
        except Exception as exc:  # noqa: BLE001
            out.append(_c("Flow assurance", "Steady-state solve", FAIL, str(exc)[:80], "",
                          "Fix the inputs flagged above and re-run"))
    if res is None:
        for name in ("Wells deliver at design rate", "Hydrate margin at design rate",
                     "Hydrate margin at turndown", "Cool-down beats the no-touch time",
                     "Velocities within the erosional limit", "Host capacity"):
            out.append(_c("Flow assurance", name, NA, "no solve", "", "Add wells and a host"))
    else:
        bad = [w for w in res.wells if not w["deliverable"]]
        worst = min((w["margin_bar"] for w in res.wells), default=float("nan"))
        out.append(_c("Flow assurance", "Wells deliver at design rate", PASS if not bad else FAIL,
                      f"tightest margin {worst:.0f} bar", "> 0 bar",
                      "Bigger line, boosting, or a closer host: " + ", ".join(w["well"] for w in bad[:4])
                      if bad else ""))
        margins = [r.min_hydrate_margin_c for r in res.edges.values() if not r.heated]
        m_now = min(margins) if margins else float("nan")
        out.append(_c("Flow assurance", "Hydrate margin at design rate",
                      PASS if m_now >= 3 else (ATTENTION if m_now >= 0 else FAIL),
                      f"{m_now:.1f} °C", "≥ 3 °C",
                      "Insulate, inhibit continuously, or heat the line" if m_now < 3 else ""))
        try:
            td = tb_fa.rate_sensitivity(layout, fas, None, (1.0, turndown_fraction))[-1]
            m_td = td["min_hydrate_margin_c"]
            out.append(_c("Flow assurance", "Hydrate margin at turndown",
                          PASS if m_td >= 3 else (ATTENTION if m_td >= 0 else FAIL),
                          f"{m_td:.1f} °C at {turndown_fraction:.0%} rate", "≥ 3 °C",
                          "Check the minimum rate the field can be run at" if m_td < 3 else ""))
        except Exception:  # noqa: BLE001
            out.append(_c("Flow assurance", "Hydrate margin at turndown", NA, "", "≥ 3 °C", ""))
        cds = [r.cooldown_h for r in res.edges.values()
               if not r.heated and not math.isinf(r.cooldown_h)
               and cat.get(layout.edges[r.edge_id].item_id).category in ("flowline", "riser")]
        cd = min(cds) if cds else float("inf")
        out.append(_c("Flow assurance", "Cool-down beats the no-touch time",
                      PASS if cd >= fas.no_touch_hours else ATTENTION,
                      "no limit" if math.isinf(cd) else f"{cd:.1f} h", f"≥ {fas.no_touch_hours:.0f} h",
                      "Insulate, or plan depressurisation and inhibition on shutdown"
                      if cd < fas.no_touch_hours else ""))
        ero = max((r.erosional_ratio for r in res.edges.values()), default=float("nan"))
        out.append(_c("Flow assurance", "Velocities within the erosional limit",
                      PASS if ero <= 1.0 else ATTENTION, f"{ero:.2f} × limit", "≤ 1.0",
                      "Increase the line size on the limiting section" if ero > 1 else ""))
        cap_fail = [f for f in res.findings if f[0] == "error" and "capacity" in f[2].lower()]
        host0 = next(iter(res.host.values())) if res.host else {}
        out.append(_c("Flow assurance", "Host capacity", PASS if not cap_fail else FAIL,
                      f"{host0.get('liquid_sm3_d', float('nan')):,.0f} Sm³/d liquid, "
                      f"{host0.get('gas_msm3_d', float('nan')):.2f} MSm³/d gas",
                      f"{fas.host_liquid_capacity_sm3_d:,.0f} Sm³/d, "
                      f"{fas.host_gas_capacity_msm3_d:.1f} MSm³/d",
                      "Confirm spare capacity and the tariff with the host operator"))
        slug = [r.edge_id for r in res.edges.values() if r.slug_risk]
        out.append(_c("Flow assurance", "No severe slugging flagged", PASS if not slug else ATTENTION,
                      ", ".join(slug) or "none", "none",
                      "Check riser stability, or plan gas lift / topside control" if slug else ""))
        spans = sum(len(r.free_spans) for r in res.edges.values())
        out.append(_c("Installation", "Free spans screened",
                      PASS if spans == 0 else ATTENTION,
                      f"{spans} candidate spans", "0 unmitigated",
                      "Survey the route and plan span supports" if spans else ""))

    # ── 3. Schedule ──
    try:
        schedule, emap = tb_schedule.build_from_layout(layout, sched)
        fo = sorted([a for a in schedule.activities.values() if a.act_id.endswith("_FIRST_OIL")],
                    key=lambda a: a.es)
        out.append(_c("Schedule", "Schedule builds end to end", PASS,
                      f"first production {fo[0].es:%b %Y}" if fo else "no first-production milestone",
                      "", ""))
        windowed = [a for a in schedule.activities.values() if a.window and a.duration_days > 0]
        tight = [a for a in windowed if a.total_float_days < 30]
        out.append(_c("Schedule", "Float on weather-window campaigns",
                      PASS if not tight else ATTENTION,
                      f"{len(tight)} campaigns under 30 days float", "≥ 30 days",
                      "A slip pushes these to the next season: "
                      + ", ".join(a.name for a in tight[:3]) if tight else ""))
        long_lead = max((a.duration_days for a in schedule.activities.values()
                         if a.group == "Procurement"), default=0) / 30.44
        out.append(_c("Schedule", "Long-lead items ordered in time", PASS if long_lead else NA,
                      f"longest lead {long_lead:.0f} months", "within the plan",
                      "Confirm the award date covers the longest lead item"))
    except ValueError as exc:
        out.append(_c("Schedule", "Schedule builds end to end", FAIL, str(exc)[:80], "",
                      "Split the campaign or widen the weather window"))

    # ── 4. Cost ──
    try:
        est = tb_cost.estimate(layout, cost)
        out.append(_c("Cost", "Estimate produced", PASS,
                      f"{est['total_usd'] / 1e6:,.0f} MUSD incl. contingency", "", ""))
        mc = tb_cost.monte_carlo(layout, cost, n=1500, seed=11)
        spread = (mc["P90"] - mc["P10"]) / max(mc["P50"], 1e-9)
        out.append(_c("Cost", "Cost uncertainty band", PASS if spread < 0.6 else ATTENTION,
                      f"P10–P90 spread {spread:.0%} of P50", "< 60 %",
                      "Tighten the item ranges or get quotes for the big items" if spread >= 0.6 else ""))
    except Exception as exc:  # noqa: BLE001
        out.append(_c("Cost", "Estimate produced", FAIL, str(exc)[:80], "", ""))
    return out


def summary(rows: List[dict]) -> Dict[str, int]:
    out = {PASS: 0, ATTENTION: 0, FAIL: 0, NA: 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def verdict(rows: List[dict]) -> str:
    c = summary(rows)
    if c[FAIL]:
        return f"Not viable as drawn — {c[FAIL]} blocking issue(s)"
    if c[ATTENTION]:
        return f"Viable with {c[ATTENTION]} item(s) to resolve"
    if c[NA]:
        return "Partly assessed — complete the inputs"
    return "Viable on every criterion checked"


def sort_rows(rows: List[dict]) -> List[dict]:
    return sorted(rows, key=lambda r: (ORDER[r["status"]], r["group"]))
