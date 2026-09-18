"""
tb_cases.py — Concept cases: snapshot, restore and compare.

A case is a complete project snapshot (layout + catalog + cost, schedule and
flow-assurance settings). `summarise` runs the estimate, the schedule and the
flow-assurance solve so several concepts can be put side by side on the numbers
that decide a screening: CAPEX, first production, deliverability and flow-
assurance margins.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Dict, List, Optional

import yaml

import tb_cost
import tb_flowassurance as tb_fa
import tb_project
import tb_schedule
from tb_catalog import Catalog
from tb_network import Layout

SCHEMA = "tieback_caseset/1"


def snapshot(name: str, layout: Layout, cost: tb_cost.CostSettings, sched: tb_schedule.ScheduleSettings,
             fa: Optional[tb_fa.FASettings] = None, note: str = "", display=None) -> dict:
    return {
        "name": name,
        "note": note,
        "saved_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "project": yaml.safe_load(tb_project.project_to_yaml(name, layout, cost, sched, fa, display)),
    }


def restore(case: dict):
    """Returns (name, layout, cost_settings, schedule_settings, fa_settings, display_settings)."""
    return tb_project.project_from_yaml_full(yaml.safe_dump(case["project"], sort_keys=False))


def summarise(case: dict, run_flow_assurance: bool = True) -> dict:
    name, layout, cost, sched, fa, _display = restore(case)
    findings = layout.validate()
    est = tb_cost.estimate(layout, cost)
    row = {
        "case": case.get("name", name),
        "wells": sum(1 for n in layout.nodes if layout.kind(n) == "well"),
        "structures": sum(1 for n in layout.nodes
                          if layout.kind(n) in tb_schedule.STRUCTURE_KINDS),
        "flowline_km": sum(r["length_m"] for r in layout.quantities()
                           if r["category"] == "flowline") / 1000.0,
        "umbilical_km": sum(r["length_m"] for r in layout.quantities()
                            if r["category"] in ("umbilical", "power_cable", "utility_line")) / 1000.0,
        "capex_base_musd": est["base_usd"] / 1e6,
        "capex_total_musd": est["total_usd"] / 1e6,
        "errors": sum(f.severity == "error" for f in findings),
        "warnings": sum(f.severity == "warning" for f in findings),
    }
    try:
        schedule, emap = tb_schedule.build_from_layout(layout, sched)
        fo = sorted([a for a in schedule.activities.values() if a.act_id.endswith("_FIRST_OIL")],
                    key=lambda a: a.es)
        row["first_production"] = fo[0].es if fo else None
        row["project_end"] = schedule.finish
        prof = tb_cost.phase_costs(est, schedule, emap, cost)
        row["peak_year_musd"] = max(prof["annual"].values()) / 1e6 if prof["annual"] else float("nan")
    except ValueError as exc:
        row["first_production"] = None
        row["project_end"] = None
        row["peak_year_musd"] = float("nan")
        row["schedule_error"] = str(exc)
    if run_flow_assurance:
        try:
            res = tb_fa.solve(layout, fa)
            host = next(iter(res.host.values())) if res.host else {}
            row.update(
                max_required_whp_bara=max((w["required_whp_bara"] for w in res.wells), default=float("nan")),
                min_whp_margin_bar=min((w["margin_bar"] for w in res.wells), default=float("nan")),
                arrival_t_c=host.get("arrival_t_c", float("nan")),
                liquid_sm3_d=host.get("liquid_sm3_d", float("nan")),
                min_hydrate_margin_c=min((r.min_hydrate_margin_c for r in res.edges.values() if not r.heated),
                                         default=float("nan")),
                fa_errors=sum(1 for f in res.findings if f[0] == "error"),
                free_spans=sum(len(r.free_spans) for r in res.edges.values()))
        except Exception as exc:  # noqa: BLE001 — a broken case must not stop the comparison
            row["fa_error"] = str(exc)
    return row


def compare(cases: List[dict], run_flow_assurance: bool = True) -> List[dict]:
    return [summarise(c, run_flow_assurance) for c in cases]


def deltas(rows: List[dict], baseline: int = 0) -> List[dict]:
    """Differences against a baseline case for the numeric comparison columns."""
    if not rows:
        return []
    base = rows[baseline]
    out = []
    for r in rows:
        d = {"case": r["case"]}
        for k, v in r.items():
            if k == "case" or not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            b = base.get(k)
            if isinstance(b, (int, float)):
                d[k] = v - b
        if isinstance(r.get("first_production"), dt.date) and isinstance(base.get("first_production"), dt.date):
            d["first_production_days"] = (r["first_production"] - base["first_production"]).days
        out.append(d)
    return out


def caseset_to_yaml(cases: List[dict]) -> str:
    return yaml.safe_dump({"schema": SCHEMA, "cases": cases}, sort_keys=False, allow_unicode=True)


def caseset_from_yaml(text: str) -> List[dict]:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ValueError("not a TieBack Studio case set")
    cases = doc.get("cases") or []
    for c in cases:
        if "project" not in c:
            raise ValueError(f"case '{c.get('name', '?')}' has no project data")
    return cases
