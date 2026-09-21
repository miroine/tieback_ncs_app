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
    # a structure in a saved concept: tie in subsea, sharing that concept's line to its host
    subsea: bool = False
    concept: str = ""
    target_node: str = ""
    network: Optional[dict] = field(default=None, repr=False, compare=False)

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
        if self.concept:
            return f"concept:{self.concept}:{self.target_node}"
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
        twin = next((o for o in out if o.subsea == c.subsea
                     and o.name.strip().upper() == c.name.strip().upper()
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


SUBSEA_TIEIN_KINDS = ("template", "manifold", "plem", "ilt", "boosting")


def candidates_from_cases(cases: List[dict], exclude: str = "", subsea: bool = True) -> List[Candidate]:
    """Tie-in points offered by saved concepts.

    Each concept's hosts are candidates like any facility. With `subsea`, so are
    its structures that have a production path to a host — tying in there
    shares that concept's line, riser and host. `exclude` names the concept
    being screened, so it is not offered to itself.
    """
    import tb_cases
    import tb_map
    out = []
    for case in cases or []:
        name = str(case.get("name") or "")
        if not name or name == exclude:
            continue
        try:
            _, lay, *_ = tb_cases.restore(case)
        except Exception:  # noqa: BLE001 — an unreadable concept offers nothing
            continue
        net_dict = None
        for nid, n in lay.nodes.items():
            kind = lay.kind(nid)
            lat, lon = tb_map.to_display(lay, n.lat, n.lon)
            if kind == "host":
                out.append(Candidate(name=f"{n.label or nid} ({name})", lat=lat, lon=lon,
                                     kind=lay.catalog.get(n.item_id).name, water_depth_m=n.water_depth_m,
                                     source=f"concept: {name}", concept=name, target_node=nid,
                                     activity="planned", activity_reason=f"host in saved concept {name}"))
            elif subsea and kind in SUBSEA_TIEIN_KINDS and lay.path_to_host(nid):
                if net_dict is None:
                    net_dict = lay.to_dict()
                    net_dict["catalog"] = lay.catalog.to_dict()
                out.append(Candidate(name=f"{n.label or nid} ({name})", lat=lat, lon=lon,
                                     kind=f"Subsea tie-in · {lay.catalog.get(n.item_id).name}",
                                     water_depth_m=n.water_depth_m, source=f"concept: {name}",
                                     concept=name, target_node=nid, subsea=True, network=net_dict,
                                     activity="planned",
                                     activity_reason=f"{kind} in saved concept {name}"))
    return out


def near(cands: List[Candidate], lat: float, lon: float, max_km: float = 100.0,
         hosts_only: bool = True) -> List[tuple]:
    """[(candidate, distance km)] within `max_km` of a WGS84 point, nearest first."""
    rows = []
    for c in dedupe(cands):
        if hosts_only and c.subsea:
            continue
        d = tb_geo.geodesic_distance(lat, lon, c.lat, c.lon) / 1000.0
        if d <= max_km:
            rows.append((c, d))
    return sorted(rows, key=lambda r: r[1])


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


def _concept_parts(layout, cand: Candidate, prefix: str = "X_"):
    """The saved concept's nodes and edges, renamed with `prefix` and moved into `layout`'s datum.

    Returns (catalog items it needs, nodes, edges, target id)."""
    import tb_catalog
    import tb_map
    d = cand.network or {}
    cx = net.Layout.from_dict({k: v for k, v in d.items() if k != "catalog"},
                              tb_catalog.Catalog.from_dict(d["catalog"]) if d.get("catalog")
                              else layout.catalog)
    conv = lambda la, lo: tb_map.from_display(layout, *tb_map.to_display(cx, la, lo))  # noqa: E731
    nodes, edges = [], []
    for n in cx.nodes.values():
        la, lo = conv(n.lat, n.lon)
        nodes.append(net.Node(prefix + n.node_id, n.item_id, la, lo, label=n.label or n.node_id,
                              water_depth_m=n.water_depth_m, sitp_psi=n.sitp_psi, hipps=n.hipps,
                              phase=n.phase, attrs=dict(n.attrs, from_concept=cand.concept)))
    for e in cx.edges.values():
        edges.append(net.Edge(prefix + e.edge_id, e.item_id, prefix + e.from_node, prefix + e.to_node,
                              diameter_in=e.diameter_in, route=[conv(*p) for p in e.route],
                              length_m=e.length_m, phase=e.phase, label=e.label or e.edge_id,
                              attrs=dict(e.attrs, from_concept=cand.concept)))
    return cx.catalog, nodes, edges, prefix + cand.target_node, cx


def trial_layout(layout, node_id: str, cand: Candidate, ts: TieInSettings):
    """A standalone tie-back from this structure (and its wells) to the candidate host.

    For a subsea candidate in a saved concept, the trial is this structure tied
    by a new flowline into that structure, together with the whole concept — so
    the flow solve sees the shared line, riser and host carrying both.
    """
    import tb_map
    if cand.subsea:
        return _trial_subsea(layout, node_id, cand, ts)
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


def _own_part(layout, node_id: str, lay):
    """Copy this structure and its jumpered wells into `lay`."""
    src = layout.nodes[node_id]
    lay.add_node(net.Node(node_id, src.item_id, src.lat, src.lon, label=src.label,
                          water_depth_m=src.water_depth_m, phase=src.phase))
    for w in layout.jumper_group(node_id):
        if layout.kind(w) != "well":
            continue
        wn = layout.nodes[w]
        lay.add_node(net.Node(w, wn.item_id, wn.lat, wn.lon, label=wn.label,
                              water_depth_m=wn.water_depth_m, sitp_psi=wn.sitp_psi, attrs=dict(wn.attrs)))
        lay.add_edge(net.Edge(f"J_{w}", "jumper_rigid", w, node_id))


def _trial_subsea(layout, node_id: str, cand: Candidate, ts: TieInSettings):
    import copy as _copy
    ccat, nodes, edges, target, cx = _concept_parts(layout, cand)
    cat = _copy.deepcopy(layout.catalog)
    for it in ccat.items.values():
        if it.item_id not in cat.items:
            cat.add(_copy.deepcopy(it))
    lay = net.Layout(cat, net.LayoutSettings(datum=layout.settings.datum,
                                             route_allowance_frac=max(ts.tortuosity - 1.0, 0.0),
                                             end_allowance_m=layout.settings.end_allowance_m))
    _own_part(layout, node_id, lay)
    for n in nodes:
        lay.add_node(n)
    for e in edges:
        lay.add_edge(e)
    src, tgt = layout.nodes[node_id], lay.nodes[target]
    lay.add_node(net.Node("TIEIN_PLET_T", ts.plet_item, src.lat, src.lon, label="PLET template end",
                          water_depth_m=src.water_depth_m))
    lay.add_node(net.Node("TIEIN_PLET_X", ts.plet_item, tgt.lat, tgt.lon, label=f"PLET at {tgt.label}",
                          water_depth_m=tgt.water_depth_m))
    lay.add_edge(net.Edge("TIEIN_J_T", "jumper_rigid", node_id, "TIEIN_PLET_T"))
    lay.add_edge(net.Edge("TIEIN_FL", ts.flowline_item, "TIEIN_PLET_T", "TIEIN_PLET_X",
                          diameter_in=ts.diameter_in))
    lay.add_edge(net.Edge("TIEIN_J_X", "jumper_rigid", "TIEIN_PLET_X", target))
    lay.add_edge(net.Edge("TIEIN_UMB", ts.umbilical_item, target, node_id))
    d, _ = distance_and_bearing(layout, node_id, cand)
    return lay, d


def _new_cost_layout(trial, target: str):
    """What a subsea tie-in adds: the trial minus the saved concept (costed with that concept).

    The concept's structure is kept as the end of the new jumper and umbilical,
    but re-typed as a PLET and priced at zero, so its installation spread does
    not add a mobilisation the new work would not need.
    """
    lay = net.Layout(trial.catalog, trial.settings)
    keep = {n for n in trial.nodes if not n.startswith("X_")} | {target}
    for n in keep:
        nd = trial.nodes[n]
        item = "plet_std" if n == target and "plet_std" in trial.catalog.items else nd.item_id
        lay.add_node(net.Node(n, item, nd.lat, nd.lon, label=nd.label, water_depth_m=nd.water_depth_m,
                              phase=nd.phase, attrs=dict(nd.attrs)))
    for e in trial.edges.values():
        if e.from_node in keep and e.to_node in keep and not e.edge_id.startswith("X_"):
            lay.add_edge(e)
    return lay


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
            if cand.subsea:
                import copy as _copy
                cs = _copy.deepcopy(cost_settings) if cost_settings is not None else tb_cost.CostSettings()
                cs.element_override_usd = {**cs.element_override_usd, "X_" + cand.target_node: 0.0}
                est = tb_cost.estimate(_new_cost_layout(lay, "X_" + cand.target_node), cs)
                row["note"] = (f"shares {cand.concept}'s line and host; capex is the new tie-in only, "
                               f"the flow solve carries both concepts' wells")
            else:
                est = tb_cost.estimate(lay, cost_settings)
            row["tieback_capex_musd"] = est["total_usd"] / 1e6
            if run_flow_assurance and any(lay.kind(n) == "well" for n in lay.nodes):
                res = tb_fa.solve(lay, fa_settings)
                host = next(iter(res.host.values())) if res.host else {}
                mine = [w for w in res.wells if not str(w["well"]).startswith("X_")] or res.wells
                row.update(required_whp_bara=max((w["required_whp_bara"] for w in mine), default=float("nan")),
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
    """Add the screened tie-back (host, riser base, PLETs, flowline, umbilical) to the real layout.

    For a subsea candidate the new PLETs, flowline and umbilical are added and
    the saved concept's path from its structure to its host is copied in, so
    the wells have a production path. Copied items carry `from_concept`; use
    `shared_ids` to price them at zero here, since they belong to that concept.
    """
    import tb_map
    ts = ts or TieInSettings()
    if cand.subsea:
        return _attach_subsea(layout, node_id, cand, ts, prefix)
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


def shared_ids(layout) -> List[str]:
    """Items copied in from another concept by a subsea tie-in (costed in that concept)."""
    return [i for i, o in list(layout.nodes.items()) + list(layout.edges.items())
            if o.attrs.get("from_concept")]


def _attach_subsea(layout, node_id: str, cand: Candidate, ts: TieInSettings, prefix: str) -> List[str]:
    import tb_map
    _, nodes, edges, target, cx = _concept_parts(layout, cand, prefix="")
    path = cx.path_to_host(cand.target_node)
    if not path:
        raise ValueError(f"{cand.target_node} in {cand.concept} has no production path to a host")
    p_nodes, p_edges = path
    ids = lambda base: tb_map.next_id(base, list(layout.nodes) + list(layout.edges))  # noqa: E731
    rename = {}
    by_id = {n.node_id: n for n in nodes}
    by_eid = {e.edge_id: e for e in edges}
    added = []
    for old in p_nodes:
        n = by_id[old]
        existing = next((k for k, v in layout.nodes.items()
                         if v.attrs.get("from_concept") == cand.concept
                         and v.attrs.get("concept_id") == old), None)
        if existing:
            rename[old] = existing
            continue
        if n.item_id not in layout.catalog.items:
            layout.catalog.add(cx.catalog.get(n.item_id))
        new = ids(f"{prefix}_{old}")
        rename[old] = new
        n.node_id = new
        n.attrs["concept_id"] = old
        layout.add_node(n)
        added.append(new)
    for old in p_edges:
        e = by_eid[old]
        if any(v.attrs.get("from_concept") == cand.concept and v.attrs.get("concept_id") == old
               for v in layout.edges.values()):
            continue
        if e.item_id not in layout.catalog.items:
            layout.catalog.add(cx.catalog.get(e.item_id))
        new = ids(f"{prefix}_{old}")
        e.edge_id, e.from_node, e.to_node = new, rename[e.from_node], rename[e.to_node]
        e.attrs["concept_id"] = old
        layout.add_edge(e)
        added.append(new)
    tgt = layout.nodes[rename[cand.target_node]]
    src = layout.nodes[node_id]
    plet_t = ids(f"{prefix}_PLETT")
    layout.add_node(net.Node(plet_t, ts.plet_item, src.lat + 0.002, src.lon, label="PLET template end",
                             water_depth_m=src.water_depth_m, phase=src.phase))
    plet_x = ids(f"{prefix}_PLETX")
    layout.add_node(net.Node(plet_x, ts.plet_item, tgt.lat + 0.001, tgt.lon, label=f"PLET at {tgt.label}"[:40],
                             water_depth_m=tgt.water_depth_m, phase=src.phase))
    added += [plet_t, plet_x]
    for eid, item, a, b, kw in (
            (ids(f"{prefix}_JT"), "jumper_rigid", node_id, plet_t, {}),
            (ids(f"{prefix}_FL"), ts.flowline_item, plet_t, plet_x, dict(diameter_in=ts.diameter_in)),
            (ids(f"{prefix}_JX"), "jumper_rigid", plet_x, tgt.node_id, {}),
            (ids(f"{prefix}_UMB"), ts.umbilical_item, tgt.node_id, node_id, {})):
        layout.add_edge(net.Edge(eid, item, a, b, phase=src.phase, **kw))
        added.append(eid)
    host = rename[p_nodes[-1]]
    # control: the concept's own umbilical to that structure stands in for the path that
    # was not copied, so it is marked as the concept's (priced at zero here)
    if True:
        if not any(e.item_id and layout.catalog.get(e.item_id).category == "umbilical"
                   and tgt.node_id in (e.from_node, e.to_node) and host in (e.from_node, e.to_node)
                   for e in layout.edges.values()):
            eid = ids(f"{prefix}_UMBH")
            layout.add_edge(net.Edge(eid, ts.umbilical_item, host, tgt.node_id, phase=src.phase,
                                     attrs={"from_concept": cand.concept}))
            added.append(eid)
    return added
