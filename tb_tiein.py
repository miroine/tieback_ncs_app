"""
tb_tiein.py — Where should this template tie in?

Takes a structure on the map (template, manifold, PLET) and a set of candidate
hosts — Sodir facilities loaded on the map, or hosts already in the layout —
and screens each one: distance, bearing, a trial tie-back layout with its cost,
required wellhead pressure, arrival temperature and hydrate margin.

Every candidate is evaluated by building a small trial layout and running the
normal cost and flow-assurance engines, so the numbers are consistent with the
rest of the app rather than a separate correlation.

Distances are geodesic (metres); the routing allowance turns them into a
design line length.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import tb_cost
import tb_flowassurance as tb_fa
import tb_geo
import tb_network as net

# Sodir facility kinds worth screening as a host
HOST_KINDS = ("PLATFORM", "FPSO", "FSU", "FIXED", "FLOATING", "TLP", "JACKET", "SEMISUB",
              "CONDEEP", "SPAR", "MOPU", "MULTI WELL TEMPLATE", "SUBSEA STRUCTURE")
SURFACE_ONLY_DEFAULT = True

# fclPhase / fclStatus wording that means the facility is no longer a live host.
# Sodir keeps decommissioned structures in "Facilities, in place" until they are
# physically removed, so status has to be read, not assumed from the layer name.
INACTIVE_WORDS = ("REMOV", "SHUT", "ABANDON", "DECOMMISSION", "DISUSED", "DEMOLI",
                  "PLUGGED", "CESSATION", "OUT OF SERVICE")
FUTURE_WORDS = ("FUTURE", "PLANNED", "UNDER CONSTRUCTION", "APPROVED", "PROJECT")
# A mobile unit is not something to tie a field back to.
MOVEABLE_WORDS = ("MOVEABLE", "MOVABLE", "MOBILE")
MOVEABLE_KINDS = ("JACK-UP", "JACKUP", "DRILLING", "SEMISUB DRILLING", "VESSEL", "SUPPORT")


@dataclass
class Candidate:
    name: str
    lat: float
    lon: float
    kind: str = ""
    water_depth_m: float = 0.0
    operator: str = ""
    belongs_to: str = ""
    source: str = "sodir"
    npdid: str = ""
    phase: str = ""
    status: str = ""
    activity: str = "unknown"      # in operation | not in operation | planned | unknown
    activity_reason: str = ""
    fixed_or_moveable: str = ""

    @property
    def label(self) -> str:
        bits = [self.name]
        if self.kind:
            bits.append(self.kind.title())
        return " · ".join(bits)

    @property
    def key(self) -> str:
        """Identity for de-duplication: Sodir's NPDID when present, else name + position."""
        if self.npdid:
            return f"npdid:{self.npdid}"
        return f"{self.name.strip().upper()}@{self.lat:.3f},{self.lon:.3f}"


def _has(text: str, words) -> bool:
    t = (text or "").upper()
    return any(w in t for w in words)


def facility_activity(props: dict) -> tuple:
    """(activity, reason) from the Sodir status fields.

    Removal and shutdown dates take precedence over the phase text, because a
    structure keeps its old phase wording until the record is updated.
    """
    p = {k.lower(): v for k, v in (props or {}).items()}
    if p.get("fcldateremoved"):
        return "not in operation", "removed"
    if p.get("fcldateshutdown"):
        return "not in operation", "shut down"
    phase, status = str(p.get("fclphase") or ""), str(p.get("fclstatus") or "")
    if _has(phase, INACTIVE_WORDS) or _has(status, INACTIVE_WORDS):
        return "not in operation", (phase or status).lower()
    if _has(phase, FUTURE_WORDS) or _has(status, FUTURE_WORDS):
        return "planned", (phase or status).lower()
    if p.get("fclstartupdate"):
        return "in operation", "in production"
    if phase or status:
        return "in operation", (phase or status).lower()
    return "unknown", "no status published"


def candidates_from_overlay(fc: dict, surface_only: bool = SURFACE_ONLY_DEFAULT,
                            kinds: Optional[List[str]] = None, active_only: bool = True,
                            fixed_only: bool = True) -> List[Candidate]:
    """Read Sodir facility features (layer 304/307) into tie-in candidates.

    `active_only` drops facilities that are removed, shut down or still planned;
    `fixed_only` drops mobile units. Both keep records whose status is unknown.
    """
    out = []
    for f in (fc or {}).get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        p = {k.lower(): v for k, v in (f.get("properties") or {}).items()}
        if surface_only and str(p.get("fclsurface", "Y")).upper() == "N":
            continue
        kind = str(p.get("fclkind") or "")
        if kinds and kind.upper() not in {k.upper() for k in kinds}:
            continue
        mobile = _has(str(p.get("fclfixedormoveable") or ""), MOVEABLE_WORDS) or _has(kind, MOVEABLE_KINDS)
        if fixed_only and mobile:
            continue
        activity, reason = facility_activity(p)
        if active_only and activity in ("not in operation", "planned"):
            continue
        lon, lat = g["coordinates"][0], g["coordinates"][1]
        out.append(Candidate(
            name=str(p.get("_label") or p.get("fclname") or "Facility"),
            lat=lat, lon=lon, kind=kind,
            water_depth_m=float(p.get("fclwaterdepth") or 0.0),
            operator=str(p.get("fclcurrentoperatorname") or ""),
            belongs_to=str(p.get("fclbelongstoname") or ""),
            npdid=str(p.get("fclnpdidfacility") or ""),
            phase=str(p.get("fclphase") or ""), status=str(p.get("fclstatus") or ""),
            activity=activity, activity_reason=reason,
            fixed_or_moveable=str(p.get("fclfixedormoveable") or "")))
    return dedupe(out)


