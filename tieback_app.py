"""
TieBack Studio — subsea tie-back concept design for the NCS.

Streamlit UI only; all engineering logic lives in tb_*.py modules.
Run:  streamlit run tieback_app.py
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import yaml

import tb_basis
import tb_bathymetry
import tb_cases
import tb_catalog
import tb_cost
import tb_costio
import tb_flowassurance as tb_fa
import tb_geo
import tb_grid
import tb_import
import tb_map
import tb_ncs
import tb_network
import tb_project
import tb_report
import tb_schedule
import tb_tiein
import tb_viability
import tb_well

APP_VERSION = "0.11.0"
HERE = Path(__file__).parent
DEMO_FILE = HERE / "test_fixtures" / "demo_field_a_tieback.yaml"

EQ = dict(torch="#EB0037", navy="#00243D", karry="#FFE7D6", pistachio="#9DBA00",
          slate="#243746", mist="#EAF0F4", line="#C3CDD5", amber="#E9A23B", teal="#007079")
GROUP_COLORS = {"Milestones": EQ["torch"], "Engineering": "#4A6B82", "Procurement": EQ["teal"],
                "Installation": EQ["navy"], "Drilling": EQ["amber"], "Host": "#7D4EBF",
                "Commissioning": EQ["pistachio"]}

st.set_page_config(page_title="TieBack Studio", page_icon="🌊", layout="wide")


# ───────────────────────────── helpers ─────────────────────────────

def _st_version():
    try:
        return tuple(int(x) for x in st.__version__.split(".")[:2])
    except Exception:
        return (0, 0)


STRETCH = {"width": "stretch"} if _st_version() >= (1, 49) else {"use_container_width": True}


def eq_layout(fig, height=380, **kw):
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=40, b=10), paper_bgcolor="white", plot_bgcolor="white",
        font=dict(family="Equinor, Segoe UI, Arial, sans-serif", color=EQ["navy"], size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), **kw)
    fig.update_xaxes(gridcolor=EQ["mist"], linecolor=EQ["line"])
    fig.update_yaxes(gridcolor=EQ["mist"], linecolor=EQ["line"])
    return fig


def md5(*objs) -> str:
    h = hashlib.md5()
    for o in objs:
        h.update(json.dumps(o, sort_keys=True, default=str).encode())
    return h.hexdigest()[:12]


def money(v_usd: float) -> str:
    cur = st.session_state.currency
    if cur == "MNOK":
        return f"{v_usd * st.session_state.nok_per_usd / 1e6:,.0f} MNOK"
    return f"{v_usd / 1e6:,.1f} MUSD"


def money_factor() -> float:
    return st.session_state.nok_per_usd / 1e6 if st.session_state.currency == "MNOK" else 1e-6


def bump():
    """Layout changed outside a keyed widget → refresh widget keys."""
    st.session_state.rev += 1


def _looks_like_grid(data: bytes) -> bool:
    """A .grd/.dat/.txt may be a grid or a point list, so ask the grid sniffer."""
    try:
        tb_grid.sniff(data)
        return True
    except Exception:  # noqa: BLE001
        return False


def _add_grid(grid, ramp: str, opacity: float, n_levels: int):
    """Put a grid on the map: a colour image, and contours as a normal vector layer."""
    S = st.session_state
    S.grids = [e for e in S.get("grids", []) if e["grid"].name != grid.name]
    S.grid_rasters = [r for r in S.get("grid_rasters", []) if r.get("title") != grid.name]
    S.user_overlays = [o for o in S.user_overlays if o.get("title") != f"{grid.name} contours"]
    raster = tb_grid.image_overlay(grid, ramp=ramp, opacity=opacity, title=grid.name)
    S.grid_rasters.append(raster)
    if n_levels > 0:
        st_ = grid.stats()
        levels = tb_grid.nice_levels(st_["min"], st_["max"], int(n_levels))
        fc = tb_grid.contour_features(grid, levels, ramp=ramp, title=f"{grid.name} contours")
        fc["rev"] = f"contours:{grid.name}:{len(levels)}:{ramp}"
        S.user_overlays.append(fc)
    S.grids.append({"grid": grid, "convention": grid.depth_convention()})
    return raster


UNDO_DEPTH = 25


def record(label: str = ""):
    """Snapshot the layout before changing it, so the change can be undone."""
    ss = st.session_state
    if "layout" not in ss:
        return
    ss.setdefault("undo", []).append((label, ss.layout.to_dict()))
    del ss.undo[:-UNDO_DEPTH]
    ss.redo = []


def _restore_layout(dct):
    st.session_state.layout = tb_network.Layout.from_dict(dct, st.session_state.layout.catalog)


def undo():
    ss = st.session_state
    if not ss.get("undo"):
        return ""
    label, dct = ss.undo.pop()
    ss.setdefault("redo", []).append((label, ss.layout.to_dict()))
    _restore_layout(dct)
    bump()
    return label


def redo():
    ss = st.session_state
    if not ss.get("redo"):
        return ""
    label, dct = ss.redo.pop()
    ss.setdefault("undo", []).append((label, ss.layout.to_dict()))
    _restore_layout(dct)
    bump()
    return label


def empty_project():
    return ("New tie-back", tb_network.Layout(tb_catalog.Catalog()), tb_cost.CostSettings(),
            tb_schedule.ScheduleSettings(), tb_fa.FASettings(), tb_map.DisplaySettings())


def load_demo():
    """The demo project, or an empty one if the file is missing or unreadable.

    A broken demo file must never stop the app from starting — the reason is
    reported in the sidebar diagnostics instead.
    """
    if not DEMO_FILE.exists():
        st.session_state.demo_error = f"{DEMO_FILE} is not in the deployment"
        return empty_project()
    try:
        txt = DEMO_FILE.read_text(encoding="utf-8-sig")
        _, lay, cs, ss, fas, disp = tb_project.project_from_yaml_full(txt)
    except Exception as exc:  # noqa: BLE001 — start anyway and explain
        st.session_state.demo_error = f"{DEMO_FILE.name}: {exc}"
        return empty_project()
    st.session_state.demo_error = ""
    return "Field A tie-back (demo)", lay, cs, ss, fas, disp


def set_project(name, layout, cost, sched, fa_settings=None, display=None):
    st.session_state.display = display or tb_map.DisplaySettings()
    st.session_state.fa_settings = fa_settings or tb_fa.FASettings()
    st.session_state.project_name = name
    st.session_state.layout = layout
    st.session_state.cost_settings = cost
    st.session_state.sched_settings = sched
    st.session_state.weather_factor = cost.weather_factor
    st.session_state.undo, st.session_state.redo = [], []
    st.session_state.map_state = {k: v for k, v in st.session_state.get("map_state", {}).items()
                                  if k in ("view", "picked", "seen")}
    st.session_state.mc = None
    st.session_state.fit_token += 1
    bump()


def init_state():
    ss = st.session_state
    if "layout" in ss:
        return
    ss.rev = 0
    ss.fit_token = 0
    ss.ncs_overlays = {}
    ss.user_overlays = []
    ss.grids = []            # imported .grd surfaces: [{"grid": Grid, "convention": str}]
    ss.grid_rasters = []     # their image overlays, as the map component wants them
    ss.currency = "MUSD"
    ss.nok_per_usd = 10.5
    ss.last_upload = None
    ss.last_layer_upload = None
    ss.catalog_source = ""
    set_project(*load_demo())


init_state()
S = st.session_state
LAY: tb_network.Layout = S.layout
CAT: tb_catalog.Catalog = LAY.catalog
REV = S.rev

# ───────────────────────────── style ─────────────────────────────

st.markdown(f"""
<style>
  [data-testid="stAppViewContainer"] {{ background: #FFFFFF; }}
  [data-testid="stSidebar"] {{ background: {EQ['mist']}; }}
  [data-testid="stMetric"] {{ border-left: 3px solid {EQ['navy']}; padding: 4px 10px; background: #FAFCFD; }}
  [data-testid="stMetricValue"] {{ color: {EQ['navy']}; font-size: 1.45rem; }}
  [data-testid="stMetricLabel"] p {{ color: {EQ['slate']}; }}
  .tb-title {{ display:flex; align-items:baseline; gap:14px; border-bottom: 2px solid {EQ['navy']}; padding-bottom: 6px; margin-bottom: 8px; }}
  .tb-title h1 {{ font-size: 1.9rem; margin: 0; color: {EQ['navy']}; letter-spacing: -0.01em; }}
  .tb-title span {{ color: {EQ['slate']}; font-size: 0.95rem; }}
  .tb-flag {{ border-left: 4px solid {EQ['torch']}; background: {EQ['karry']}; padding: 8px 12px; color: {EQ['navy']}; font-size: 0.9rem; }}
  .tb-foot {{ color: #6F6F6F; font-size: 0.78rem; border-top: 1px solid {EQ['line']}; margin-top: 24px; padding-top: 8px; }}
</style>
""", unsafe_allow_html=True)

st.markdown(f"""<div class="tb-title"><h1>TieBack Studio</h1>
<span>{S.project_name} · subsea tie-back concept design for the Norwegian Continental Shelf</span></div>""",
            unsafe_allow_html=True)

# ─────────────────────── cached computations ───────────────────────

def _session():
    sess = requests.Session()
    sess.headers.update({"User-Agent": f"TieBackStudio/{APP_VERSION}"})
    return sess


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_ncs_cached(key: str, bbox: tuple, simplify: float = 0.0005, max_features: int = 5000) -> dict:
    return tb_ncs.fetch_layer(key, bbox, _session(), max_features=max_features, simplify_deg=simplify)


# ───────────────────────────── sidebar ─────────────────────────────

with st.sidebar:
    st.subheader("Project")
    new_name = st.text_input("Project name", S.project_name, key=f"pname_{REV}")
    if new_name != S.project_name:
        S.project_name = new_name
    up = st.file_uploader("Open project (.yaml)", type=["yaml", "yml"], key="proj_upload")
    if up is not None:
        data = up.getvalue()
        sig = hashlib.md5(data).hexdigest()
        if sig != S.last_upload:
            S.last_upload = sig
            try:
                loaded = tb_project.project_from_yaml_full(data.decode("utf-8-sig"))
            except Exception as exc:  # noqa: BLE001 — show any parse problem to the user
                st.error(f"Could not open project: {exc}")
            else:
                set_project(*loaded)
                st.rerun()
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("Save project", tb_project.project_to_yaml(S.project_name, LAY, S.cost_settings,
                                                                      S.sched_settings, S.fa_settings, S.display),
                           file_name=f"{S.project_name.replace(' ', '_')}.yaml", mime="text/yaml")
    with c2:
        if st.button("Load demo"):
            set_project(*load_demo())
            st.rerun()
    tpl_dir = HERE / "templates"
    tpls = sorted(tpl_dir.glob("*.yaml")) if tpl_dir.exists() else []
    if tpls:
        names = {}
        for f in tpls:
            try:
                names[f.name] = yaml.safe_load(f.read_text()).get("name", f.stem)
            except Exception:  # noqa: BLE001
                names[f.name] = f.stem
        pick = st.selectbox("Start from a concept template", list(names), format_func=lambda k: names[k],
                            key=f"tpl_{REV}")
        picked = S.map_state.get("picked")
        view = S.map_state.get("view")
        where = ["Picked point on the map", "Centre of the current map view", "Template's own coordinates"]
        avail = [w for w in where if (w != where[0] or picked) and (w != where[1] or view)]
        place_mode = st.radio("Place it at", avail, key=f"tplwhere_{REV}",
                              help="Use the map's 'Pick point' tool to choose a spot, then load the template there")
        if picked:
            st.caption(f"Picked point: {picked[0]:.5f}°N, {picked[1]:.5f}°E")
        else:
            st.caption("No point picked yet — use 'Pick point' on the map toolbar.")
        if st.button("Load template"):
            try:
                lay = tb_network.Layout.from_dict(yaml.safe_load((tpl_dir / pick).read_text()), tb_catalog.Catalog())
                if place_mode == where[0] and picked:
                    lay.place_at(float(picked[0]), float(picked[1]))
                elif place_mode == where[1] and view:
                    lay.place_at((view[1] + view[3]) / 2, (view[0] + view[2]) / 2)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Template failed to load: {exc}")
            else:
                set_project(names[pick], lay, tb_cost.CostSettings(), tb_schedule.ScheduleSettings(),
                            tb_fa.FASettings())
                st.rerun()
    if st.button("New empty layout (keeps catalog)"):
        set_project("New tie-back", tb_network.Layout(CAT, copy.deepcopy(LAY.settings)),
                    S.cost_settings, S.sched_settings, S.fa_settings)
        st.rerun()

    if S.get("cases"):
        st.subheader("Concepts")
        names = [c_["name"] for c_ in S.cases]
        current = S.get("active_case") or "(working layout)"
        options = ["(working layout)"] + names
        pick_active = st.selectbox("Active concept", options,
                                   index=options.index(current) if current in options else 0,
                                   key=f"active_case_{REV}",
                                   help="Switching loads that concept into the editor — cost, schedule, "
                                        "flow assurance, viability and the report all follow it")
        if pick_active != current:
            if S.get("autosave_case", True) and current in names:
                S.cases = [c_ for c_ in S.cases if c_["name"] != current]
                S.cases.append(tb_cases.snapshot(current, LAY, S.cost_settings, S.sched_settings,
                                                 S.fa_settings, "", S.display))
            if pick_active != "(working layout)":
                set_project(*tb_cases.restore(next(c_ for c_ in S.cases if c_["name"] == pick_active)))
            S.active_case = pick_active
            S.case_rows = None
            st.rerun()
        S.autosave_case = st.checkbox("Save edits back when switching", S.get("autosave_case", True))
        S.ghost_cases = st.multiselect("Also show on the map", [n_ for n_ in names if n_ != pick_active],
                                       default=[g for g in S.get("ghost_cases", []) if g in names],
                                       help="Draws the other concepts behind the active one")

    st.subheader("Design basis")
    datums = list(tb_geo.DATUMS)
    d_sel = st.selectbox("Layout datum", datums, index=datums.index(LAY.settings.datum), key=f"datum_{REV}",
                         help="Coordinates are stored as lat/lon in this datum. Map display assumes WGS84.")
    allow = st.number_input("Route allowance (%)", 0.0, 20.0, float(LAY.settings.route_allowance_frac) * 100, 0.5,
                            key=f"allow_{REV}", help="Seabed undulation, lay tolerance, route deviations")
    endal = st.number_input("End allowance per line (m)", 0.0, 2000.0, float(LAY.settings.end_allowance_m), 10.0,
                            key=f"endal_{REV}", help="Tie-in spools and overlength")
    wf = st.number_input("Offshore weather factor", 1.0, 3.0, float(S.weather_factor), 0.05, key=f"wf_{REV}",
                         help="Multiplies offshore durations in both cost and schedule")
    S.auto_land = st.checkbox("Land new wells in a nearby template automatically",
                              S.get("auto_land", True), key=f"autoland_{REV}",
                              help="A well placed within 250 m of a template or manifold with a free slot "
                                   "is tied in for you")
    minr = st.number_input("Minimum lay bend radius (m)", 0.0, 5000.0,
                           float(LAY.settings.min_bend_radius_m), 50.0, key=f"minr_{REV}",
                           help="Routes with tighter bends are flagged in the design checks")
    LAY.settings.min_bend_radius_m = minr
    LAY.settings.datum, LAY.settings.route_allowance_frac, LAY.settings.end_allowance_m = d_sel, allow / 100, endal
    S.weather_factor = wf
    S.cost_settings.weather_factor = wf
    S.sched_settings.weather_factor = wf
    cur = st.radio("Currency display", ["MUSD", "MNOK"], horizontal=True, index=["MUSD", "MNOK"].index(S.currency))
    S.currency = cur
    if cur == "MNOK":
        S.nok_per_usd = st.number_input("NOK per USD", 1.0, 30.0, float(S.nok_per_usd), 0.1)

    with st.expander("Appearance"):
        disp = S.display
        modes = {"item": "Equipment type", "fluid": "Fluid / service", "phase": "Development phase",
                 "checks": "Design checks"}
        cmode = st.selectbox("Colour lines by", list(modes), format_func=lambda k: modes[k],
                             index=list(modes).index(disp.color_mode), key=f"cmode_{REV}")
        cc = st.columns(2)
        sym = cc[0].slider("Symbol size", 0.4, 3.0, float(disp.symbol_scale), 0.1, key=f"symsc_{REV}")
        lsc = cc[1].slider("Line thickness", 0.4, 3.0, float(disp.line_scale), 0.1, key=f"linesc_{REV}")
        bydia = st.checkbox("Thicker lines for larger bore", bool(disp.thickness_by_diameter),
                            key=f"bydia_{REV}")
        colors = dict(disp.fluid_colors)
        if cmode == "fluid":
            st.caption("Colour per fluid or service")
            cols = st.columns(2)
            for i, f_ in enumerate(tb_map.FLUIDS):
                colors[f_] = cols[i % 2].color_picker(f_.title(), colors.get(f_, tb_map.FLUID_COLORS[f_]),
                                                      key=f"fc_{f_}_{REV}")
            if st.button("Reset fluid colours"):
                colors = dict(tb_map.FLUID_COLORS)
                S.display = tb_map.DisplaySettings(sym, lsc, bydia, cmode, colors)
                bump()
                st.rerun()
        if (cmode, sym, lsc, bydia, colors) != (disp.color_mode, disp.symbol_scale, disp.line_scale,
                                                disp.thickness_by_diameter, disp.fluid_colors):
            S.display = tb_map.DisplaySettings(sym, lsc, bydia, cmode, colors)
        all_tags = LAY.all_tags()
        if all_tags:
            st.caption("Tag filter")
            S.tag_include = st.multiselect("Show only", all_tags,
                                           default=[t for t in S.get("tag_include", []) if t in all_tags],
                                           key=f"tagin_{REV}")
            S.tag_exclude = st.multiselect("Hide", all_tags,
                                           default=[t for t in S.get("tag_exclude", []) if t in all_tags],
                                           key=f"tagex_{REV}")
        else:
            st.caption("No tags yet — add them in an item's properties to filter the map.")

    st.subheader("NCS map layers")
    st.caption("Live from Sodir FactMaps (WGS84). Fetched for the layout area or the current map view.")
    layer_keys = list(tb_ncs.NCS_LAYERS)
    chosen = st.multiselect("Layers", layer_keys,
                            default=[k for k in layer_keys if tb_ncs.NCS_LAYERS[k].default_on],
                            format_func=lambda k: tb_ncs.NCS_LAYERS[k].title)
    buffer_km = st.slider("Area around layout (km)", 5, 150, 40, 5)
    b1, b2 = st.columns(2)
    load_layout_area = b1.button("Load for layout")
    view = S.map_state.get("view")
    load_view = b2.button("Load for map view", disabled=not view,
                          help="Pan or interact with the map first so its view is known")
    load_all_disc = st.button("All NCS discoveries", help="Every discovery on the shelf, coloured by main "
                                                          "hydrocarbon type as in FactMaps")
    if st.button("Clear NCS layers"):
        S.ncs_overlays = {}
    if load_all_disc:
        with st.spinner("Fetching all NCS discoveries…"):
            try:
                fc = fetch_ncs_cached("discoveries_all", tb_ncs.NCS_BBOX, 0.002, 4000)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Discoveries: {exc}")
            else:
                S.ncs_overlays["discoveries_all"] = {**fc, "rev": "all-ncs"}
                st.success(f"{len(fc['features'])} discoveries loaded")

    st.subheader("Bathymetry")
    st.caption("EMODnet DTM (~115 m grid, depths to LAT). Switch the bathymetry and depth-contour "
               "overlays on in the map's layer control, top right.")
    blank_only = st.checkbox("Only nodes with no depth", True)
    if st.button("Fetch seabed profiles along routes"):
        with st.spinner("Sampling the seabed along each line…"):
            try:
                got = tb_bathymetry.fetch_route_profiles(LAY, _session())
            except Exception as exc:  # noqa: BLE001
                st.error(f"Profile lookup failed: {exc}")
            else:
                ok = sum(1 for v in got.values() if v)
                if ok:
                    st.success(f"Seabed profile stored for {ok} of {len(got)} lines — flow assurance now "
                               f"follows the real terrain")
                    bump()
                    st.rerun()
                else:
                    st.warning("No profiles returned for those routes")
    if st.button("Fill water depths from EMODnet"):
        with st.spinner("Sampling the EMODnet DTM…"):
            try:
                got = tb_bathymetry.fill_node_depths(LAY, _session(), only_blank=blank_only)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Depth lookup failed: {exc}")
            else:
                ok = sum(1 for v in got.values() if v is not None)
                if ok:
                    st.success(f"Set depth on {ok} of {len(got)} nodes")
                    bump()
                    st.rerun()
                else:
                    st.warning("No depths returned for those positions")
    if load_layout_area or load_view:
        if load_view:
            bbox = tuple(round(v, 4) for v in view)
        else:
            pts = [(n.lat, n.lon) for n in LAY.nodes.values()] or [(60.5, 2.8)]
            bbox = tuple(round(v, 4) for v in tb_ncs.bbox_around(pts, buffer_km))
        S.ncs_overlays = {}
        with st.spinner("Fetching Sodir FactMaps layers…"):
            for k in chosen:
                try:
                    fc = fetch_ncs_cached(k, bbox)
                except Exception as exc:  # noqa: BLE001 — network/service errors shown per layer
                    st.warning(f"{tb_ncs.NCS_LAYERS[k].title}: {exc}")
                    continue
                S.ncs_overlays[k] = {**fc, "rev": f"{k}:{bbox}"}
                if not fc["features"]:
                    st.info(f"{fc['title']}: nothing inside this area — widen the search radius or pan to "
                            f"the field you are tying into.")
                elif fc.get("truncated"):
                    st.info(f"{fc['title']}: showing first {len(fc['features'])} features — zoom in and load for map view.")

    st.subheader("Import map layer")
    lfs = st.file_uploader("GeoJSON, KML/KMZ, shapefile (.shp with its .dbf/.prj, or a .zip), "
                           "CSV, or a grid (.grd/.asc/.irap/.zmap)",
                           type=["geojson", "json", "kml", "kmz", "zip", "shp", "dbf", "prj", "shx", "cpg",
                                 "csv", "txt", "grd", "asc", "irap", "zmap", "dat"],
                           accept_multiple_files=True, key="layer_upload")
    st.caption("Select a .shp together with its .dbf and .prj — a shapefile carries no coordinate "
               "system of its own, so without the .prj you must set it below.")
    crs_mode = st.selectbox("Source coordinates", ["From file (or WGS84 lat/lon)", "WGS84 lat/lon", "ED50 lat/lon",
                                                   "WGS84 UTM", "ED50 UTM"])
    zone = st.number_input("UTM zone (north)", 1, 60, 31, 1, disabled="UTM" not in crs_mode)
    sel_crs = {"WGS84 lat/lon": tb_import.WGS84_GEO, "ED50 lat/lon": tb_import.Crs("geographic", "ED50"),
               "WGS84 UTM": tb_import.Crs("utm", "WGS84", int(zone)),
               "ED50 UTM": tb_import.Crs("utm", "ED50", int(zone))}.get(crs_mode)
    files = {f.name: f.getvalue() for f in (lfs or [])}
    grid_names = [nm for nm, data in files.items() if _looks_like_grid(data)]
    vector_files = {nm: d for nm, d in files.items() if nm not in grid_names}

    if vector_files and st.button("Add layer to map"):
        try:
            palette = ["#4A6B82", "#7D4EBF", "#B08D57", "#007079", "#C4561B"]
            for name, fc in tb_import.read_uploads(vector_files, sel_crs):
                fc.update(title=name, color=palette[len(S.user_overlays) % len(palette)],
                          rev=hashlib.md5(vector_files[name]).hexdigest()[:10])
                S.user_overlays.append(fc)
                st.success(f"Added {len(fc['features'])} features from {name}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Import failed: {exc}")

    if grid_names:
        st.markdown("**Grid surface**")
        gname = st.selectbox("Grid file", grid_names, key="grid_file")
        swap = st.checkbox("Swap grid axes", value=False, key="grid_swap",
                           help="IRAP classic ASCII does not state which axis cycles fastest. "
                                "Tick this if the surface comes out with its axes swapped.")
        try:
            preview = tb_grid.read_grid(gname, files[gname], sel_crs, transposed=swap)
            st.caption(f"{preview.source_format} — {preview.describe()}")
            gc = st.columns(3)
            ramp = gc[0].selectbox("Colour ramp", list(tb_grid.RAMPS), key="grid_ramp")
            opacity = gc[1].slider("Opacity", 0.1, 1.0, 0.7, 0.05, key="grid_opacity")
            n_lv = gc[2].number_input("Contour lines", 0, 60, 12, 1, key="grid_levels",
                                      help="0 draws no contours — image only.")
            if st.button("Add grid to map"):
                try:
                    _add_grid(preview, ramp, float(opacity), int(n_lv))
                    st.success(f"Added {preview.name} ({preview.nx} × {preview.ny} nodes)")
                    S.fit_token += 1
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not draw the grid: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Grid import failed: {exc}")

    if S.get("grids"):
        st.markdown("**Loaded grids**")
        gnames = [e["grid"].name for e in S.grids]
        pick = st.selectbox("Grid", gnames, key="grid_pick")
        entry = next(e for e in S.grids if e["grid"].name == pick)
        grid = entry["grid"]
        cov = tb_grid.coverage(LAY, grid)
        st.caption(f"{grid.describe()} — covers {cov['nodes_on_grid']} of "
                   f"{cov['nodes_on_grid'] + cov['nodes_off_grid']} elements in the layout")
        conv = st.selectbox("These values are", ["positive_down", "elevation"],
                            index=0 if entry["convention"] != "elevation" else 1,
                            format_func=lambda k: ("Water depth, positive down (m)" if k == "positive_down"
                                                   else "Seabed elevation, negative below sea level (m)"),
                            key="grid_conv")
        entry["convention"] = conv
        gb = st.columns(3)
        if gb[0].button("Set element depths"):
            got = tb_grid.fill_node_depths(LAY, grid, only_blank=False, convention=conv)
            hit = sum(1 for v in got.values() if v is not None)
            bump()
            st.success(f"Water depth set on {hit} of {len(got)} elements from {grid.name}")
        if gb[1].button("Seabed profiles from grid"):
            got = tb_grid.fetch_route_profiles(LAY, grid, convention=conv)
            ok_n = sum(1 for v in got.values() if v)
            bump()
            if ok_n:
                st.success(f"Stored a profile on {ok_n} of {len(got)} lines — "
                           f"turn on 'Follow stored seabed profiles' in Flow assurance.")
            else:
                st.warning("No line lies wholly inside the grid, so no profile was stored. "
                           "A part-covered profile would bias the free-span and cool-down checks.")
        if gb[2].button("Remove grid"):
            S.grids = [e for e in S.grids if e["grid"].name != pick]
            S.grid_rasters = [r for r in S.grid_rasters if r.get("title") != pick]
            S.user_overlays = [o for o in S.user_overlays if o.get("title") != f"{pick} contours"]
            st.rerun()
    if S.user_overlays:
        names = [o["title"] for o in S.user_overlays]
        drop = st.selectbox("Imported layers", names, key="drop_layer")
        cc1, cc2 = st.columns(2)
        if cc1.button("Remove layer"):
            S.user_overlays = [o for o in S.user_overlays if o["title"] != drop]
            st.rerun()
        pts_layer = next((o for o in S.user_overlays if o["title"] == drop), None)
        n_pts = len(tb_import.point_features(pts_layer)) if pts_layer else 0
        if n_pts:
            node_items = [i for i in CAT.items.values() if i.category in tb_catalog.NODE_KINDS]
            conv_item = st.selectbox("Convert its points to", [i.item_id for i in node_items],
                                     format_func=lambda i: CAT.get(i).name, key="conv_item")
            if cc2.button(f"Place {n_pts} items"):
                it = CAT.get(conv_item)
                for lat, lon, props in tb_import.point_features(pts_layer):
                    nid = tb_map.next_id(tb_map.ID_PREFIX.get(it.category, "N"), list(LAY.nodes) + list(LAY.edges))
                    label = tb_import.feature_label(props) or nid
                    la, lo = tb_map.from_display(LAY, lat, lon)  # imported layers are WGS84
                    LAY.add_node(tb_network.Node(nid, it.item_id, la, lo, label=label[:40]))
                bump()
                S.fit_token += 1
                st.rerun()

    if S.get("demo_error"):
        st.warning(f"Demo project not loaded — {S.demo_error}. The app started with an empty layout; "
                   f"open a project, load a template, or fix the file in the repository.")
    with st.expander("Diagnostics"):
        import platform
        rows = [("App version", APP_VERSION), ("Python", platform.python_version()),
                ("Streamlit", st.__version__), ("App folder", str(HERE)),
                ("Demo file", f"{DEMO_FILE.name}: "
                              + (f"{DEMO_FILE.stat().st_size:,} bytes" if DEMO_FILE.exists() else "MISSING"))]
        if DEMO_FILE.exists():
            try:
                first = DEMO_FILE.read_text(encoding="utf-8-sig").strip().splitlines()[0][:60]
            except Exception as exc:  # noqa: BLE001
                first = f"unreadable: {exc}"
            rows.append(("Demo file first line", first))
        tpl_n = len(list((HERE / "templates").glob("*.yaml"))) if (HERE / "templates").exists() else 0
        rows.append(("Templates found", str(tpl_n)))
        expected = {"tb_project": "project_from_yaml_full", "tb_map": "DisplaySettings",
                    "tb_network": "place_at", "tb_flowassurance": "solve_coupled", "tb_tiein": "screen",
                    "tb_basis": "design_basis", "tb_cases": "snapshot", "tb_report": "build_report"}
        stale = []
        for mod_name, attr in expected.items():
            mod = globals().get(mod_name if mod_name != "tb_flowassurance" else "tb_fa")
            target = mod if attr[0].isupper() or not hasattr(tb_network.Layout, attr) else tb_network.Layout
            if mod is None or not (hasattr(mod, attr) or hasattr(tb_network.Layout, attr)):
                stale.append(mod_name)
        rows.append(("Module check", "all modules current" if not stale
                     else f"older than the app: {', '.join(stale)} — re-upload those files"))
        st.dataframe(pd.DataFrame(rows, columns=["Item", "Value"]), hide_index=True, **STRETCH)

    st.markdown('<div class="tb-foot">Screening tool for engineering concept work. Default cost rates are '
                'indicative placeholders. Not affiliated with or endorsed by Equinor or Sodir. '
                f'v{APP_VERSION} · MIT</div>', unsafe_allow_html=True)


# ─────────────────────────────── tabs ───────────────────────────────

findings = LAY.validate()
n_err = sum(f.severity == "error" for f in findings)
n_warn = sum(f.severity == "warning" for f in findings)

tab_layout, tab_cat, tab_cost, tab_sched, tab_fa, tab_basis, tab_viab, tab_cases, tab_exp = st.tabs(
    ["Layout", "Equipment catalog", "Cost", "Schedule", "Flow assurance", "Design basis", "Viability",
     "Cases", "Export"])

# ═══════════════════════════════ LAYOUT ═══════════════════════════════
with tab_layout:
    q = pd.DataFrame(LAY.quantities())
    km = lambda cat_: q.loc[q["category"] == cat_, "length_m"].sum() / 1000 if len(q) else 0.0
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Wells", int((q["category"] == "well").sum()) if len(q) else 0)
    m2.metric("Structures", int(q["category"].isin(tb_schedule.STRUCTURE_KINDS).sum()) if len(q) else 0)
    m3.metric("Flowlines", f"{km('flowline'):.1f} km")
    m4.metric("Umbilicals", f"{km('umbilical'):.1f} km")
    m5.metric("Checks", f"{n_err} errors · {n_warn} warnings" if (n_err or n_warn) else "All clear")

    cc = st.columns([1, 1, 1, 3])
    if cc[0].button("Undo", disabled=not S.get("undo"),
                    help=f"Undo {S.undo[-1][0]}" if S.get("undo") else "Nothing to undo"):
        undo()
        st.rerun()
    if cc[1].button("Redo", disabled=not S.get("redo")):
        redo()
        st.rerun()
    if S.get("undo"):
        cc[2].caption(f"{len(S.undo)} step(s)")

    overlays = list(S.ncs_overlays.values()) + list(S.user_overlays)
    ghost_colors = ["#8E9BA6", "#A88BC0", "#8BAF9B", "#C0A88B"]
    for i, gname in enumerate(S.get("ghost_cases", []) or []):
        case = next((c_ for c_ in S.get("cases", []) if c_["name"] == gname), None)
        if not case:
            continue
        try:
            g_lay = tb_cases.restore(case)[1]
            fc = tb_import.layout_to_geojson(g_lay)
            fc.update(title=f"Concept: {gname}", color=ghost_colors[i % len(ghost_colors)],
                      geometry="line", rev=f"ghost:{gname}")
            overlays.append(fc)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Concept '{gname}' could not be drawn: {exc}")

    tag_inc, tag_exc = S.get("tag_include", []), S.get("tag_exclude", [])
    visible = set(LAY.by_tags(tag_inc, tag_exc)) if (tag_inc or tag_exc) else None
    if visible is not None:
        st.caption(f"Tag filter active — showing {len(visible)} of {len(LAY.nodes) + len(LAY.edges)} items")
    payload = tb_map.build_payload(LAY, findings, S.display, visible)
    event = tb_map.render_map(payload, tb_map.build_palette(CAT), overlays, S.map_state.get("selected"),
                              height=640, fit_token=S.fit_token, rasters=S.get("grid_rasters", []))
    if tb_map.apply_display_event(S.display, event):
        tb_map.apply_event(LAY, event, S.map_state)      # consume it so it is not re-applied
        bump()
        st.rerun()
    pre_event = LAY.to_dict() if event else None
    result = tb_map.apply_event(LAY, event, S.map_state)
    if result["changed"] and pre_event is not None:
        S.setdefault("undo", []).append((str(event.get("type", "map edit")), pre_event))
        del S.undo[:-UNDO_DEPTH]
        S.redo = []
    if result["error"]:
        st.toast(result["error"], icon="⚠️")
    if result["changed"]:
        new_id = result.get("selected")
        if (result["message"].startswith("Added") and new_id in LAY.nodes
                and LAY.kind(new_id) == "well" and S.get("auto_land", True)):
            near = LAY.nearest_structure(new_id, max_m=250.0)
            if near:
                LAY.assign_to_structure([new_id], near)
                st.toast(f"{new_id} landed in {LAY.nodes[near].label or near}", icon="⚓")
        bump()
        st.rerun()

    sel = S.map_state.get("selected")
    left, right = st.columns([1.1, 1])

    with left:
        st.markdown("#### Selected item")
        if not sel or (sel not in LAY.nodes and sel not in LAY.edges):
            st.caption("Click equipment or a line on the map to edit its properties.")
        elif sel in LAY.nodes:
            node = LAY.nodes[sel]
            it = CAT.get(node.item_id)
            same = [i.item_id for i in CAT.by_category(it.category)]
            with st.form(f"node_form_{sel}_{REV}"):
                st.caption(f"{sel} · {it.category.replace('_', ' ')}")
                label = st.text_input("Label", node.label or sel)
                item_id = st.selectbox("Equipment", same, index=same.index(node.item_id),
                                       format_func=lambda i: CAT.get(i).name)
                coord_mode = st.radio("Coordinates", ["Lat/lon", "UTM"], horizontal=True)
                if coord_mode == "Lat/lon":
                    cc = st.columns(2)
                    lat = cc[0].number_input(f"Latitude ({LAY.settings.datum})", -90.0, 90.0, float(node.lat), format="%.6f")
                    lon = cc[1].number_input(f"Longitude ({LAY.settings.datum})", -180.0, 180.0, float(node.lon), format="%.6f")
                    utm_vals = None
                else:
                    e0, n0, z0, _ = tb_geo.geo_to_utm(node.lat, node.lon, datum=LAY.settings.datum)
                    cc = st.columns(3)
                    z = cc[0].number_input("Zone", 1, 60, int(z0))
                    e_ = cc[1].number_input("Easting (m)", 0.0, 1_000_000.0, round(e0, 2), format="%.2f")
                    n_ = cc[2].number_input("Northing (m)", 0.0, 10_000_000.0, round(n0, 2), format="%.2f")
                    utm_vals = (e_, n_, z)
                    lat, lon = node.lat, node.lon
                cc = st.columns(4)
                depth = cc[0].number_input("Water depth (m)", 0.0, 4000.0, float(node.water_depth_m))
                phase = cc[1].number_input("Phase", 1, 9, int(node.phase))
                heading = cc[2].number_input("Heading (° from north)", 0.0, 360.0,
                                             float(node.attrs.get("heading_deg", 0.0) or 0.0), 5.0,
                                             help=f"Rotates the true-scale footprint "
                                                  f"({it.footprint_l_m:.0f} × {it.footprint_w_m:.0f} m), "
                                                  f"visible when zoomed in")
                hipps = cc[3].checkbox("HIPPS", node.hipps, help="Protects everything downstream of this item")
                sitp = st.number_input("Shut-in tubing pressure (psi)", 0.0, 25000.0, float(node.sitp_psi),
                                       step=100.0) if it.category == "well" else node.sitp_psi
                node_tags = st.text_input("Tags (comma separated)", ", ".join(LAY.tags(sel)),
                                          help="Filter the map by tag from the sidebar, e.g. "
                                               "'phase 2, option B, high priority'")
                ok = st.form_submit_button("Apply changes", type="primary")
            if ok:
                if utm_vals:
                    lat, lon = tb_geo.utm_to_geo(utm_vals[0], utm_vals[1], int(utm_vals[2]), "N", LAY.settings.datum)
                record("edit node")
                node.label, node.item_id, node.water_depth_m = label, item_id, depth
                node.attrs["heading_deg"] = float(heading)
                LAY.set_tags(sel, node_tags.split(","))
                node.phase, node.hipps, node.sitp_psi = int(phase), hipps, sitp
                LAY.move_node(sel, float(lat), float(lon))
                bump()
                st.rerun()
        else:
            edge = LAY.edges[sel]
            it = CAT.get(edge.item_id)
            same = [i.item_id for i in CAT.by_category(it.category)]
            with st.form(f"edge_form_{sel}_{REV}"):
                st.caption(f"{sel} · {it.category.replace('_', ' ')} · {edge.from_node} → {edge.to_node}")
                label = st.text_input("Label", edge.label or sel)
                item_id = st.selectbox("Equipment", same, index=same.index(edge.item_id),
                                       format_func=lambda i: CAT.get(i).name)
                cc = st.columns(3)
                diam = cc[0].number_input("Internal diameter (in)", 0.0, 48.0, float(edge.diameter_in), 0.5)
                phase = cc[1].number_input("Phase", 1, 9, int(edge.phase))
                fixed = cc[2].checkbox("Fixed length", edge.length_m is not None)
                calc_len = tb_geo.route_length(LAY.edge_shape(edge), LAY.settings.route_allowance_frac,
                                               LAY.settings.end_allowance_m, LAY.settings.datum)
                length = st.number_input("Design length (m)", 0.0, 500_000.0,
                                         float(edge.length_m if edge.length_m is not None else round(calc_len, 1)),
                                         disabled=False)
                route_df = pd.DataFrame(edge.route, columns=["lat", "lon"]) if edge.route else \
                    pd.DataFrame({"lat": pd.Series(dtype=float), "lon": pd.Series(dtype=float)})
                st.caption("Route bends (lat/lon, from start to end)")
                route_new = st.data_editor(route_df, num_rows="dynamic", key=f"route_{sel}_{REV}", **STRETCH)
                others = [e2.edge_id for e2 in LAY.edges.values() if e2.edge_id != sel and e2.route]
                fluids = list(tb_map.FLUIDS)
                cur_fluid = tb_map.fluid_of(LAY, edge)
                fluid = st.selectbox("Fluid / service", fluids, index=fluids.index(cur_fluid),
                                     key=f"fluid_{sel}_{REV}",
                                     help="Used when the map colours lines by fluid, and in the reports")
                carriers = ["—"] + [e2.edge_id for e2 in LAY.edges.values()
                                    if e2.edge_id != sel and CAT.get(e2.item_id).category in ("flowline", "riser")]
                cur_carrier = edge.attrs.get("piggyback_on") or "—"
                piggy = st.selectbox("Strapped to (piggyback)", carriers,
                                     index=carriers.index(cur_carrier) if cur_carrier in carriers else 0,
                                     key=f"piggy_{sel}_{REV}",
                                     help="Laid with that line instead of its own campaign: it follows the "
                                          "carrier's route and only adds a share of the lay time")
                edge_tags = st.text_input("Tags (comma separated)", ", ".join(LAY.tags(sel)),
                                          key=f"etags_{sel}_{REV}")
                smooth = st.checkbox("Smooth the route (as-laid curve through the bends)",
                                     bool(edge.attrs.get("smooth")),
                                     help="Rigid lines are laid in curves, not sharp corners. Length and "
                                          "flow assurance use the smoothed geometry; bends stay editable.")
                copy_from = st.selectbox("Copy route from", ["—"] + others, key=f"cproute_{sel}_{REV}",
                                         help="Run this line alongside an existing one (e.g. a chemical "
                                              "line following the flowline)")
                ok = st.form_submit_button("Apply changes", type="primary")
            if ok:
                record("edit line")
                edge.label, edge.item_id, edge.diameter_in, edge.phase = label, item_id, diam, int(phase)
                edge.attrs["smooth"] = bool(smooth)
                edge.attrs["fluid"] = fluid
                LAY.set_tags(sel, edge_tags.split(","))
                if piggy == "—":
                    edge.attrs.pop("piggyback_on", None)
                else:
                    edge.attrs["piggyback_on"] = piggy
                edge.length_m = float(length) if fixed else None
                if copy_from != "—":
                    src = LAY.edges[copy_from]
                    rt = list(src.route)
                    if (src.from_node, src.to_node) == (edge.to_node, edge.from_node):
                        rt = rt[::-1]      # same corridor, opposite direction
                    edge.route = [tuple(v) for v in rt]
                else:
                    rn = route_new.dropna()
                    edge.route = [(float(a), float(b)) for a, b in zip(rn["lat"], rn["lon"])]
                bump()
                st.rerun()
            shape_n = len(LAY.edge_shape(edge))
            r_min = tb_geo.min_bend_radius(LAY.edge_shape(edge))
            st.caption(f"Computed design length {calc_len:,.0f} m (geodesic × {1 + LAY.settings.route_allowance_frac:.2f}"
                       f" + {LAY.settings.end_allowance_m:.0f} m)"
                       + (f" · smoothed through {shape_n} points" if edge.attrs.get("smooth") else "")
                       + (f" · tightest bend {r_min:,.0f} m" if r_min != float("inf") else ""))

    with right:
        st.markdown("#### Design checks")
        if findings:
            fdf = pd.DataFrame([dict(Severity=f.severity, Item=f.element_id, Check=f.message) for f in findings])
            order = {"error": 0, "warning": 1, "info": 2}
            fdf = fdf.sort_values("Severity", key=lambda s: s.map(order))
            st.dataframe(fdf, hide_index=True, height=300, **STRETCH)
        else:
            st.success("No design check findings.")

    if S.map_state.get("picked"):
        cc = st.columns([2, 1, 1])
        pk = S.map_state["picked"]
        cc[0].caption(f"Picked point {pk[0]:.5f}°N, {pk[1]:.5f}°E — place a template here from the sidebar, "
                      f"or move the whole layout to it.")
        if cc[1].button("Move layout here"):
            LAY.place_at(float(pk[0]), float(pk[1]))
            bump()
            S.fit_token += 1
            st.rerun()
        if sel in LAY.nodes and cc[2].button(f"Move {sel} here"):
            LAY.move_node(sel, float(pk[0]), float(pk[1]))
            bump()
            st.rerun()

    with st.expander("Wells in template slots"):
        tmpls = [nid for nid in LAY.nodes if LAY.kind(nid) in ("template", "manifold")]
        loose = [nid for nid in LAY.nodes if LAY.kind(nid) == "well"]
        if not tmpls or not loose:
            st.caption("Add a template or manifold and some wells, then land the wells in its slots here "
                       "instead of drawing a connection for each one.")
        else:
            cc = st.columns([1.4, 2, 1])
            tgt = cc[0].selectbox("Structure", tmpls, key=f"slot_t_{REV}",
                                  format_func=lambda i: f"{LAY.nodes[i].label or i} "
                                                        f"({LAY.free_slots(i)} free of {CAT.get(LAY.nodes[i].item_id).slots})")
            # a well recorded in this structure, or already tied to it, is in a slot
            tied = {e.to_node if e.from_node == tgt else e.from_node for e in LAY.edges.values()
                    if tgt in (e.from_node, e.to_node) and CAT.get(e.item_id).category == "jumper"}
            already = [w for w in loose
                       if LAY.nodes[w].attrs.get("in_structure") == tgt or w in tied]
            nearby = [w for w in loose
                      if w not in already and not LAY.nodes[w].attrs.get("in_structure")
                      and tb_geo.geodesic_distance(LAY.nodes[w].lat, LAY.nodes[w].lon,
                                                   LAY.nodes[tgt].lat, LAY.nodes[tgt].lon,
                                                   LAY.settings.datum) <= 250.0]
            pre = already + nearby[:max(LAY.free_slots(tgt), 0)]
            chosen_wells = cc[1].multiselect("Wells", loose, default=pre, key=f"slot_w_{REV}",
                                             format_func=lambda i: LAY.nodes[i].label or i)
            if cc[2].button("Land in slots", type="primary"):
                record("land wells in slots")
                try:
                    made = LAY.assign_to_structure(chosen_wells, tgt)
                except (KeyError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.success(f"{len(chosen_wells)} well(s) in {tgt}"
                               + (f", {len(made)} tie-in(s) created" if made else ""))
                    bump()
                    st.rerun()
            released = [w for w in already if w not in chosen_wells]
            if released and st.button(f"Release {len(released)} well(s) from {tgt}"):
                record("release wells")
                LAY.release_from_structure(released)
                bump()
                st.rerun()
            st.caption("Wells within 250 m of the structure are pre-selected. A well landed in a slot gets "
                       "the integral slot tie-in, not a fabricated spool, and travels with the structure "
                       "when you drag it.")

    with st.expander("Tie-in screening — where should this structure connect?"):
        order_kind = {"template": 0, "manifold": 1, "boosting": 2, "plem": 3, "plet": 4}
        structures = sorted([nid for nid in LAY.nodes if LAY.kind(nid) in order_kind],
                            key=lambda nid: (order_kind[LAY.kind(nid)], nid))
        if not structures:
            st.caption("Place a template or manifold first, then screen it against nearby hosts.")
        else:
            cc = st.columns([2, 1, 1, 1])
            src_node = cc[0].selectbox("Structure", structures, key=f"ti_node_{REV}",
                                       format_func=lambda i: LAY.nodes[i].label or i)
            ti_dia = cc[1].number_input("Line ID (in)", 2.0, 36.0, 10.0, 1.0, key=f"ti_dia_{REV}")
            ti_max = cc[2].number_input("Search radius (km)", 5.0, 200.0, 60.0, 5.0, key=f"ti_max_{REV}")
            ti_surface = cc[3].checkbox("Surface only", True, key=f"ti_surf_{REV}",
                                        help="Drop subsea structures registered as facilities")
            cc = st.columns([1, 1, 2])
            ti_active = cc[0].checkbox("In operation only", True, key=f"ti_act_{REV}",
                                       help="Sodir keeps shut-down and abandoned structures in the "
                                            "'in place' layer until they are physically removed")
            ti_fixed = cc[1].checkbox("Fixed installations only", True, key=f"ti_fix_{REV}",
                                      help="Excludes rigs and other mobile units")
            fac_layers = [k for k in ("facilities", "facilities_all") if k in S.ncs_overlays]
            raw = []
            for k in fac_layers:
                raw += tb_tiein.candidates_from_overlay(S.ncs_overlays[k], ti_surface,
                                                        active_only=ti_active, fixed_only=ti_fixed)
            raw += tb_tiein.candidates_from_layout(LAY)
            cands = tb_tiein.dedupe(raw)
            if fac_layers and len(raw) != len(cands):
                cc[2].caption(f"{len(raw) - len(cands)} duplicate facility record(s) merged across layers")
            if not cands:
                st.info("No candidate hosts yet. Load the Sodir facility layers in the sidebar (or add a "
                        "host to the layout) and screen again.")
            elif st.button("Screen tie-in options", type="primary"):
                ts = tb_tiein.TieInSettings(diameter_in=ti_dia, max_distance_km=ti_max)
                with st.spinner(f"Screening {len(cands)} candidate host(s)…"):
                    S.tiein_rows = tb_tiein.screen(LAY, src_node, cands, ts, S.fa_settings, S.cost_settings,
                                                   tb_fa.well_inputs(LAY))
                    S.tiein_node, S.tiein_settings = src_node, ts
            if S.get("tiein_rows") is not None and S.get("tiein_node") == src_node:
                rows = S.tiein_rows
                if not rows:
                    st.warning(f"No host within {ti_max:.0f} km of {src_node}.")
                else:
                    tdf = pd.DataFrame(rows).drop(columns=["lat", "lon"], errors="ignore")
                    st.dataframe(tdf, hide_index=True, **STRETCH, column_config={
                        c_: st.column_config.NumberColumn(format="%.1f") for c_ in tdf.columns
                        if tdf[c_].dtype.kind == "f"})
                    best = rows[0]
                    st.success(f"Nearest: {best['host']} at {best['distance_km']:.1f} km "
                               f"({best['bearing_deg']:.0f}° true) — line {best['line_length_km']:.1f} km, "
                               + (f"{best.get('required_whp_bara', float('nan')):.0f} bara needed at the wellhead, "
                                  f"arrival {best.get('arrival_t_c', float('nan')):.0f} °C"
                                  if "required_whp_bara" in best else "cost only"))
                    cc = st.columns([2, 1])
                    chosen = cc[0].selectbox("Build a tie-back to", [r["host"] for r in rows],
                                             key=f"ti_pick_{REV}")
                    if cc[1].button("Add to layout"):
                        cand = next(c_ for c_ in cands if c_.name == chosen)
                        try:
                            added = tb_tiein.attach_to_layout(LAY, src_node, cand, S.tiein_settings)
                        except Exception as exc:  # noqa: BLE001
                            st.error(str(exc))
                        else:
                            S.tiein_rows = None
                            bump()
                            S.fit_token += 1
                            st.rerun()

    with st.expander("Add equipment by coordinates"):
        with st.form(f"add_coord_{REV}"):
            node_items = [i.item_id for i in CAT.items.values() if i.category in tb_catalog.NODE_KINDS]
            cc = st.columns([2, 1, 1, 1])
            a_item = cc[0].selectbox("Equipment", node_items, format_func=lambda i: CAT.get(i).name)
            a_mode = cc[1].selectbox("Coordinate type", ["Lat/lon", "UTM"])
            a_x = cc[2].number_input("Lon / easting", value=2.6, format="%.6f")
            a_y = cc[3].number_input("Lat / northing", value=60.5, format="%.6f")
            a_zone = st.number_input("UTM zone (if UTM)", 1, 60, 31)
            if st.form_submit_button("Add"):
                try:
                    if a_mode == "UTM":
                        la, lo = tb_geo.utm_to_geo(a_x, a_y, int(a_zone), "N", LAY.settings.datum)
                    else:
                        la, lo = a_y, a_x
                    it = CAT.get(a_item)
                    nid = tb_map.next_id(tb_map.ID_PREFIX.get(it.category, "N"), list(LAY.nodes) + list(LAY.edges))
                    LAY.add_node(tb_network.Node(nid, a_item, float(la), float(lo), label=nid))
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
                else:
                    S.map_state["selected"] = nid
                    bump()
                    S.fit_token += 1
                    st.rerun()

    with st.expander("All equipment (bulk edit)"):
        node_rows = [dict(id=n.node_id, label=n.label, item_id=n.item_id, lat=n.lat, lon=n.lon,
                          water_depth_m=n.water_depth_m, sitp_psi=n.sitp_psi, hipps=n.hipps, phase=n.phase)
                     for n in LAY.nodes.values()]
        ndf = pd.DataFrame(node_rows, columns=["id", "label", "item_id", "lat", "lon", "water_depth_m",
                                               "sitp_psi", "hipps", "phase"])
        node_ids = [i.item_id for i in CAT.items.values() if i.category in tb_catalog.NODE_KINDS]
        ned = st.data_editor(ndf, hide_index=True, key=f"nodes_ed_{REV}", **STRETCH, column_config={
            "id": st.column_config.TextColumn(disabled=True),
            "item_id": st.column_config.SelectboxColumn("item", options=node_ids, required=True),
            "lat": st.column_config.NumberColumn(format="%.6f"), "lon": st.column_config.NumberColumn(format="%.6f"),
            "phase": st.column_config.NumberColumn(min_value=1, max_value=9, step=1)})
        edge_rows = [dict(id=e.edge_id, label=e.label, item_id=e.item_id, source=e.from_node, target=e.to_node,
                          diameter_in=e.diameter_in, fixed_length_m=e.length_m, phase=e.phase,
                          design_length_m=round(LAY.edge_length(e), 1)) for e in LAY.edges.values()]
        edf = pd.DataFrame(edge_rows, columns=["id", "label", "item_id", "source", "target", "diameter_in",
                                               "fixed_length_m", "phase", "design_length_m"])
        edge_ids = [i.item_id for i in CAT.items.values() if i.category in tb_catalog.EDGE_KINDS]
        eed = st.data_editor(edf, hide_index=True, key=f"edges_ed_{REV}", **STRETCH, column_config={
            "id": st.column_config.TextColumn(disabled=True), "source": st.column_config.TextColumn(disabled=True),
            "target": st.column_config.TextColumn(disabled=True),
            "design_length_m": st.column_config.NumberColumn(disabled=True, format="%.0f"),
            "item_id": st.column_config.SelectboxColumn("item", options=edge_ids, required=True)})
        if st.button("Apply table edits", type="primary"):
            record("bulk edit")
            try:
                for r in ned.to_dict("records"):
                    nd = LAY.nodes[r["id"]]
                    new_cat = CAT.get(r["item_id"]).category
                    if new_cat != CAT.get(nd.item_id).category:
                        raise ValueError(f"{r['id']}: cannot change equipment category ({new_cat})")
                    nd.label, nd.item_id = str(r["label"] or r["id"]), r["item_id"]
                    nd.water_depth_m, nd.sitp_psi = float(r["water_depth_m"] or 0), float(r["sitp_psi"] or 0)
                    nd.hipps, nd.phase = bool(r["hipps"]), int(r["phase"] or 1)
                    LAY.move_node(r["id"], float(r["lat"]), float(r["lon"]))
                for r in eed.to_dict("records"):
                    ed = LAY.edges[r["id"]]
                    new_cat = CAT.get(r["item_id"]).category
                    if new_cat != CAT.get(ed.item_id).category:
                        raise ValueError(f"{r['id']}: cannot change connection category ({new_cat})")
                    ed.label, ed.item_id = str(r["label"] or r["id"]), r["item_id"]
                    ed.diameter_in, ed.phase = float(r["diameter_in"] or 0), int(r["phase"] or 1)
                    fl = r["fixed_length_m"]
                    ed.length_m = None if fl is None or (isinstance(fl, float) and np.isnan(fl)) else float(fl)
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
            else:
                bump()
                st.rerun()

# ═══════════════════════════ CATALOG ═══════════════════════════
with tab_cat:
    st.markdown('<div class="tb-flag">Default rates are indicative placeholders for demonstration. '
                'Replace them with a project cost library before using results.</div>', unsafe_allow_html=True)
    st.write("")
    items_df = pd.DataFrame([{**{k: v for k, v in vars(i).items() if k != "uncertainty"},
                              "unc_low": i.uncertainty[0], "unc_ml": i.uncertainty[1], "unc_high": i.uncertainty[2]}
                             for i in CAT.items.values()])
    spreads_df = pd.DataFrame([{**{k: v for k, v in vars(s_).items() if k != "uncertainty"},
                                "unc_low": s_.uncertainty[0], "unc_ml": s_.uncertainty[1],
                                "unc_high": s_.uncertainty[2]} for s_ in CAT.spreads.values()])
    st.markdown("#### Equipment items")
    st.caption("Cost basis: unit · per_m · per_inch_m (× metres × inch ID). Install days: per unit, or per km for "
               "linear items. Uncertainty: triangular multipliers.")
    items_new = st.data_editor(items_df, num_rows="dynamic", key=f"cat_items_{REV}", hide_index=True, **STRETCH,
                               column_config={
                                   "category": st.column_config.SelectboxColumn(
                                       options=list(tb_catalog.NODE_KINDS + tb_catalog.EDGE_KINDS), required=True),
                                   "cost_basis": st.column_config.SelectboxColumn(options=list(tb_catalog.COST_BASIS)),
                                   "install_spread": st.column_config.SelectboxColumn(options=list(CAT.spreads)),
                                   "procurement_usd": st.column_config.NumberColumn(format="%.0f"),
                                   "fabrication_usd": st.column_config.NumberColumn(format="%.0f")})
    st.markdown("#### Vessel spreads")
    spreads_new = st.data_editor(spreads_df, num_rows="dynamic", key=f"cat_spreads_{REV}", hide_index=True, **STRETCH)

    def _rows_to_catalog(idf, sdf) -> tb_catalog.Catalog:
        def unc(r):
            return (float(r.pop("unc_low")), float(r.pop("unc_ml")), float(r.pop("unc_high")))
        spreads = []
        spread_fields = {f.name for f in dataclasses.fields(tb_catalog.VesselSpread)} - {"uncertainty"}
        for r in sdf.dropna(subset=["key"]).to_dict("records"):
            u = unc(r)
            vals = {k: r[k] for k in spread_fields if k in r}
            for k, v in list(vals.items()):
                if k != "key" and k != "name" and (v is None or (isinstance(v, float) and np.isnan(v))):
                    vals[k] = 0.0
            spreads.append(tb_catalog.VesselSpread(**vals, uncertainty=u))
        items = []
        num = {"procurement_usd", "fabrication_usd", "engineering_frac", "install_days", "lead_time_months",
               "rating_psi", "max_diameter_in", "min_diameter_in", "weight_te"}
        for r in idf.dropna(subset=["item_id"]).to_dict("records"):
            u = unc(r)
            r = {k: (0.0 if (k in num and (v is None or (isinstance(v, float) and np.isnan(v)))) else v)
                 for k, v in r.items()}
            r["slots"] = int(r.get("slots") or 0)
            r["notes"] = "" if r.get("notes") is None or (isinstance(r.get("notes"), float)) else r["notes"]
            items.append(tb_catalog.CatalogItem(**r, uncertainty=u))
        return tb_catalog.Catalog(items, spreads)

    def _adopt_catalog(new_cat: tb_catalog.Catalog):
        missing = {x.item_id for x in list(LAY.nodes.values()) + list(LAY.edges.values())} - set(new_cat.items)
        if missing:
            raise ValueError(f"layout uses items not in the new catalog: {sorted(missing)}")
        for el in list(LAY.nodes.values()) + list(LAY.edges.values()):
            if new_cat.get(el.item_id).category != CAT.get(el.item_id).category:
                raise ValueError(f"{el.item_id} changed category but is used in the layout")
        LAY.catalog = new_cat
        S.mc = None
        bump()

    cc = st.columns([1, 1, 2])
    if cc[0].button("Apply catalog changes", type="primary"):
        try:
            _adopt_catalog(_rows_to_catalog(items_new, spreads_new))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Catalog not applied: {type(exc).__name__}: {exc}")
        else:
            st.rerun()
    cc[1].download_button("Download cost library", CAT.to_yaml(), file_name="tieback_cost_library.yaml",
                          mime="text/yaml")
    lib = cc[2].file_uploader("Load cost library (.yaml, .xlsx or .csv)",
                              type=["yaml", "yml", "xlsx", "xlsm", "csv"], key="lib_upload")
    st.download_button("Download cost catalog as Excel", tb_costio.catalog_to_workbook(CAT),
                       file_name="tieback_cost_catalog.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       help="Sheets: items, spreads, notes. Edit the rates and load it straight back.")
    spreads_csv = None
    if lib is not None and lib.name.lower().endswith(".csv"):
        spreads_csv = st.file_uploader("Vessel spreads CSV (optional)", type=["csv"], key="lib_spreads_upload")
    if lib is not None and st.button("Replace catalog with this file"):
        name = lib.name.lower()
        try:
            if name.endswith((".xlsx", ".xlsm")):
                new_cat = tb_costio.catalog_from_workbook(lib.getvalue())
            elif name.endswith(".csv"):
                new_cat = tb_costio.catalog_from_csv(lib.getvalue(),
                                                     spreads_csv.getvalue() if spreads_csv else None)
            else:
                new_cat = tb_catalog.Catalog.from_yaml(lib.getvalue().decode("utf-8-sig"))
            _adopt_catalog(new_cat)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Catalog not loaded: {exc}")
        else:
            S.catalog_source = lib.name
            st.success(f"Loaded {len(CAT.items)} items and {len(CAT.spreads)} vessel spreads from {lib.name}")
            st.rerun()

# ═══════════════════════════ SCHEDULE (computed first; cost phasing needs it) ═══════════════════════════
sched_error = None
try:
    SCH, EMAP = tb_schedule.build_from_layout(LAY, S.sched_settings)
except ValueError as exc:
    SCH, EMAP, sched_error = None, {}, str(exc)

EST = tb_cost.estimate(LAY, S.cost_settings)

# ═══════════════════════════════ COST ═══════════════════════════════
with tab_cost:
    cs = S.cost_settings
    with st.form(f"cost_settings_{REV}"):
        cc = st.columns(3)
        survey = cc[0].number_input("Survey & pre-commissioning (% of installation)", 0.0, 50.0,
                                    float(cs.survey_precomm_frac) * 100, 0.5)
        owners = cc[1].number_input("Owner's costs (%)", 0.0, 30.0, float(cs.owners_cost_frac) * 100, 0.5)
        cont = cc[2].number_input("Contingency (%)", 0.0, 60.0, float(cs.contingency_frac) * 100, 1.0)
        if st.form_submit_button("Apply cost settings"):
            cs.survey_precomm_frac, cs.owners_cost_frac, cs.contingency_frac = survey / 100, owners / 100, cont / 100
            S.mc = None
            bump()
            st.rerun()

    f = money_factor()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Base estimate", money(EST["base_usd"]))
    k2.metric(f"With contingency ({cs.contingency_frac:.0%})", money(EST["total_usd"]))
    mc_sig = md5(LAY.to_dict(), CAT.to_dict(), vars(cs))
    mc = S.get("mc")
    if mc and mc.get("sig") == mc_sig:
        k3.metric("P50 (excl. contingency)", money(mc["P50"]))
        k4.metric("P10 – P90", f"{money(mc['P10'])} – {money(mc['P90'])}")
    else:
        k3.metric("P50 (excl. contingency)", "—")
        k4.metric("P10 – P90", "Run simulation")

    cc = st.columns([1.2, 1])
    with cc[0]:
        bc = pd.DataFrame({"Category": list(EST["by_category"]), "Value": [v * f for v in EST["by_category"].values()]})
        fig = px.bar(bc, x="Value", y="Category", orientation="h", color_discrete_sequence=[EQ["navy"]])
        fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(tb_cost.CATEGORIES)), title=None)
        fig.update_xaxes(title=S.currency)
        st.plotly_chart(eq_layout(fig, 340, title="CAPEX by cost category"), **STRETCH)
    with cc[1]:
        lines = pd.DataFrame(EST["lines"])
        if len(lines):
            by_cat = lines.groupby("category")["direct"].sum().sort_values(ascending=False) * f
            fig = px.bar(by_cat.reset_index(), x="category", y="direct", color_discrete_sequence=[EQ["teal"]])
            fig.update_xaxes(title=None)
            fig.update_yaxes(title=S.currency)
            st.plotly_chart(eq_layout(fig, 340, title="Direct cost by equipment type"), **STRETCH)

    st.markdown("#### Cost phasing")
    if SCH is None:
        st.warning(f"Schedule could not be built, so CAPEX cannot be phased: {sched_error}")
    else:
        PH = tb_cost.phase_costs(EST, SCH, EMAP, cs)
        ann = pd.DataFrame({"Year": [str(y) for y in PH["annual"]], "CAPEX": [v * f for v in PH["annual"].values()]})
        fig = px.bar(ann, x="Year", y="CAPEX", color_discrete_sequence=[EQ["navy"]])
        fig.update_yaxes(title=S.currency)
        st.plotly_chart(eq_layout(fig, 300, title="Annual CAPEX profile (incl. contingency)"), **STRETCH)

    st.markdown("#### Probabilistic estimate")
    cc = st.columns([1, 1, 3])
    n_iter = cc[0].selectbox("Iterations", [2000, 5000, 10000, 20000], index=1)
    seed = cc[1].number_input("Seed", 0, 10_000, 42)
    if cc[2].button("Run simulation", type="primary"):
        res = tb_cost.monte_carlo(LAY, cs, n=int(n_iter), seed=int(seed))
        res["sig"] = mc_sig
        S.mc = res
        st.rerun()
    if mc and mc.get("sig") == mc_sig:
        samples = np.asarray(mc["samples"]) * f
        fig = go.Figure(go.Histogram(x=samples, nbinsx=60, marker_color=EQ["slate"], opacity=0.85))
        for lbl, col in (("P10", EQ["pistachio"]), ("P50", EQ["navy"]), ("P90", EQ["torch"])):
            fig.add_vline(x=mc[lbl] * f, line_color=col, line_width=2, annotation_text=lbl)
        fig.add_vline(x=EST["total_usd"] * f, line_dash="dash", line_color=EQ["amber"],
                      annotation_text="Deterministic + contingency")
        fig.update_xaxes(title=S.currency)
        st.plotly_chart(eq_layout(fig, 320, title="Base cost distribution (P10 low, P90 high)"), **STRETCH)
    elif mc:
        st.info("Layout or costs changed since the last simulation — run it again.")

    with st.expander("Cost by item, with factors and overrides"):
        if len(lines):
            view_df = lines[["element_id", "label", "item", "category", "quantity_basis", "offshore_days",
                             "procurement", "fabrication", "engineering", "installation", "direct"]].copy()
            for col in ("procurement", "fabrication", "engineering", "installation", "direct"):
                view_df[col] = view_df[col] * f
            view_df["factor"] = [cs.element_factor.get(e, 1.0) for e in view_df["element_id"]]
            view_df["override"] = [cs.element_override_usd.get(e, np.nan) * f if e in cs.element_override_usd
                                   else np.nan for e in view_df["element_id"]]
            ed = st.data_editor(view_df, hide_index=True, key=f"cost_lines_{REV}", **STRETCH,
                                disabled=[c for c in view_df.columns if c not in ("factor", "override")],
                                column_config={c: st.column_config.NumberColumn(format="%.2f")
                                               for c in ("procurement", "fabrication", "engineering",
                                                         "installation", "direct", "override")})
            if st.button("Apply factors and overrides"):
                cs.element_factor = {r["element_id"]: float(r["factor"]) for r in ed.to_dict("records")
                                     if r["factor"] is not None and not np.isnan(r["factor"]) and float(r["factor"]) != 1.0}
                cs.element_override_usd = {r["element_id"]: float(r["override"]) / f for r in ed.to_dict("records")
                                           if r["override"] is not None and not np.isnan(r["override"])}
                S.mc = None
                bump()
                st.rerun()
            st.download_button("Download cost lines (CSV)", lines.to_csv(index=False), "tieback_cost_lines.csv")

# ═══════════════════════════════ SCHEDULE ═══════════════════════════════
with tab_sched:
    ssn = S.sched_settings
    with st.form(f"sched_form_{REV}"):
        cc = st.columns(4)
        dg2 = cc[0].date_input("DG2 date", ssn.dg2_date)
        feed = cc[1].number_input("FEED to DG3 (days)", 0.0, 1500.0, float(ssn.feed_days), 10.0)
        pdo = cc[2].number_input("PDO approval (days)", 0.0, 720.0, float(ssn.pdo_approval_days), 10.0)
        award = cc[3].number_input("Award after DG3 (days)", -365.0, 720.0, float(ssn.award_after_dg3_days), 10.0)
        cc = st.columns(4)
        drill = cc[0].number_input("Drill & complete per well (days)", 1.0, 400.0, float(ssn.drill_days_per_well), 5.0)
        rigs = cc[1].number_input("Rigs", 1, 6, int(ssn.rigs))
        precomm = cc[2].number_input("Pre-commissioning (days)", 0.0, 365.0, float(ssn.precommissioning_days), 5.0)
        comm = cc[3].number_input("Commissioning (days)", 0.0, 365.0, float(ssn.commissioning_days), 5.0)
        cc = st.columns(4)
        w0 = cc[0].date_input("Marine season opens", dt.date(2027, *ssn.marine_window.start_mmdd))
        w1 = cc[1].date_input("Marine season closes", dt.date(2027, *ssn.marine_window.end_mmdd))
        metro = cc[2].number_input("Metrology (days)", 0.0, 120.0, float(ssn.metrology_days), 1.0)
        phases = sorted({n.phase for n in LAY.nodes.values()} | {e.phase for e in LAY.edges.values()}) or [1]
        off_df = pd.DataFrame({"phase": phases, "award_delay_days": [ssn.phase_offset_days.get(p, 0.0) for p in phases]})
        off_new = cc[3].data_editor(off_df, hide_index=True, key=f"phase_off_{REV}",
                                    disabled=["phase"])
        if st.form_submit_button("Apply schedule settings", type="primary"):
            ssn.dg2_date, ssn.feed_days, ssn.pdo_approval_days, ssn.award_after_dg3_days = dg2, feed, pdo, award
            ssn.drill_days_per_well, ssn.rigs = drill, int(rigs)
            ssn.precommissioning_days, ssn.commissioning_days, ssn.metrology_days = precomm, comm, metro
            ssn.marine_window = tb_schedule.Window((w0.month, w0.day), (w1.month, w1.day))
            ssn.phase_offset_days = {int(r["phase"]): float(r["award_delay_days"] or 0)
                                     for r in off_new.to_dict("records")}
            bump()
            st.rerun()

    if SCH is None:
        st.error(f"Schedule could not be built: {sched_error}")
    else:
        acts = SCH.activities
        fo = sorted([a for a in acts.values() if a.act_id.endswith("_FIRST_OIL")], key=lambda a: a.es)
        k = st.columns(4)
        k[0].metric("DG3 / PDO submitted", acts["DG3"].es.strftime("%b %Y"))
        k[1].metric("PDO approved", acts["PDO_OK"].es.strftime("%b %Y"))
        k[2].metric("First production", fo[0].es.strftime("%b %Y") if fo else "—")
        k[3].metric("Last activity ends", SCH.finish.strftime("%b %Y"))

        tbl = pd.DataFrame(SCH.table())
        tbl["row"] = np.where(tbl["critical"], "● " + tbl["name"], tbl["name"])
        bars = tbl[~tbl["milestone"]].copy()
        bars["start"] = pd.to_datetime(bars["start"])
        bars["finish"] = pd.to_datetime(bars["finish"])
        fig = px.timeline(bars, x_start="start", x_end="finish", y="row", color="group",
                          color_discrete_map=GROUP_COLORS, hover_data=["duration_days", "total_float_days", "window"])
        ms = tbl[tbl["milestone"]]
        fig.add_trace(go.Scatter(x=pd.to_datetime(ms["start"]), y=ms["row"], mode="markers", name="Milestone",
                                 marker=dict(symbol="diamond", size=11, color=EQ["torch"])))
        order = list(tbl.sort_values("start")["row"])
        fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(order)), title=None)
        st.plotly_chart(eq_layout(fig, max(380, 22 * len(tbl) + 80), title="Project schedule (CPM)"), **STRETCH)
        st.caption("Marine campaigns are held inside the season window. Float on window-constrained activities is "
                   "indicative (backward pass ignores windows). ● marks activities on the critical path.")
        with st.expander("Activity table"):
            st.dataframe(tbl, hide_index=True, **STRETCH)
            st.download_button("Download schedule (CSV)", tbl.to_csv(index=False), "tieback_schedule.csv")

# ═══════════════════════════ FLOW ASSURANCE ═══════════════════════════
with tab_fa:
    fas = S.fa_settings
    st.caption("Steady-state screening: Beggs & Brill (revised, Payne) multiphase gradient on a black-oil fluid, "
               "analytic pipe heat loss, Towler-Mokhatab hydrates with Hammerschmidt inhibition, lumped cool-down. "
               "Joule-Thomson cooling is not modelled — gas lines with large pressure drop arrive colder than shown.")
    with st.expander("Host, environment and inhibition", expanded=False):
        with st.form(f"fa_settings_{REV}"):
            cc = st.columns(4)
            arr = cc[0].number_input("Host arrival pressure (bara)", 1.0, 500.0, float(fas.arrival_bara), 1.0)
            sbt = cc[1].number_input("Seabed temperature (°C)", -2.0, 30.0, float(fas.seabed_temp_c), 0.5)
            ddep = cc[2].number_input("Default water depth (m)", 1.0, 4000.0, float(fas.default_water_depth_m), 10.0)
            ntt = cc[3].number_input("No-touch time (h)", 0.0, 72.0, float(fas.no_touch_hours), 1.0)
            cc = st.columns(4)
            liqcap = cc[0].number_input("Host liquid capacity (Sm³/d)", 0.0, 1e6, float(fas.host_liquid_capacity_sm3_d), 500.0)
            gascap = cc[1].number_input("Host gas capacity (MSm³/d)", 0.0, 200.0, float(fas.host_gas_capacity_msm3_d), 0.5)
            inh = cc[2].selectbox("Inhibitor", list(tb_fa.th.INHIBITORS), index=list(tb_fa.th.INHIBITORS).index(fas.inhibitor))
            inhw = cc[3].number_input("Inhibitor in water phase (wt %)", 0.0, 80.0, float(fas.inhibitor_wt_pct), 5.0)
            cc = st.columns(4)
            jl = cc[0].number_input("Jumper length (m)", 5.0, 500.0, float(fas.jumper_length_m), 5.0)
            jd = cc[1].number_input("XT jumper ID (in)", 2.0, 16.0, float(fas.well_jumper_diameter_in), 0.5)
            rough = cc[2].number_input("Pipe roughness (mm)", 0.0, 2.0, float(fas.roughness_in) * 25.4, 0.005, format="%.3f")
            segl = cc[3].number_input("Calculation step (m)", 25.0, 2000.0, float(fas.segment_length_m), 25.0)
            cc = st.columns(3)
            jt = cc[0].checkbox("Joule-Thomson cooling", bool(fas.include_jt),
                                help="Expansion cooling along the line; matters most for gas and "
                                     "condensate systems with a large pressure drop")
            npt = cc[1].number_input("Pressure/temperature passes", 1, 6, int(fas.pt_iterations),
                                     help="JT couples the two marches; 3 passes is usually enough")
            slugv = cc[2].number_input("Riser slugging gas velocity (m/s)", 0.5, 10.0,
                                       float(fas.severe_slug_vsg_m_s), 0.5,
                                       help="Below this superficial gas velocity in the riser, severe "
                                            "slugging is flagged")
            cc = st.columns(3)
            usebed = cc[0].checkbox("Follow stored seabed profiles", bool(fas.use_seabed_profile),
                                    help="Use the EMODnet terrain fetched in the sidebar instead of a "
                                         "straight slope between end depths")
            spangap = cc[1].number_input("Free-span clearance (m)", 0.1, 5.0, float(fas.free_span_gap_m), 0.1)
            maxspan = cc[2].number_input("Report spans up to (m)", 50.0, 2000.0, float(fas.max_free_span_m), 50.0)
            if st.form_submit_button("Apply flow assurance settings", type="primary"):
                try:
                    S.fa_settings = tb_fa.FASettings(arrival_bara=arr, seabed_temp_c=sbt, default_water_depth_m=ddep,
                                                     no_touch_hours=ntt, host_liquid_capacity_sm3_d=liqcap,
                                                     host_gas_capacity_msm3_d=gascap, inhibitor=inh,
                                                     inhibitor_wt_pct=inhw, jumper_length_m=jl,
                                                     well_jumper_diameter_in=jd, roughness_in=rough / 25.4,
                                                     segment_length_m=segl, air_temp_c=fas.air_temp_c,
                                                     erosional_c=fas.erosional_c, include_jt=bool(jt),
                                                     pt_iterations=int(npt), severe_slug_vsg_m_s=slugv,
                                                     use_seabed_profile=bool(usebed), free_span_gap_m=spangap,
                                                     max_free_span_m=maxspan)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    bump()
                    st.rerun()

    wells_in = tb_fa.well_inputs(LAY)
    st.markdown("#### Well streams")
    if not wells_in:
        st.info("Add wells to the layout to run flow assurance.")
    else:
        wdf = pd.DataFrame([dict(id=w, label=LAY.nodes[w].label or w, **vars(v)) for w, v in wells_in.items()])
        wed = st.data_editor(wdf, hide_index=True, key=f"fa_wells_{REV}", **STRETCH, column_config={
            "id": st.column_config.TextColumn(disabled=True), "label": st.column_config.TextColumn(disabled=True),
            "oil_sm3_d": st.column_config.NumberColumn("Oil/cond. Sm³/d", min_value=0.0, format="%.0f"),
            "water_cut": st.column_config.NumberColumn("Water cut", min_value=0.0, max_value=0.99, format="%.2f"),
            "gor_sm3_sm3": st.column_config.NumberColumn("GOR Sm³/Sm³", min_value=0.0, format="%.0f"),
            "api": st.column_config.NumberColumn("API", min_value=5.0, max_value=80.0),
            "gas_sg": st.column_config.NumberColumn("Gas SG", min_value=0.55, max_value=1.5, format="%.3f"),
            "salinity_wt_pct": st.column_config.NumberColumn("Salinity wt %", min_value=0.0, max_value=25.0),
            "wht_c": st.column_config.NumberColumn("WHT °C", format="%.1f"),
            "max_whp_bara": st.column_config.NumberColumn("Available WHP bara", min_value=1.0, format="%.1f")})
        if st.button("Apply well inputs"):
            try:
                for r in wed.to_dict("records"):
                    vals = {k: float(r[k]) for k in tb_fa.WellFA.__dataclass_fields__}
                    tb_fa.set_well_inputs(LAY, r["id"], tb_fa.WellFA(**vals))
            except (ValueError, TypeError) as exc:
                st.error(f"Well inputs not applied: {exc}")
            else:
                bump()
                st.rerun()

    fa_edges = [e for e in LAY.edges.values() if CAT.get(e.item_id).category in ("flowline", "riser", "jumper")]
    if fa_edges:
        with st.expander("Pipe heat transfer (U-values)"):
            st.caption("Overall heat-transfer coefficient referenced to the inner diameter. Defaults are screening "
                       "values by pipe type; enter a value to override one line.")
            udf = pd.DataFrame([dict(id=e.edge_id, item=CAT.get(e.item_id).name,
                                     default_u=tb_fa.DEFAULT_U_W_M2K.get(e.item_id, tb_fa.DEFAULT_U_BY_CATEGORY.get(
                                         CAT.get(e.item_id).category, 10.0)),
                                     u_override=e.attrs.get("u_w_m2k")) for e in fa_edges])
            ued = st.data_editor(udf, hide_index=True, key=f"fa_u_{REV}", **STRETCH,
                                 disabled=["id", "item", "default_u"],
                                 column_config={"u_override": st.column_config.NumberColumn(
                                     "U override W/m²K", min_value=0.0, format="%.2f")})
            if st.button("Apply U-values"):
                for r in ued.to_dict("records"):
                    v = r["u_override"]
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        LAY.edges[r["id"]].attrs.pop("u_w_m2k", None)
                    else:
                        LAY.edges[r["id"]].attrs["u_w_m2k"] = float(v)
                bump()
                st.rerun()

    if wells_in:
        FA_RES = tb_fa.solve(LAY, S.fa_settings, wells_in)
        worst = min(FA_RES.wells, key=lambda w: w["margin_bar"]) if FA_RES.wells else None
        min_margin = min((r.min_hydrate_margin_c for r in FA_RES.edges.values() if not r.heated), default=np.nan)
        k = st.columns(4)
        if FA_RES.host:
            h0 = next(iter(FA_RES.host.values()))
            k[0].metric("Host arrival", f"{h0['arrival_bara']:.0f} bara · {h0['arrival_t_c']:.1f} °C")
            k[1].metric("Host intake", f"{h0['liquid_sm3_d']:,.0f} Sm³/d liquid",
                        f"{h0['gas_msm3_d']:.2f} MSm³/d gas", delta_color="off")
        if worst:
            k[2].metric("Tightest wellhead margin", f"{worst['margin_bar']:.1f} bar", worst["label"], delta_color="off")
        k[3].metric("Lowest hydrate margin", f"{min_margin:.1f} °C" if min_margin == min_margin else "—")

        if FA_RES.findings:
            fdf = pd.DataFrame([dict(Severity=a, Item=b, Check=c_) for a, b, c_ in FA_RES.findings])
            fdf = fdf.sort_values("Severity", key=lambda s_: s_.map({"error": 0, "warning": 1, "info": 2}))
            st.dataframe(fdf, hide_index=True, **STRETCH)
        else:
            st.success("All wells deliverable; no hydrate, cool-down, erosion or capacity findings.")

        st.markdown("#### Wells")
        st.dataframe(pd.DataFrame(FA_RES.wells), hide_index=True, **STRETCH, column_config={
            c_: st.column_config.NumberColumn(format="%.1f") for c_ in
            ("required_whp_bara", "available_whp_bara", "margin_bar")})

        st.markdown("#### Lines")
        ldf = pd.DataFrame([dict(line=r.edge_id, flow=f"{r.upstream} → {r.downstream}", length_m=r.length_m,
                                 incl_deg=r.theta_deg, id_in=r.d_in, u_w_m2k=r.u_w_m2k, p_in_bara=r.p_in_bara,
                                 p_out_bara=r.p_out_bara, t_in_c=r.t_in_c, t_out_c=r.t_out_c,
                                 pattern=r.dominant_pattern, max_holdup=r.max_holdup,
                                 max_velocity_m_s=r.max_velocity_m_s, erosional_ratio=r.erosional_ratio,
                                 hydrate_margin_c=r.min_hydrate_margin_c,
                                 cooldown_h=(np.nan if r.heated or math.isinf(r.cooldown_h) else r.cooldown_h),
                                 fluid=tb_map.fluid_of(LAY, LAY.edges[r.edge_id]),
                                 seabed_profile=r.uses_seabed_profile, free_spans=len(r.free_spans))
                            for r in FA_RES.edges.values()])
        st.dataframe(ldf, hide_index=True, **STRETCH, column_config={
            c_: st.column_config.NumberColumn(format="%.1f") for c_ in
            ("length_m", "incl_deg", "p_in_bara", "p_out_bara", "t_in_c", "t_out_c", "hydrate_margin_c", "cooldown_h")})

        st.markdown("#### Profile along a well's path")
        pw = st.selectbox("Well", [w["well"] for w in FA_RES.wells],
                          format_func=lambda w: LAY.nodes[w].label or w, key="fa_profile_well")
        path = LAY.path_to_host(pw) if pw else None
        if path:
            rows, x0 = [], 0.0
            for eid in path[1]:
                er = FA_RES.edges.get(eid)
                if not er:
                    continue
                for pt in er.profile:
                    rows.append(dict(distance_km=(x0 + pt["x_m"]) / 1000, line=eid, p_bara=pt["p_bara"],
                                     t_c=pt["t_c"], hydrate_t_c=pt["t_c"] - pt["hydrate_margin_c"],
                                     holdup=pt["holdup"], velocity_m_s=pt["v_m_m_s"], pattern=pt["pattern"]))
                x0 += er.length_m
            prof = pd.DataFrame(rows)
            if len(prof):
                cc = st.columns(2)
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=prof["distance_km"], y=prof["p_bara"], mode="lines", name="Pressure",
                                         line=dict(color=EQ["navy"], width=3)))
                fig.update_xaxes(title="Distance from wellhead (km)")
                fig.update_yaxes(title="bara")
                cc[0].plotly_chart(eq_layout(fig, 320, title="Pressure"), **STRETCH)
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=prof["distance_km"], y=prof["t_c"], mode="lines", name="Fluid temperature",
                                         line=dict(color=EQ["torch"], width=3)))
                fig.add_trace(go.Scatter(x=prof["distance_km"], y=prof["hydrate_t_c"], mode="lines",
                                         name="Hydrate temperature (inhibited)",
                                         line=dict(color=EQ["slate"], width=2, dash="dash")))
                fig.update_xaxes(title="Distance from wellhead (km)")
                fig.update_yaxes(title="°C")
                cc[1].plotly_chart(eq_layout(fig, 320, title="Temperature vs hydrate curve"), **STRETCH)

        if path:
            sec = pd.DataFrame(tb_fa.path_section(LAY, FA_RES, pw, S.fa_settings))
            if len(sec):
                marks = pd.DataFrame(tb_fa.section_nodes(LAY, FA_RES, pw, S.fa_settings))
                st.markdown("#### Route section (wellhead to host)")
                fig = go.Figure()
                floor = float(sec["seabed_elev_m"].min()) - 40
                fig.add_trace(go.Scatter(x=sec["distance_m"] / 1000, y=sec["seabed_elev_m"], name="Seabed",
                                         mode="lines", line=dict(color="#8C6D4F", width=1),
                                         fill="tozeroy" if False else None))
                fig.add_trace(go.Scatter(x=sec["distance_m"] / 1000, y=[floor] * len(sec), name="Seabed fill",
                                         mode="lines", line=dict(width=0), fill="tonexty",
                                         fillcolor="rgba(140,109,79,0.35)", showlegend=False, hoverinfo="skip"))
                fig.add_trace(go.Scatter(x=sec["distance_m"] / 1000, y=sec["pipe_elev_m"], name="Line / riser",
                                         mode="lines", line=dict(color=EQ["navy"], width=4),
                                         customdata=np.stack([sec["p_bara"], sec["t_c"], sec["line"]], axis=-1),
                                         hovertemplate="%{customdata[2]}<br>%{x:.2f} km<br>%{y:.0f} m<br>"
                                                       "%{customdata[0]:.1f} bara · %{customdata[1]:.1f} °C<extra></extra>"))
                fig.add_trace(go.Scatter(x=marks["distance_m"] / 1000, y=marks["elev_m"], mode="markers+text",
                                         text=marks["label"], textposition="top center", name="Equipment",
                                         marker=dict(symbol="square", size=9, color=EQ["torch"])))
                fig.add_hline(y=0, line_color="#4A90C4", line_width=2)
                fig.update_xaxes(title="Distance from wellhead (km)")
                fig.update_yaxes(title="Elevation (m, sea level = 0)")
                st.plotly_chart(eq_layout(fig, 360, title="Longitudinal section"), **STRETCH)

                xs = st.columns([1, 1])
                sec_lines = list(dict.fromkeys(sec["line"]))
                xl = xs[0].selectbox("Cross-section of", sec_lines, key="fa_xsec_line",
                                     format_func=lambda i: f"{i} · {CAT.get(LAY.edges[i].item_id).name}")
                if xl:
                    er = FA_RES.edges[xl]
                    layers = tb_fa.pipe_cross_section(CAT.get(LAY.edges[xl].item_id), er.d_in)
                    fig = go.Figure()
                    for ly in reversed(layers):
                        r_ = ly["outer_mm"] / 2
                        fig.add_shape(type="circle", x0=-r_, y0=-r_, x1=r_, y1=r_, xref="x", yref="y",
                                      line=dict(color="#FFFFFF", width=1), fillcolor=ly["color"], layer="below")
                    outer = layers[-1]["outer_mm"] / 2
                    fig.add_trace(go.Scatter(x=[0], y=[0], mode="text", text=[f'{er.d_in:g}" ID'],
                                             textfont=dict(color="#00243D", size=13), showlegend=False))
                    for i_, ly in enumerate(layers):
                        fig.add_trace(go.Scatter(x=[outer * 1.15], y=[outer - i_ * outer / max(len(layers), 1) * 0.6],
                                                 mode="markers+text", marker=dict(size=11, color=ly["color"]),
                                                 text=[f"  {ly['name']} — {ly['note']}"], textposition="middle right",
                                                 showlegend=False, hoverinfo="skip"))
                    fig.update_xaxes(visible=False, range=[-outer * 1.2, outer * 3.4])
                    fig.update_yaxes(visible=False, scaleanchor="x", scaleratio=1,
                                     range=[-outer * 1.3, outer * 1.3])
                    xs[0].plotly_chart(eq_layout(fig, 340, title=f"{xl} build-up "
                                                                f"(OD {layers[-1]['outer_mm']:.0f} mm)"), **STRETCH)
                    xs[1].dataframe(pd.DataFrame(layers)[["name", "outer_mm", "note"]], hide_index=True, **STRETCH,
                                    column_config={"outer_mm": st.column_config.NumberColumn("outer Ø (mm)", format="%.0f")})
                    xs[1].caption(f"U = {er.u_w_m2k:.1f} W/m²K · inclination {er.theta_deg:.1f}° · "
                                  f"{er.length_m:,.0f} m · {er.dominant_pattern} flow")

        st.markdown("#### Turndown and ramp-up")
        cc = st.columns([2, 1])
        fr_txt = cc[0].text_input("Rate fractions to check", "1.0, 0.7, 0.5, 0.3", key=f"td_{REV}")
        if cc[1].button("Run turndown check"):
            try:
                fracs = [float(x) for x in fr_txt.replace(";", ",").split(",") if x.strip()]
            except ValueError:
                st.error("Fractions must be numbers, e.g. 1.0, 0.7, 0.5")
            else:
                S.turndown = dict(sig=md5(LAY.to_dict(), vars(S.fa_settings), fr_txt),
                                  rows=tb_fa.rate_sensitivity(LAY, S.fa_settings, wells_in, fracs))
        td = S.get("turndown")
        if td and td["sig"] == md5(LAY.to_dict(), vars(S.fa_settings), fr_txt):
            tdf = pd.DataFrame(td["rows"])
            st.dataframe(tdf, hide_index=True, **STRETCH, column_config={
                c_: st.column_config.NumberColumn(format="%.1f") for c_ in
                ("arrival_t_c", "max_required_whp_bara", "min_hydrate_margin_c", "min_cooldown_h",
                 "liquid_inventory_m3", "surge_vs_design_m3", "max_velocity_m_s")})
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=tdf["oil_sm3_d"], y=tdf["min_hydrate_margin_c"], mode="lines+markers",
                                     name="Hydrate margin (°C)", line=dict(color=EQ["torch"], width=3)))
            fig.add_trace(go.Scatter(x=tdf["oil_sm3_d"], y=tdf["arrival_t_c"], mode="lines+markers",
                                     name="Arrival temperature (°C)", line=dict(color=EQ["navy"], width=3)))
            fig.add_hline(y=0, line_dash="dash", line_color=EQ["slate"])
            fig.update_xaxes(title="Field oil rate (Sm³/d)")
            fig.update_yaxes(title="°C")
            st.plotly_chart(eq_layout(fig, 300, title="Turndown: arrival temperature and hydrate margin"), **STRETCH)
            bad = tdf[tdf["min_hydrate_margin_c"] < 0]
            if len(bad):
                st.warning(f"Hydrate margin is negative at and below {bad['fraction'].max():.0%} of design rate — "
                           f"continuous inhibition or insulation is needed for turndown operation.")

        st.markdown("#### Well deliverability (nodal)")
        st.caption("Give a well an IPR and tubing below, then solve the rates from the intersection of "
                   "inflow, tubing lift and the network back-pressure instead of typing them in.")
        ipr_rows = []
        for w_, v_ in wells_in.items():
            d_ = LAY.nodes[w_].attrs.get("ipr") or {}
            ipr_rows.append(dict(id=w_, label=LAY.nodes[w_].label or w_, use=bool(d_.get("use", False)),
                                 kind=d_.get("kind", "pi"), reservoir_bara=float(d_.get("reservoir_bara", 350.0)),
                                 pi_sm3_d_bar=float(d_.get("pi_sm3_d_bar", 12.0)),
                                 bubble_bara=float(d_.get("bubble_bara", 200.0)),
                                 md_m=float(d_.get("md_m", 2600.0)), tvd_m=float(d_.get("tvd_m", 2500.0)),
                                 tubing_id_in=float(d_.get("tubing_id_in", 4.892)),
                                 reservoir_t_c=float(d_.get("reservoir_t_c", 85.0))))
        idf = pd.DataFrame(ipr_rows)
        ied = st.data_editor(idf, hide_index=True, key=f"fa_ipr_{REV}", **STRETCH,
                             disabled=["id", "label"], column_config={
                                 "kind": st.column_config.SelectboxColumn(options=list(tb_well.IPR_KINDS)),
                                 "reservoir_bara": st.column_config.NumberColumn("Res. P (bara)", format="%.0f"),
                                 "pi_sm3_d_bar": st.column_config.NumberColumn("PI (Sm³/d/bar)", format="%.2f"),
                                 "bubble_bara": st.column_config.NumberColumn("Pb (bara)", format="%.0f"),
                                 "md_m": st.column_config.NumberColumn("Tubing MD (m)", format="%.0f"),
                                 "tvd_m": st.column_config.NumberColumn("TVD (m)", format="%.0f"),
                                 "reservoir_t_c": st.column_config.NumberColumn("Res. T (°C)", format="%.0f")})
        cc = st.columns([1, 1, 2])
        if cc[0].button("Save well IPR data"):
            for r_ in ied.to_dict("records"):
                LAY.nodes[r_["id"]].attrs["ipr"] = {k: r_[k] for k in r_ if k not in ("id", "label")}
            bump()
            st.rerun()
        if cc[1].button("Solve rates from IPR", type="primary"):
            iprs, tubs = {}, {}
            for r_ in ied.to_dict("records"):
                if not r_["use"]:
                    continue
                iprs[r_["id"]] = tb_well.IPR(kind=r_["kind"],
                                             reservoir_pressure_psia=r_["reservoir_bara"] * tb_fa.BARA_TO_PSIA,
                                             productivity_index=r_["pi_sm3_d_bar"] * tb_fa.SM3_TO_STB / tb_fa.BARA_TO_PSIA,
                                             bubble_point_psia=r_["bubble_bara"] * tb_fa.BARA_TO_PSIA)
                tubs[r_["id"]] = tb_well.Tubing(depth_ft=r_["md_m"] * 3.28084, tvd_ft=r_["tvd_m"] * 3.28084,
                                                id_in=r_["tubing_id_in"],
                                                geothermal_f=r_["reservoir_t_c"] * 1.8 + 32)
            if not iprs:
                st.warning("Tick 'use' for at least one well first.")
            else:
                with st.spinner("Solving inflow, tubing lift and network back-pressure…"):
                    _, rates, info = tb_fa.solve_coupled(LAY, S.fa_settings, wells_in, iprs, tubs)
                for w_, q_ in rates.items():
                    v_ = wells_in[w_]
                    v_.oil_sm3_d = q_
                    tb_fa.set_well_inputs(LAY, w_, v_)
                S.nodal_info = info
                bump()
                st.rerun()
        if S.get("nodal_info"):
            inf = S.nodal_info
            (st.success if inf["converged"] else st.warning)(
                f"Nodal solve {'converged' if inf['converged'] else 'did not converge'} "
                f"after {inf['iterations']} iteration(s). {inf['note']}")

        sized = [e for e in fa_edges if CAT.get(e.item_id).category in ("flowline", "riser")]
        if sized:
            st.markdown("#### Line size sensitivity")
            cc = st.columns([2, 1, 1, 1])
            se = cc[0].selectbox("Line", [e.edge_id for e in sized], key="fa_sweep_edge",
                                 format_func=lambda i: f"{i} · {CAT.get(LAY.edges[i].item_id).name}")
            it_ = CAT.get(LAY.edges[se].item_id)
            dmin = cc[1].number_input("From ID (in)", 2.0, 48.0, float(max(it_.min_diameter_in, 4.0)), 1.0)
            dmax = cc[2].number_input("To ID (in)", 2.0, 48.0, float(max(it_.max_diameter_in, dmin + 2.0)), 1.0)
            dstep = cc[3].number_input("Step (in)", 0.5, 6.0, 2.0, 0.5)
            if st.button("Run size sensitivity"):
                ds = list(np.arange(dmin, dmax + 1e-9, dstep))
                S.fa_sweep = dict(edge=se, sig=md5(LAY.to_dict(), vars(S.fa_settings)),
                                  rows=tb_fa.diameter_sweep(LAY, se, ds, S.fa_settings))
            sw = S.get("fa_sweep")
            if sw and sw["edge"] == se and sw["sig"] == md5(LAY.to_dict(), vars(S.fa_settings)):
                sdf = pd.DataFrame(sw["rows"])
                cc = st.columns(2)
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=sdf["diameter_in"], y=sdf["max_required_whp_bara"], mode="lines+markers",
                                         name="Highest required WHP", line=dict(color=EQ["navy"], width=3)))
                if FA_RES.wells:
                    fig.add_hline(y=min(w["available_whp_bara"] for w in FA_RES.wells), line_dash="dash",
                                  line_color=EQ["torch"], annotation_text="Lowest available WHP")
                fig.update_xaxes(title="Line ID (in)")
                fig.update_yaxes(title="bara")
                cc[0].plotly_chart(eq_layout(fig, 300, title="Wellhead pressure needed"), **STRETCH)
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=sdf["diameter_in"], y=sdf["edge_t_out_c"], mode="lines+markers",
                                         name="Line outlet temperature", line=dict(color=EQ["torch"], width=3)))
                fig.update_xaxes(title="Line ID (in)")
                fig.update_yaxes(title="°C")
                cc[1].plotly_chart(eq_layout(fig, 300, title="Outlet temperature"), **STRETCH)
                st.dataframe(sdf, hide_index=True, **STRETCH)

        st.download_button("Download line results (CSV)", ldf.to_csv(index=False), "tieback_flow_assurance_lines.csv")

# ═══════════════════════════ DESIGN BASIS ═══════════════════════════
with tab_basis:
    st.caption(tb_basis.UNITS_NOTE)
    basis_rows = tb_basis.design_basis(LAY, S.cost_settings, S.sched_settings, S.fa_settings,
                                       S.nok_per_usd, S.get("catalog_source", ""))
    counts = tb_basis.summary(basis_rows)
    k = st.columns(4)
    k[0].metric("Entered", counts["ok"])
    k[1].metric("On defaults", counts["default"])
    k[2].metric("Missing", counts["missing"])
    k[3].metric("To resolve", counts["action"])
    todo = tb_basis.outstanding(basis_rows)
    if todo:
        st.markdown("#### To close out before the numbers can be relied on")
        for r in todo:
            st.markdown(f"- **{r['item']}** ({r['category']}): {r['value']}"
                        + (f" — {r['note']}" if r["note"] else ""))
    else:
        st.success("Every checklist item has a project value.")
    st.markdown("#### Full checklist")
    show = st.multiselect("Show", ["ok", "default", "missing", "action"],
                          default=["ok", "default", "missing", "action"], key=f"basis_f_{REV}")
    bdf = pd.DataFrame([r for r in basis_rows if r["status"] in show])
    st.dataframe(bdf, hide_index=True, **STRETCH, column_config={
        "category": st.column_config.TextColumn("Category", width="medium"),
        "item": st.column_config.TextColumn("Item", width="medium"),
        "value": st.column_config.TextColumn("Value (SI)", width="medium"),
        "status": st.column_config.TextColumn("Status", width="small"),
        "note": st.column_config.TextColumn("Note", width="large")})
    st.download_button("Design basis (CSV)", pd.DataFrame(basis_rows).to_csv(index=False),
                       "tieback_design_basis.csv")

# ═══════════════════════════════ VIABILITY ═══════════════════════════════
with tab_viab:
    st.caption("Does this concept stand up? Every criterion is judged on the active concept's layout, "
               "rates and settings. SI units throughout.")
    if S.get("active_case") and S.active_case != "(working layout)":
        st.info(f"Active concept: **{S.active_case}**")
    cc = st.columns([1, 3])
    td_frac = cc[0].slider("Turndown case", 0.1, 0.9, 0.5, 0.1, key=f"viab_td_{REV}",
                           help="Fraction of design rate used for the turndown checks")
    vsig = md5(LAY.to_dict(), vars(S.fa_settings), vars(S.cost_settings), td_frac)
    if cc[1].button("Run viability check", type="primary") or S.get("viab_sig") == vsig:
        if S.get("viab_sig") != vsig:
            with st.spinner("Solving, scheduling and costing the concept…"):
                S.viab_rows, S.viab_sig = tb_viability.viability(LAY, S.cost_settings, S.sched_settings,
                                                                 S.fa_settings, td_frac), vsig
        rows = S.viab_rows
        counts = tb_viability.summary(rows)
        k = st.columns(4)
        k[0].metric("Pass", counts[tb_viability.PASS])
        k[1].metric("To resolve", counts[tb_viability.ATTENTION])
        k[2].metric("Blocking", counts[tb_viability.FAIL])
        k[3].metric("Not assessed", counts[tb_viability.NA])
        v = tb_viability.verdict(rows)
        (st.error if counts[tb_viability.FAIL] else st.warning if counts[tb_viability.ATTENTION]
         else st.success)(v)
        blocking = [r for r in rows if r["status"] in (tb_viability.FAIL, tb_viability.ATTENTION)]
        if blocking:
            st.markdown("#### What to fix")
            for r in tb_viability.sort_rows(blocking):
                icon = "🔴" if r["status"] == tb_viability.FAIL else "🟠"
                st.markdown(f"{icon} **{r['criterion']}** ({r['group']}) — {r['value']}"
                            + (f", target {r['threshold']}" if r["threshold"] else "")
                            + (f". {r['action']}" if r["action"] else ""))
        st.markdown("#### All criteria")
        vdf = pd.DataFrame(tb_viability.sort_rows(rows))
        st.dataframe(vdf, hide_index=True, **STRETCH, column_config={
            "group": st.column_config.TextColumn("Group", width="small"),
            "criterion": st.column_config.TextColumn("Criterion", width="medium"),
            "status": st.column_config.TextColumn("Status", width="small"),
            "value": st.column_config.TextColumn("Value", width="medium"),
            "threshold": st.column_config.TextColumn("Target", width="small"),
            "action": st.column_config.TextColumn("If not met", width="large")})
        st.download_button("Viability checklist (CSV)", vdf.to_csv(index=False), "tieback_viability.csv")
    else:
        st.info("Run the check to score the concept on layout, flow assurance, schedule and cost.")

# ═══════════════════════════════ CASES ═══════════════════════════════
with tab_cases:
    st.caption("Snapshot concepts and compare them on cost, schedule and flow assurance. A case stores the "
               "whole project — layout, catalog and all settings — so loading one takes you back exactly.")
    if "cases" not in S:
        S.cases = []
    cc = st.columns([2, 1, 1])
    case_name = cc[0].text_input("Case name", S.project_name, key=f"case_name_{REV}")
    case_note = cc[1].text_input("Note (optional)", "", key=f"case_note_{REV}")
    if cc[2].button("Save current as case", type="primary"):
        S.cases = [c_ for c_ in S.cases if c_["name"] != case_name]
        S.cases.append(tb_cases.snapshot(case_name, LAY, S.cost_settings, S.sched_settings, S.fa_settings,
                                         case_note, S.display))
        S.case_rows = None
        st.rerun()

    if not S.cases:
        st.info("No cases yet. Build a concept, then save it here and change something — a bigger flowline, "
                "a different host, an extra phase — and save that too.")
    else:
        names = [c_["name"] for c_ in S.cases]
        cc = st.columns([2, 1, 1, 1])
        pick = cc[0].selectbox("Case", names, key=f"case_pick_{REV}")
        if cc[1].button("Load into editor"):
            set_project(*tb_cases.restore(next(c_ for c_ in S.cases if c_["name"] == pick)))
            st.rerun()
        if cc[2].button("Delete case"):
            S.cases = [c_ for c_ in S.cases if c_["name"] != pick]
            S.case_rows = None
            st.rerun()
        run_fa = cc[3].checkbox("Include flow assurance", True, key=f"case_fa_{REV}")
        sig = md5([c_["project"] for c_ in S.cases], run_fa)
        if st.button("Compare cases", type="primary") or (S.get("case_rows") and S.get("case_sig") == sig):
            if S.get("case_sig") != sig:
                with st.spinner("Costing, scheduling and solving each case…"):
                    S.case_rows = tb_cases.compare(S.cases, run_fa)
                    S.case_sig = sig
            rows = S.case_rows
            cdf = pd.DataFrame(rows)
            st.dataframe(cdf, hide_index=True, **STRETCH, column_config={
                c_: st.column_config.NumberColumn(format="%.1f") for c_ in cdf.columns
                if cdf[c_].dtype.kind == "f"})
            f_ = money_factor()
            cc = st.columns(2)
            fig = px.bar(cdf, x="case", y="capex_total_musd", color_discrete_sequence=[EQ["navy"]])
            fig.update_yaxes(title="MUSD (incl. contingency)")
            fig.update_xaxes(title=None)
            cc[0].plotly_chart(eq_layout(fig, 300, title="CAPEX by case"), **STRETCH)
            if "first_production" in cdf and cdf["first_production"].notna().any():
                fp = cdf.dropna(subset=["first_production"]).copy()
                fp["first_production"] = pd.to_datetime(fp["first_production"])
                fig = px.scatter(fp, x="first_production", y="case", color_discrete_sequence=[EQ["torch"]])
                fig.update_traces(marker=dict(size=14, symbol="diamond"))
                fig.update_xaxes(title=None)
                fig.update_yaxes(title=None)
                cc[1].plotly_chart(eq_layout(fig, 300, title="First production"), **STRETCH)
            if run_fa and "min_hydrate_margin_c" in cdf:
                fig = go.Figure()
                fig.add_trace(go.Bar(x=cdf["case"], y=cdf["min_hydrate_margin_c"], name="Hydrate margin (°C)",
                                     marker_color=EQ["teal"]))
                fig.add_trace(go.Bar(x=cdf["case"], y=cdf["min_whp_margin_bar"], name="Wellhead margin (bar)",
                                     marker_color=EQ["amber"]))
                fig.add_hline(y=0, line_color=EQ["torch"], line_dash="dash")
                st.plotly_chart(eq_layout(fig, 300, title="Flow assurance margins (higher is safer)"), **STRETCH)
            with st.expander("Differences against the first case"):
                st.dataframe(pd.DataFrame(tb_cases.deltas(rows)), hide_index=True, **STRETCH)
            st.download_button("Comparison (CSV)", cdf.to_csv(index=False), "tieback_case_comparison.csv")

        cc = st.columns(2)
        cc[0].download_button("Save case set (.yaml)", tb_cases.caseset_to_yaml(S.cases),
                              "tieback_cases.yaml", "text/yaml")
        up_cases = cc[1].file_uploader("Load case set (.yaml)", type=["yaml", "yml"], key="cases_upload")
        if up_cases is not None and st.button("Add cases from file"):
            try:
                loaded = tb_cases.caseset_from_yaml(up_cases.getvalue().decode("utf-8-sig"))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Case set not loaded: {exc}")
            else:
                have = {c_["name"] for c_ in S.cases}
                S.cases += [c_ for c_ in loaded if c_["name"] not in have]
                S.case_rows = None
                st.rerun()

# ═══════════════════════════════ EXPORT ═══════════════════════════════
with tab_exp:
    st.markdown("#### Downloads")
    cc = st.columns(3)
    cc[0].download_button("Project (.yaml)", tb_project.project_to_yaml(S.project_name, LAY, S.cost_settings,
                                                                        S.sched_settings, S.fa_settings, S.display),
                          f"{S.project_name.replace(' ', '_')}.yaml", "text/yaml")
    cc[1].download_button("Layout (GeoJSON)", json.dumps(tb_import.layout_to_geojson(LAY)),
                          "tieback_layout.geojson", "application/geo+json")
    cc[2].download_button("Quantities (CSV)", pd.DataFrame(LAY.quantities()).to_csv(index=False),
                          "tieback_quantities.csv", "text/csv")
    st.markdown("#### Screening report")
    cc = st.columns([2, 1, 1])
    rep_author = cc[0].text_input("Prepared by", "", key=f"rep_author_{REV}")
    rep_fa = cc[1].checkbox("Include flow assurance", True, key=f"rep_fa_{REV}")
    if cc[2].button("Build report", type="primary"):
        with st.spinner("Costing, scheduling, solving and writing the document…"):
            try:
                S.report_bytes = tb_report.build_report(S.project_name, LAY, S.cost_settings, S.sched_settings,
                                                        S.fa_settings, rep_author, rep_fa, S.nok_per_usd,
                                                        S.get("catalog_source", ""))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Report failed: {exc}")
    if S.get("report_bytes"):
        st.download_button("Download report (.docx)", S.report_bytes,
                           f"{S.project_name.replace(' ', '_')}_screening_report.docx",
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        st.caption("Concept summary, design checks, quantities, CAPEX with charts, schedule milestones and "
                   "critical path, flow assurance tables with the route section, and the basis and limits.")

    st.markdown("#### FieldVista hand-off")
    if SCH is not None:
        PH = tb_cost.phase_costs(EST, SCH, EMAP, S.cost_settings)
        fo = sorted([a for a in SCH.activities.values() if a.act_id.endswith("_FIRST_OIL")], key=lambda a: a.es)
        handoff = {
            "schema": "tieback_capex_handoff/0-draft",
            "note": "Draft format pending mapping to the FieldVista case schema.",
            "project": S.project_name,
            "currency": "USD",
            "capex_total_usd": round(EST["total_usd"], 0),
            "capex_by_year_usd": {int(y): round(v, 0) for y, v in PH["annual"].items()},
            "first_production": {a.act_id.split("_")[0]: a.es.isoformat() for a in fo},
            "drilling_cost_included": False,
        }
        st.code(yaml.safe_dump(handoff, sort_keys=False), language="yaml")
        st.download_button("CAPEX hand-off (.yaml)", yaml.safe_dump(handoff, sort_keys=False),
                           "tieback_capex_handoff.yaml", "text/yaml")
        st.caption("Drilling and completion cost is not included; it stays in FieldVista's well cost inputs.")
