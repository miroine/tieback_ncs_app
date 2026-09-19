"""
tb_map.py — Bridge between the Layout engine and the drag-and-drop map component.

Protocol
--------
Python → JS (args):  payload (layout geometry + validation severity), palette
                     (catalog items), connection rules, overlays (GeoJSON),
                     `rev` (content hash — JS redraws only when it changes).
JS → Python (value): one event {nonce, seq, type, payload, view}.
                     `nonce` is random per iframe load and `seq` increments per
                     user action, so an event is applied exactly once even
                     though Streamlit re-delivers the last value on every rerun.

`apply_event` is pure (Layout + event → result) so it is unit-tested without
Streamlit. `render_map` is the only function that touches Streamlit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

import tb_geo
import tb_network as net
from tb_catalog import EDGE_KINDS, EDGE_RULES, NODE_KINDS, edge_allowed

COMPONENT_DIR = Path(__file__).parent / "tb_map_component"

ID_PREFIX = {
    "well": "W", "template": "TMPL", "manifold": "MF", "plet": "PLET", "plem": "PLEM",
    "ilt": "ILT", "ssiv": "SSIV", "boosting": "MPP", "compression": "COMP",
    "separation": "SEP", "riser_base": "RB", "host": "HOST",
    "flowline": "FL", "umbilical": "UMB", "jumper": "J", "riser": "RIS", "power_cable": "PC",
    "utility_line": "UL",
}
SEV_RANK = {"error": 3, "warning": 2, "info": 1}

# Service / fluid phase carried by a line — drives colour when colour mode is "fluid".
FLUIDS = ("multiphase", "oil", "gas", "condensate", "water injection", "gas lift",
          "chemical", "control", "power", "other")
FLUID_COLORS = {
    "multiphase": "#00243D", "oil": "#1E7A3C", "gas": "#EB0037", "condensate": "#E9A23B",
    "water injection": "#2E86AB", "gas lift": "#7D4EBF", "chemical": "#9DBA00",
    "control": "#B3801A", "power": "#C4561B", "other": "#6F6F6F",
}
PHASE_COLORS = ["#00243D", "#EB0037", "#007079", "#E9A23B", "#7D4EBF", "#9DBA00", "#C4561B", "#4A6B82"]
CATEGORY_FLUID = {
    "flowline": "multiphase", "riser": "multiphase", "jumper": "multiphase",
    "umbilical": "control", "power_cable": "power", "utility_line": "chemical",
}
ITEM_FLUID = {"gaslift_line": "gas lift", "winj_line": "water injection", "chem_line": "chemical",
              "service_line": "chemical", "fibre_cable": "control", "umb_power": "power"}
COLOR_MODES = ("item", "fluid", "phase", "checks")


@dataclass
class DisplaySettings:
    """How the layout is drawn. Saved with the project."""
    symbol_scale: float = 1.0          # equipment symbol size
    line_scale: float = 1.0            # line thickness
    thickness_by_diameter: bool = True  # thicker line for a bigger bore
    color_mode: str = "item"           # item | fluid | phase | checks
    fluid_colors: Dict[str, str] = field(default_factory=lambda: dict(FLUID_COLORS))

    def __post_init__(self):
        if self.color_mode not in COLOR_MODES:
            raise ValueError(f"unknown colour mode '{self.color_mode}'")
        for name, v in (("symbol_scale", self.symbol_scale), ("line_scale", self.line_scale)):
            if not 0.2 <= float(v) <= 4.0:
                raise ValueError(f"{name} must be between 0.2 and 4")


def fluid_of(layout, edge) -> str:
    """Stated fluid/service, else the default for that item or category."""
    f = (edge.attrs.get("fluid") or "").strip().lower()
    if f in FLUIDS:
        return f
    it = layout.catalog.get(edge.item_id)
    return ITEM_FLUID.get(it.item_id) or CATEGORY_FLUID.get(it.category, "other")


def edge_color(layout, edge, display: "DisplaySettings", severity: str) -> str:
    it = layout.catalog.get(edge.item_id)
    if display.color_mode == "fluid":
        return display.fluid_colors.get(fluid_of(layout, edge), FLUID_COLORS["other"])
    if display.color_mode == "phase":
        return PHASE_COLORS[(int(edge.phase) - 1) % len(PHASE_COLORS)]
    if display.color_mode == "checks":
        return {"error": "#EB0037", "warning": "#E9A23B"}.get(severity, "#6F6F6F")
    return it.line_color


def next_id(prefix: str, existing) -> str:
    used = set(existing)
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


def severity_map(findings) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for f in findings:
        if not f.element_id:
            continue
        if SEV_RANK[f.severity] > SEV_RANK.get(out.get(f.element_id, ""), 0):
            out[f.element_id] = f.severity
    return out


def to_display(layout, lat, lon):
    """Layout datum -> WGS84 for the web map."""
    return tb_geo.transform_datum(lat, lon, layout.settings.datum, "WGS84")


def from_display(layout, lat, lon):
    """WGS84 from the web map -> layout datum."""
    return tb_geo.transform_datum(lat, lon, "WGS84", layout.settings.datum)


def build_payload(layout: "net.Layout", findings=None, display: Optional["DisplaySettings"] = None,
                  visible: Optional[set] = None) -> dict:
    """Geometry is always WGS84 (display datum); the layout may be stored in ED50.

    `visible` (element ids) comes from the tag filter — elements outside it are sent
    with hidden=True so the map leaves them out without losing them.
    """
    findings = layout.validate() if findings is None else findings
    display = display or DisplaySettings()
    sev = severity_map(findings)
    cat = layout.catalog
    nodes = [dict(id=n.node_id, label=n.label or n.node_id, item_id=n.item_id,
                  item=cat.get(n.item_id).name, kind=cat.get(n.item_id).category,
                  symbol=cat.get(n.item_id).symbol or cat.get(n.item_id).category,
                  footprint=[cat.get(n.item_id).footprint_l_m, cat.get(n.item_id).footprint_w_m],
                  heading=float(n.attrs.get("heading_deg", 0.0) or 0.0),
                  lat=to_display(layout, n.lat, n.lon)[0], lon=to_display(layout, n.lat, n.lon)[1],
                  phase=n.phase, hipps=n.hipps, tags=layout.tags(n.node_id),
                  hidden=(visible is not None and n.node_id not in visible),
                  in_structure=n.attrs.get("in_structure", ""),
                  severity=sev.get(n.node_id, "")) for n in layout.nodes.values()]
    edges = [dict(id=e.edge_id, label=e.label or e.edge_id, item_id=e.item_id,
                  item=cat.get(e.item_id).name, kind=cat.get(e.item_id).category,
                  source=e.from_node, target=e.to_node, diameter_in=e.diameter_in,
                  route=[list(to_display(layout, float(a), float(b))) for a, b in e.route],
                  shape=[list(to_display(layout, float(a), float(b))) for a, b in layout.edge_shape(e)],
                  smooth=bool(e.attrs.get("smooth")),
                  color=edge_color(layout, e, display, sev.get(e.edge_id, "")),
                  dash=cat.get(e.item_id).line_dash, fluid=fluid_of(layout, e),
                  length_m=round(layout.edge_length(e), 1), phase=e.phase,
                  tags=layout.tags(e.edge_id),
                  hidden=(visible is not None and (e.edge_id not in visible
                                                   or e.from_node not in visible or e.to_node not in visible)),
                  severity=sev.get(e.edge_id, "")) for e in layout.edges.values()]
    return {"nodes": nodes, "edges": edges,
            "display": {"symbol_scale": float(display.symbol_scale), "line_scale": float(display.line_scale),
                        "thickness_by_diameter": bool(display.thickness_by_diameter),
                        "color_mode": display.color_mode}}


def build_palette(catalog) -> dict:
    node_items, edge_items = [], []
    for it in catalog.items.values():
        rec = dict(id=it.item_id, name=it.name, kind=it.category)
        if it.category in EDGE_KINDS:
            rec.update(min_d=it.min_diameter_in, max_d=it.max_diameter_in, basis=it.cost_basis)
            edge_items.append(rec)
        elif it.category in NODE_KINDS:
            node_items.append(rec)
    return {"node_items": node_items, "edge_items": edge_items}


def content_rev(*objs) -> str:
    h = hashlib.md5()
    for o in objs:
        h.update(json.dumps(o, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def default_diameter(item) -> float:
    if item.max_diameter_in <= 0:
        return 0.0
    return float(min(max(10.0, item.min_diameter_in), item.max_diameter_in))


def apply_event(layout: "net.Layout", event: Optional[dict], state: dict) -> dict:
    """Apply one component event to `layout` exactly once.

    `state` is a persistent dict (session_state) holding `seen` {nonce: seq},
    `view` (last bbox) and `selected`. Returns
    {applied, changed, selected, message, error}.
    """
    res = dict(applied=False, changed=False, selected=state.get("selected"), message="", error=None)
    if not event or not isinstance(event, dict):
        return res
    nonce, seq = str(event.get("nonce", "")), int(event.get("seq", -1))
    seen = state.setdefault("seen", {})
    if seq <= seen.get(nonce, -1):
        return res
    seen[nonce] = seq
    res["applied"] = True
    if event.get("view"):
        state["view"] = event["view"]
    typ, p = event.get("type"), event.get("payload") or {}
    cat = layout.catalog
    try:
        if typ == "add_node":
            it = cat.get(p["item_id"])
            nid = next_id(ID_PREFIX.get(it.category, "N"), list(layout.nodes) + list(layout.edges))
            la, lo = from_display(layout, float(p["lat"]), float(p["lon"]))
            layout.add_node(net.Node(nid, it.item_id, la, lo,
                                     label=str(p.get("label") or nid), phase=int(p.get("phase", 1))))
            res.update(changed=True, selected=nid, message=f"Added {it.name} {nid}")
        elif typ == "move_many":
            ids = [str(x) for x in (p.get("ids") or []) if x in layout.nodes]
            anchor = p.get("anchor")
            if anchor not in layout.nodes:
                raise KeyError(f"unknown node {anchor}")
            la, lo = from_display(layout, float(p["lat"]), float(p["lon"]))
            dlat, dlon = la - layout.nodes[anchor].lat, lo - layout.nodes[anchor].lon
            layout.translate_elements(ids, dlat, dlon)
            res.update(changed=True, selected=anchor, message=f"Moved {len(ids)} items")
        elif typ == "move_group":
            nid = p["id"]
            if nid not in layout.nodes:
                raise KeyError(f"unknown node {nid}")
            la, lo = from_display(layout, float(p["lat"]), float(p["lon"]))
            dlat, dlon = la - layout.nodes[nid].lat, lo - layout.nodes[nid].lon
            members = [nid] + layout.jumper_group(nid)
            for mid in members:
                nd = layout.nodes[mid]
                layout.move_node(mid, nd.lat + dlat, nd.lon + dlon)
            res.update(changed=True, selected=nid,
                       message=f"Moved {nid} with {len(members) - 1} attached item(s)")
        elif typ == "pick":
            la, lo = from_display(layout, float(p["lat"]), float(p["lon"]))
            state["picked"] = [la, lo]
            res["message"] = f"Point picked at {la:.5f}, {lo:.5f}"
        elif typ == "move_node":
            nid = p["id"]
            if nid not in layout.nodes:
                raise KeyError(f"unknown node {nid}")
            layout.move_node(nid, *from_display(layout, float(p["lat"]), float(p["lon"])))
            res.update(changed=True, selected=nid)
        elif typ == "add_edge":
            it = cat.get(p["item_id"])
            a, b = p["source"], p["target"]
            ka, kb = layout.kind(a), layout.kind(b)
            if not edge_allowed(it.category, ka, kb):
                raise ValueError(f"{it.category} cannot connect {ka} ↔ {kb}")
            eid = next_id(ID_PREFIX.get(it.category, "E"), list(layout.nodes) + list(layout.edges))
            d = float(p.get("diameter_in") or default_diameter(it))
            layout.add_edge(net.Edge(eid, it.item_id, a, b, diameter_in=d, label=eid,
                                     phase=max(layout.nodes[a].phase, layout.nodes[b].phase)))
            res.update(changed=True, selected=eid, message=f"Connected {a} → {b} with {it.name}")
        elif typ == "set_route":
            eid = p["id"]
            if eid not in layout.edges:
                raise KeyError(f"unknown edge {eid}")
            route = [(float(v[0]), float(v[1])) for v in p.get("route", [])]
            for la, lo in route:
                if not (-90 <= la <= 90 and -180 <= lo <= 180):
                    raise ValueError("route vertex out of range")
            layout.edges[eid].route = [from_display(layout, la, lo) for la, lo in route]
            layout.edges[eid].length_m = None
            res.update(changed=True, selected=eid)
        elif typ == "delete":
            eid = p["id"]
            if eid in layout.nodes:
                layout.remove_node(eid)
            elif eid in layout.edges:
                del layout.edges[eid]
            else:
                raise KeyError(f"unknown element {eid}")
            res.update(changed=True, selected=None, message=f"Deleted {eid}")
        elif typ == "select":
            sid = p.get("id")
            res["selected"] = sid if (sid in layout.nodes or sid in layout.edges) else None
        elif typ == "view":
            pass
        else:
            raise ValueError(f"unknown event type '{typ}'")
    except (KeyError, ValueError) as exc:
        res["error"] = str(exc).strip("'\"")
    state["selected"] = res["selected"]
    return res


_component_func = None


def apply_display_event(display: "DisplaySettings", event: Optional[dict]) -> bool:
    """Apply a toolbar size change from the map. Returns True if anything changed."""
    if not event or event.get("type") != "display":
        return False
    p = event.get("payload") or {}
    changed = False
    for name in ("symbol_scale", "line_scale"):
        if name in p:
            v = max(0.2, min(4.0, float(p[name])))
            if abs(getattr(display, name) - v) > 1e-9:
                setattr(display, name, v)
                changed = True
    return changed


def render_map(payload: dict, palette: dict, overlays: List[dict], selected: Optional[str],
               height: int = 620, fit_token: int = 0, key: str = "tieback_map",
               rasters: Optional[List[dict]] = None):
    """Render the component (Streamlit only). Returns the latest event or None.

    `rasters` are image overlays (imported grids) — {title, url, bounds, opacity}.
    Only their `rev` goes into the content hash: a grid image is a ~100 kB data
    URI and hashing it on every rerun would cost more than redrawing it.
    """
    global _component_func
    import streamlit.components.v1 as components
    if _component_func is None:
        _component_func = components.declare_component("tieback_map", path=str(COMPONENT_DIR))
    rasters = list(rasters or [])
    rev = content_rev(payload, [o.get("rev", o.get("layer")) for o in overlays],
                      [r.get("rev", r.get("title")) for r in rasters])
    return _component_func(payload=payload, palette=palette, rules=EDGE_RULES, overlays=overlays,
                           rasters=rasters, selected=selected, height=height, rev=rev,
                           fit_token=fit_token, key=key, default=None)