def dedupe(cands: List[Candidate], radius_m: float = 300.0) -> List[Candidate]:
    """One entry per facility: the same platform appears in several Sodir layers,
    and near-identical records share a name and position."""
    out: List[Candidate] = []
    seen = set()
    for c in cands:
        if c.key in seen:
            continue
        twin = next((o for o in out if o.name.strip().upper() == c.name.strip().upper()
                     and tb_geo.geodesic_distance(o.lat, o.lon, c.lat, c.lon) < radius_m), None)
        if twin is not None:
            if not twin.water_depth_m and c.water_depth_m:      # keep the richer record
                twin.water_depth_m = c.water_depth_m
            if not twin.operator and c.operator:
                twin.operator = c.operator
            continue
        seen.add(c.key)
        out.append(c)
    return out


def candidates_from_layout(layout) -> List[Candidate]:
    out = []
    for n in layout.nodes.values():
        if layout.kind(n.node_id) != "host":
            continue
        out.append(Candidate(name=n.label or n.node_id, lat=n.lat, lon=n.lon,
                             kind=layout.catalog.get(n.item_id).name,
                             water_depth_m=n.water_depth_m, source="layout",
                             npdid=f"layout:{n.node_id}", activity="in operation",
                             activity_reason="in this layout"))
    return dedupe(out)


def distance_and_bearing(layout, node_id: str, cand: Candidate) -> tuple:
    """(geodesic distance m, bearing deg from the node to the candidate)."""
    import tb_map
    n = layout.nodes[node_id]
    lat, lon = tb_map.to_display(layout, n.lat, n.lon)     # candidates are WGS84
    d, az, _ = tb_geo.vincenty_inverse(lat, lon, cand.lat, cand.lon)
    return d, az


@dataclass
class TieInSettings:
    diameter_in: float = 10.0
    flowline_item: str = "fl_rigid_cs"
    umbilical_item: str = "umb_static"
    riser_item: str = "riser_flex"
    host_item: str = "host_tiein"
    plet_item: str = "plet_std"
    riser_base_item: str = "riser_base"
    tortuosity: float = 1.05           # route length vs straight line
    max_distance_km: float = 60.0
    riser_length_factor: float = 2.2   # riser length vs water depth
    default_host_depth_m: float = 120.0


def trial_layout(layout, node_id: str, cand: Candidate, ts: TieInSettings):
    """A standalone tie-back from this structure (and its wells) to the candidate host."""
    import tb_map
    cat = layout.catalog
    src = layout.nodes[node_id]
    lay = net.Layout(cat, net.LayoutSettings(datum=layout.settings.datum,
                                             route_allowance_frac=max(ts.tortuosity - 1.0, 0.0),
                                             end_allowance_m=layout.settings.end_allowance_m))
    lay.add_node(net.Node(node_id, src.item_id, src.lat, src.lon, label=src.label,
                          water_depth_m=src.water_depth_m, phase=src.phase))
    for w in layout.jumper_group(node_id):
        if layout.kind(w) != "well":
            continue
        wn = layout.nodes[w]
        lay.add_node(net.Node(w, wn.item_id, wn.lat, wn.lon, label=wn.label,
                              water_depth_m=wn.water_depth_m, sitp_psi=wn.sitp_psi, attrs=dict(wn.attrs)))
        lay.add_edge(net.Edge(f"J_{w}", "jumper_rigid", w, node_id))
    hlat, hlon = tb_map.from_display(layout, cand.lat, cand.lon)
    depth = cand.water_depth_m or ts.default_host_depth_m
    lay.add_node(net.Node("TIEIN_HOST", ts.host_item, hlat, hlon, label=cand.name, water_depth_m=0.0))
    lay.add_node(net.Node("TIEIN_RB", ts.riser_base_item, hlat, hlon, label="Riser base",
                          water_depth_m=depth))
    lay.add_node(net.Node("TIEIN_PLET_H", ts.plet_item, hlat, hlon, label="PLET host end",
                          water_depth_m=depth))
    lay.add_node(net.Node("TIEIN_PLET_T", ts.plet_item, src.lat, src.lon, label="PLET template end",
                          water_depth_m=src.water_depth_m))
    d, _ = distance_and_bearing(layout, node_id, cand)
    lay.add_edge(net.Edge("TIEIN_RISER", ts.riser_item, "TIEIN_RB", "TIEIN_HOST",
                          diameter_in=ts.diameter_in, length_m=max(depth * ts.riser_length_factor, 50.0)))
    lay.add_edge(net.Edge("TIEIN_J_RB", "jumper_rigid", "TIEIN_PLET_H", "TIEIN_RB"))
    lay.add_edge(net.Edge("TIEIN_FL", ts.flowline_item, "TIEIN_PLET_T", "TIEIN_PLET_H",
                          diameter_in=ts.diameter_in))
    lay.add_edge(net.Edge("TIEIN_J_T", "jumper_rigid", node_id, "TIEIN_PLET_T"))
    lay.add_edge(net.Edge("TIEIN_UMB", ts.umbilical_item, "TIEIN_HOST", node_id))
    return lay, d


