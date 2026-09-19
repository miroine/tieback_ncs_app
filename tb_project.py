"""
tb_project.py — Save/load a complete TieBack Studio project as one YAML file:
layout + catalog (incl. overrides) + cost settings + schedule settings.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, fields

import yaml

import tb_flowassurance as fa
import tb_map
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


def display_settings_from_dict(d: dict) -> "tb_map.DisplaySettings":
    valid = {f.name for f in fields(tb_map.DisplaySettings)}
    return tb_map.DisplaySettings(**{k: v for k, v in (d or {}).items() if k in valid})


def project_to_yaml(name: str, layout: Layout, cost: CostSettings, sched: ScheduleSettings,
                    fa_settings: "fa.FASettings" = None,
                    display: "tb_map.DisplaySettings" = None) -> str:
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
        "display_settings": asdict(display or tb_map.DisplaySettings()),
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def project_from_yaml(text: str):
    """Returns (name, layout, cost_settings, schedule_settings)."""
    return project_from_yaml_full(text)[:4]


def project_from_yaml_full(text: str):
    """Returns (name, layout, cost_settings, schedule_settings, fa_settings, display_settings).

    Accepts a project file, a bare layout file, and files whose `schema` line has
    been lost in editing — the shape of the document is used as a fallback.
    """
    try:
        doc = yaml.safe_load(text.lstrip("\ufeff \t\r\n") if isinstance(text, str) else text)
    except yaml.YAMLError as exc:
        raise ValueError(f"file is not valid YAML: {str(exc).splitlines()[0]}") from None
    if not isinstance(doc, dict):
        head = (text or "").strip().splitlines()[:1]
        raise ValueError("not a TieBack Studio project or layout file"
                         + (f" — it starts with: {head[0][:60]!r}" if head else " — the file is empty"))
    schema = doc.get("schema")
    looks_like_layout = schema == "tieback_layout/1" or ("nodes" in doc and "edges" in doc and "layout" not in doc)
    looks_like_project = schema == SCHEMA or ("layout" in doc and "catalog" in doc)
    if looks_like_layout and not looks_like_project:     # bare layout file → default catalog
        cat = Catalog()
        return ("Imported layout", Layout.from_dict({**doc, "schema": "tieback_layout/1"}, cat),
                CostSettings(), ScheduleSettings(), fa.FASettings(), tb_map.DisplaySettings())
    if not looks_like_project:
        raise ValueError(f"unsupported schema '{schema}' — top-level keys are "
                         f"{sorted(doc)[:8]}; expected a project or a layout file")
    doc = {**doc, "schema": SCHEMA}
    cat = Catalog.from_dict(doc["catalog"])
    layout = Layout.from_dict(doc["layout"], cat)
    return (doc.get("name", "Untitled"), layout, cost_settings_from_dict(doc.get("cost_settings")),
            schedule_settings_from_dict(doc.get("schedule_settings")),
            fa_settings_from_dict(doc.get("flow_assurance_settings")),
            display_settings_from_dict(doc.get("display_settings")))
