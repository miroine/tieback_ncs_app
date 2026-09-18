"""
tb_report.py — DG2/DG3 screening report as a Word document.

Builds a self-contained .docx from a project: concept summary, design checks,
quantities, CAPEX, schedule and flow assurance, plus the assumptions and limits
that belong with screening numbers. Charts are rendered with matplotlib (no
browser needed, so it also works on Streamlit Cloud).

python-docx is used rather than a Node toolchain so the report can be produced
inside the running app.
"""
from __future__ import annotations

import datetime as dt
import io
import math
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from docx import Document  # noqa: E402
from docx.enum.section import WD_ORIENT  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.shared import Inches, Pt, RGBColor  # noqa: E402

import tb_basis  # noqa: E402
import tb_cost  # noqa: E402
import tb_flowassurance as tb_fa  # noqa: E402
import tb_schedule  # noqa: E402

NAVY = RGBColor(0x00, 0x24, 0x3D)
TORCH = RGBColor(0xEB, 0x00, 0x37)
SLATE = "#243746"

DISCLAIMER = (
    "Screening-level concept study. Cost rates come from the project cost library and are indicative "
    "unless replaced with benchmarked data. Flow assurance is a steady-state Beggs & Brill screening "
    "with a black-oil fluid: it is not a substitute for OLGA, LedaFlow or PIPESIM. Bathymetry, where "
    "used, is the EMODnet regional grid, not a project survey."
)


def _fig_png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.set_facecolor("white")
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _capex_chart(est: dict):
    cats = [c for c in tb_cost.CATEGORIES if est["by_category"].get(c, 0) > 0]
    vals = [est["by_category"][c] / 1e6 for c in cats]
    fig, ax = plt.subplots(figsize=(6.6, 3.0))
    ax.barh(cats[::-1], vals[::-1], color=SLATE)
    ax.set_xlabel("MUSD")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color="#EAF0F4")
    ax.set_axisbelow(True)
    return _fig_png(fig)


def _profile_chart(annual: Dict[int, float]):
    fig, ax = plt.subplots(figsize=(6.6, 2.6))
    ax.bar([str(y) for y in annual], [v / 1e6 for v in annual.values()], color="#00243D")
    ax.set_ylabel("MUSD")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#EAF0F4")
    ax.set_axisbelow(True)
    return _fig_png(fig)