def screen(layout, node_id: str, candidates: List[Candidate], ts: Optional[TieInSettings] = None,
           fa_settings=None, cost_settings=None, wells: Optional[Dict[str, "tb_fa.WellFA"]] = None,
           run_flow_assurance: bool = True) -> List[dict]:
    """Rank candidate hosts for a tie-back from `node_id`. Nearest first."""
    ts = ts or TieInSettings()
    rows = []
    for cand in dedupe(candidates):
        d, az = distance_and_bearing(layout, node_id, cand)
        if d / 1000.0 > ts.max_distance_km:
            continue
        row = dict(host=cand.name, kind=cand.kind, status=cand.activity, status_note=cand.activity_reason,
                   source=cand.source, operator=cand.operator,
                   distance_km=d / 1000.0, bearing_deg=az,
                   line_length_km=d * ts.tortuosity / 1000.0,
                   host_water_depth_m=cand.water_depth_m or float("nan"),
                   lat=cand.lat, lon=cand.lon)
        try:
            lay, _ = trial_layout(layout, node_id, cand, ts)
            if wells:
                for w, v in wells.items():
                    if w in lay.nodes:
                        tb_fa.set_well_inputs(lay, w, v)
            est = tb_cost.estimate(lay, cost_settings)
            row["tieback_capex_musd"] = est["total_usd"] / 1e6
            if run_flow_assurance and any(lay.kind(n) == "well" for n in lay.nodes):
                res = tb_fa.solve(lay, fa_settings)
                host = next(iter(res.host.values())) if res.host else {}
                row.update(required_whp_bara=max((w["required_whp_bara"] for w in res.wells), default=float("nan")),
                           arrival_t_c=host.get("arrival_t_c", float("nan")),
                           min_hydrate_margin_c=min((r.min_hydrate_margin_c for r in res.edges.values()
                                                     if not r.heated), default=float("nan")),
                           deliverable=all(w["deliverable"] for w in res.wells) if res.wells else None)
        except Exception as exc:  # noqa: BLE001 — a bad candidate must not stop the screening
            row["note"] = str(exc)
        rows.append(row)
    return sorted(rows, key=lambda r: r["distance_km"])


def attach_to_layout(layout, node_id: str, cand: Candidate, ts: Optional[TieInSettings] = None,
                     prefix: str = "TI") -> List[str]:
    """Add the screened tie-back (host, riser base, PLETs, flowline, umbilical) to the real layout."""
    import tb_map
    ts = ts or TieInSettings()
    cat = layout.catalog
    ids = lambda base: tb_map.next_id(base, list(layout.nodes) + list(layout.edges))
    hlat, hlon = tb_map.from_display(layout, cand.lat, cand.lon)
    depth = cand.water_depth_m or ts.default_host_depth_m
    src = layout.nodes[node_id]
    host = ids(f"{prefix}_HOST")
    layout.add_node(net.Node(host, ts.host_item, hlat, hlon, label=cand.name[:40], water_depth_m=0.0,
                             phase=src.phase))
    rb = ids(f"{prefix}_RB")
    layout.add_node(net.Node(rb, ts.riser_base_item, hlat - 0.002, hlon, label="Riser base",
                             water_depth_m=depth, phase=src.phase))
    plet_h = ids(f"{prefix}_PLETH")
    layout.add_node(net.Node(plet_h, ts.plet_item, hlat - 0.004, hlon, label="PLET host end",
                             water_depth_m=depth, phase=src.phase))
    plet_t = ids(f"{prefix}_PLETT")
    layout.add_node(net.Node(plet_t, ts.plet_item, src.lat + 0.002, src.lon, label="PLET template end",
                             water_depth_m=src.water_depth_m, phase=src.phase))
    added = [host, rb, plet_h, plet_t]
    for eid, item, a, b, kw in (
            (ids(f"{prefix}_RIS"), ts.riser_item, rb, host,
             dict(diameter_in=ts.diameter_in, length_m=max(depth * ts.riser_length_factor, 50.0))),
            (ids(f"{prefix}_JRB"), "jumper_rigid", plet_h, rb, {}),
            (ids(f"{prefix}_FL"), ts.flowline_item, plet_t, plet_h, dict(diameter_in=ts.diameter_in)),
            (ids(f"{prefix}_JT"), "jumper_rigid", node_id, plet_t, {}),
            (ids(f"{prefix}_UMB"), ts.umbilical_item, host, node_id, {})):
        layout.add_edge(net.Edge(eid, item, a, b, phase=src.phase, **kw))
        added.append(eid)
    return added
