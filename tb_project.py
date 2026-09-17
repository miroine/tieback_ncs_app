"""
tb_project.py — Save/load a complete TieBack Studio project as one YAML file:
layout + catalog (incl. overrides) + cost settings + schedule settings.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, fields

import yaml

import tb_flowassurance as fa
from tb_catalog import Catalog
from tb_cost import CostSettings
from tb_network import Layout
from tb_schedule import ScheduleSettings, Window

SCHEMA = "tieback_project/1"


def schedule_settings_to_dict(s: ScheduleSettings) -> dict:
    d = asdict(s)
    d["dg2_date"] = s.dg2_date.isoformat()
    d["marine_window"] = {"start_mmdd": list(s.marine_window.start_mmdd),
                          "end_mmdd": list(s.marine_window.end_mmdd)}
    d["phase_offset_days"] = {int(k): float(v) for k, v in s.phase_offset_days.items()}
    return d


def schedule_settings_from_dict(d: dict) -> ScheduleSettings:
    d = dict(d or {})
    valid = {f.name for f in fields(ScheduleSettings)}
    d = {k: v for k, v in d.items() if k in valid}
    if "dg2_date" in d and not isinstance(d["dg2_date"], dt.date):
        d["dg2_date"] = dt.date.fromisoformat(str(d["dg2_date"]))
    if "marine_window" in d:
        w = d["marine_window"]
        d["marine_window"] = Window(tuple(w["start_mmdd"]), tuple(w["end_mmdd"]))
    if "phase_offset_days" in d:
        d["phase_offset_days"] = {int(k): float(v) for k, v in (d["phase_offset_days"] or {}).items()}
    return ScheduleSettings(**d)


def cost_settings_from_dict(d: dict) -> CostSettings:
    valid = {f.name for f in fields(CostSettings)}
    return CostSettings(**{k: v for k, v in (d or {}).items() if k in valid})


def fa_settings_from_dict(d: dict) -> "fa.FASettings":
    valid = {f.name for f in fields(fa.FASettings)}
    return fa.FASettings(**{k: v for k, v in (d or {}).items() if k in valid})


def project_to_yaml(name: str, layout: Layout, cost: CostSettings, sched: ScheduleSettings,
                    fa_settings: "fa.FASettings" = None) -> str:
    cat = layout.catalog.to_dict()
    for coll in (cat["spreads"], cat["items"]):
        for rec in coll:
            rec["uncertainty"] = list(rec["uncertainty"])
    doc = {
        "schema": SCHEMA,
        "name": name,
        "saved_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "catalog": cat,
        "layout": layout.to_dict(),
        "cost_settings": asdict(cost),
        "schedule_settings": schedule_settings_to_dict(sched),
        "flow_assurance_settings": asdict(fa_settings or fa.FASettings()),
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def project_from_yaml(text: str):
    """Returns (name, layout, cost_settings, schedule_settings)."""
    return project_from_yaml_full(text)[:4]


def project_from_yaml_full(text: str):
    """Returns (name, layout, cost_settings, schedule_settings, fa_settings)."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("not a project file")
    if doc.get("schema") == "tieback_layout/1":          # bare layout file → default catalog
        cat = Catalog()
        return "Imported layout", Layout.from_dict(doc, cat), CostSettings(), ScheduleSettings(), fa.FASettings()
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"unsupported project schema '{doc.get('schema')}'")
    cat = Catalog.from_dict(doc["catalog"])
    layout = Layout.from_dict(doc["layout"], cat)
    return (doc.get("name", "Untitled"), layout, cost_settings_from_dict(doc.get("cost_settings")),
            schedule_settings_from_dict(doc.get("schedule_settings")),
            fa_settings_from_dict(doc.get("flow_assurance_settings")))