def _section_chart(section: List[dict]):
    fig, ax = plt.subplots(figsize=(6.6, 2.8))
    x = [r["distance_m"] / 1000 for r in section]
    bed = [r["seabed_elev_m"] for r in section]
    pipe = [r["pipe_elev_m"] for r in section]
    floor = min(bed) - 40
    ax.fill_between(x, bed, floor, color="#8C6D4F", alpha=0.35, linewidth=0)
    ax.plot(x, bed, color="#8C6D4F", linewidth=1)
    ax.plot(x, pipe, color="#00243D", linewidth=2.5, label="Line / riser")
    ax.axhline(0, color="#4A90C4", linewidth=1.5)
    ax.set_xlabel("Distance from wellhead (km)")
    ax.set_ylabel("Elevation (m)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="lower right")
    return _fig_png(fig)


def _heading(doc, text, level=1):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = NAVY
    return h


def _table(doc, headers: List[str], rows: List[list], widths: Optional[List[float]] = None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = str(h)
        for p in cell.paragraphs:
            for r in p.runs:
                r.font.bold = True
                r.font.size = Pt(9)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = "" if v is None else str(v)
            for p in cells[i].paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)
    if widths:
        for r in t.rows:
            for i, w in enumerate(widths):
                r.cells[i].width = Inches(w)
    return t


def _fmt(v, nd=1):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "—"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


def build_report(project_name: str, layout, cost_settings, sched_settings, fa_settings=None,
                 author: str = "", include_flow_assurance: bool = True, nok_per_usd: float = 10.5,
                 catalog_source: str = "") -> bytes:
    """Returns the .docx bytes for a screening report on this project."""
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.PORTRAIT
    for s_ in doc.sections:
        s_.left_margin = s_.right_margin = Inches(0.9)
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = title.add_run(project_name)
    run.font.size = Pt(26)
    run.font.bold = True
    run.font.color.rgb = NAVY
    sub = doc.add_paragraph()
    r2 = sub.add_run("Subsea tie-back concept screening")
    r2.font.size = Pt(13)
    r2.font.color.rgb = TORCH
    meta = doc.add_paragraph()
    meta.add_run(f"Prepared {dt.date.today():%d %B %Y}" + (f" · {author}" if author else "")).font.size = Pt(9)

    findings = layout.validate()
    est = tb_cost.estimate(layout, cost_settings)
    quantities = layout.quantities()
    cat = layout.catalog

    # ── summary ──
    _heading(doc, "Concept summary", 1)
    wells = sum(1 for n in layout.nodes if layout.kind(n) == "well")
    fl_km = sum(r["length_m"] for r in quantities if r["category"] == "flowline") / 1000
    umb_km = sum(r["length_m"] for r in quantities
                 if r["category"] in ("umbilical", "power_cable", "utility_line")) / 1000
    hosts = [layout.nodes[n].label or n for n in layout.nodes if layout.kind(n) == "host"]
    schedule = emap = None
    try:
        schedule, emap = tb_schedule.build_from_layout(layout, sched_settings)
        fo = sorted([a for a in schedule.activities.values() if a.act_id.endswith("_FIRST_OIL")],
                    key=lambda a: a.es)
        first_oil = f"{fo[0].es:%b %Y}" if fo else "—"
    except ValueError as exc:
        first_oil = f"not scheduled ({exc})"
    summary = [
        ["Wells", wells],
        ["Host", ", ".join(hosts) or "—"],
        ["Flowlines", f"{fl_km:,.1f} km"],
        ["Umbilicals, cables and utility lines", f"{umb_km:,.1f} km"],
        ["CAPEX, base estimate", f"{est['base_usd'] / 1e6:,.0f} MUSD"],
        ["CAPEX incl. contingency", f"{est['total_usd'] / 1e6:,.0f} MUSD"],
        ["First production", first_oil],
        ["Design checks", f"{sum(f.severity == 'error' for f in findings)} errors, "
                          f"{sum(f.severity == 'warning' for f in findings)} warnings"],
    ]
    _table(doc, ["Item", "Value"], summary, [3.0, 3.4])

    # ── design checks ──
    _heading(doc, "Design checks", 1)
    if findings:
        rows = [[f.severity.title(), f.element_id or "—", f.message] for f in
                sorted(findings, key=lambda f: {"error": 0, "warning": 1, "info": 2}[f.severity])]
        _table(doc, ["Severity", "Item", "Finding"], rows, [0.9, 0.9, 4.6])
    else:
        doc.add_paragraph("No findings: connectivity, pressure ratings, slots, control and power paths all pass.")

    # ── quantities ──
    doc.add_page_break()
    _heading(doc, "Equipment and quantities", 1)
    rows = []
    for r in sorted(quantities, key=lambda x: (x["category"], x["element_id"])):
        qty = f"{r['length_m']:,.0f} m" if r["length_m"] else "1"
        extra = f'{r["diameter_in"]:g}" ID' if r["diameter_in"] else ""
        if r.get("piggyback_on"):
            extra = (extra + " · strapped to " + r["piggyback_on"]).strip(" ·")
        rows.append([r["element_id"], r["item"], r["category"].replace("_", " "), qty, extra, r["phase"]])
    _table(doc, ["Tag", "Equipment", "Type", "Quantity", "Notes", "Phase"], rows,
           [0.7, 2.0, 1.0, 1.0, 1.3, 0.4])

    # ── cost ──
    doc.add_page_break()
    _heading(doc, "Cost estimate", 1)
    doc.add_paragraph(
        f"Deterministic build-up with {cost_settings.contingency_frac:.0%} contingency and a "
        f"{cost_settings.weather_factor:.2f} weather factor on offshore durations. "
        f"Drilling and completion cost is excluded.")
    _table(doc, ["Category", "MUSD"],
           [[c, _fmt(v / 1e6, 1)] for c, v in est["by_category"].items() if v], [3.4, 1.4])
    doc.add_picture(_capex_chart(est), width=Inches(6.4))
    if schedule is not None:
        prof = tb_cost.phase_costs(est, schedule, emap, cost_settings)
        if prof["annual"]:
            doc.add_paragraph("Annual CAPEX profile (incl. contingency):")
            doc.add_picture(_profile_chart(prof["annual"]), width=Inches(6.4))

    # ── schedule ──
    if schedule is not None:
        doc.add_page_break()
        _heading(doc, "Schedule", 1)
        doc.add_paragraph(
            f"CPM from DG2 on {sched_settings.dg2_date:%d %b %Y}. Marine campaigns are held inside the "
            f"{sched_settings.marine_window.start_mmdd[1]}/{sched_settings.marine_window.start_mmdd[0]}–"
            f"{sched_settings.marine_window.end_mmdd[1]}/{sched_settings.marine_window.end_mmdd[0]} season "
            f"window, with {sched_settings.rigs} rig(s) drilling.")
        tbl = schedule.table()
        rows = [[a["name"], f"{a['start']:%b %Y}", f"{a['finish']:%b %Y}", _fmt(a["duration_days"], 0),
                 "critical" if a["critical"] else _fmt(a["total_float_days"], 0)]
                for a in tbl if a["milestone"] or a["critical"]]
        _table(doc, ["Activity (milestones and critical path)", "Start", "Finish", "Days", "Float"],
               rows, [3.0, 0.9, 0.9, 0.6, 0.8])

    # ── flow assurance ──
    if include_flow_assurance and any(layout.kind(n) == "well" for n in layout.nodes):
        doc.add_page_break()
        _heading(doc, "Flow assurance", 1)
        try:
            res = tb_fa.solve(layout, fa_settings)
        except Exception as exc:  # noqa: BLE001
            doc.add_paragraph(f"Flow assurance could not be solved: {exc}")
        else:
            s = fa_settings or tb_fa.FASettings()
            doc.add_paragraph(
                f"Steady state at {s.arrival_bara:.0f} bara host arrival, seabed {s.seabed_temp_c:.0f} °C"
                + (f", {s.inhibitor} at {s.inhibitor_wt_pct:.0f} wt % in the water phase" if s.inhibitor != "None" else "")
                + (", Joule-Thomson cooling included" if s.include_jt else ", no Joule-Thomson cooling") + ".")
            if res.wells:
                _table(doc, ["Well", "Oil (Sm³/d)", "WHP required (bara)", "Available (bara)", "Margin (bar)"],
                       [[w["label"], _fmt(w["oil_sm3_d"], 0), _fmt(w["required_whp_bara"]),
                         _fmt(w["available_whp_bara"]), _fmt(w["margin_bar"])] for w in res.wells],
                       [1.2, 1.2, 1.5, 1.3, 1.0])
            rows = [[r.edge_id, f"{r.upstream}→{r.downstream}", _fmt(r.length_m, 0), f'{r.d_in:g}"',
                     _fmt(r.p_in_bara), _fmt(r.t_out_c), _fmt(r.min_hydrate_margin_c),
                     "—" if r.heated or math.isinf(r.cooldown_h) else _fmt(r.cooldown_h)]
                    for r in res.edges.values()]
            doc.add_paragraph("Lines:")
            _table(doc, ["Line", "Flow", "Length (m)", "ID", "P in (bara)", "T out (°C)",
                         "Hydrate margin (°C)", "Cool-down (h)"], rows,
                   [0.7, 1.1, 0.9, 0.5, 0.9, 0.8, 1.1, 0.9])
            if res.findings:
                doc.add_paragraph("Findings:")
                _table(doc, ["Severity", "Item", "Finding"],
                       [[a.title(), b, c] for a, b, c in res.findings], [0.9, 0.9, 4.6])
            wells_ids = [w["well"] for w in res.wells]
            if wells_ids:
                section = tb_fa.path_section(layout, res, wells_ids[0], fa_settings)
                if section:
                    doc.add_paragraph(f"Route section, {res.wells[0]['label']} to host:")
                    doc.add_picture(_section_chart(section), width=Inches(6.4))

    # ── design basis checklist ──
    doc.add_page_break()
    _heading(doc, "Design basis checklist", 1)
    basis = tb_basis.design_basis(layout, cost_settings, sched_settings, fa_settings, nok_per_usd,
                                  catalog_source)
    counts = tb_basis.summary(basis)
    doc.add_paragraph(f"{counts['ok']} items entered for this project, {counts['default']} on built-in "
                      f"defaults, {counts['missing']} missing, {counts['action']} to resolve. "
                      + tb_basis.UNITS_NOTE)
    _table(doc, ["Category", "Item", "Value (SI)", "Status"],
           [[r["category"], r["item"], r["value"], r["status"]] for r in basis], [1.3, 1.9, 1.9, 0.7])
    todo = tb_basis.outstanding(basis)
    if todo:
        doc.add_paragraph("Outstanding before these numbers can be relied on:")
        for r in todo:
            doc.add_paragraph(f"{r['item']} ({r['category']}): {r['value']}"
                              + (f" — {r['note']}" if r["note"] else ""), style="List Bullet")

    # ── assumptions ──
    doc.add_page_break()
    _heading(doc, "Basis and limitations", 1)
    doc.add_paragraph(DISCLAIMER)
    basis = [
        ["Coordinate datum", layout.settings.datum],
        ["Route allowance", f"{layout.settings.route_allowance_frac:.0%} plus "
                            f"{layout.settings.end_allowance_m:.0f} m per line"],
        ["Minimum lay bend radius", f"{layout.settings.min_bend_radius_m:,.0f} m"],
        ["Weather factor", f"{cost_settings.weather_factor:.2f}"],
        ["Contingency", f"{cost_settings.contingency_frac:.0%}"],
        ["Owner's costs", f"{cost_settings.owners_cost_frac:.0%}"],
        ["Cost basis", "Indicative catalog rates unless a project cost library is loaded"],
        ["Excluded", "Drilling and completion, abandonment, operating cost, tariffs"],
    ]
    _table(doc, ["Basis", "Value"], basis, [2.4, 4.0])

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
