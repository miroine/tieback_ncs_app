"""
tb_network.py — Subsea layout model, validation and quantity take-off.

A Layout is a graph: nodes are point equipment placed at (lat, lon),
edges are linear equipment routed along optional intermediate vertices.
Validation returns structured findings (error / warning / info) so the UI
can colour map elements; nothing here raises on a merely *bad design*.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import yaml

import tb_geo
from tb_catalog import (Catalog, EDGE_KINDS, NODE_KINDS, PRODUCTION_EDGES,
                        edge_allowed)

SEVERITIES = ("error", "warning", "info")


@dataclass
class Node:
    node_id: str
    item_id: str
    lat: float
    lon: float
    label: str = ""
    water_depth_m: float = 0.0
    sitp_psi: float = 0.0          # wells: shut-in tubing pressure
    hipps: bool = False            # HIPPS / fortified zone downstream of this node
    phase: int = 1                 # development phase / campaign group
    attrs: dict = field(default_factory=dict)


@dataclass
class Edge:
    edge_id: str
    item_id: str
    from_node: str
    to_node: str
    diameter_in: float = 0.0
    route: List[Tuple[float, float]] = field(default_factory=list)  # intermediate (lat, lon)
    length_m: Optional[float] = None   # explicit override of computed length
    phase: int = 1
    label: str = ""
    attrs: dict = field(default_factory=dict)


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    element_id: str = ""


@dataclass
class LayoutSettings:
    datum: str = "WGS84"
    route_allowance_frac: float = 0.03
    end_allowance_m: float = 50.0


class Layout:
    def __init__(self, catalog: Catalog, settings: Optional[LayoutSettings] = None):
        self.catalog = catalog
        self.settings = settings or LayoutSettings()
        self.nodes: Dict[str, Node] = {}
        self.edges: Dict[str, Edge] = {}

    # ── editing ──
    def add_node(self, node: Node) -> Node:
        if node.node_id in self.nodes or node.node_id in self.edges:
            raise ValueError(f"duplicate id '{node.node_id}'")
        self.catalog.get(node.item_id)
        if not (-90 <= node.lat <= 90 and -180 <= node.lon <= 180):
            raise ValueError(f"{node.node_id}: coordinates out of range")
        self.nodes[node.node_id] = node
        return node

    def add_edge(self, edge: Edge) -> Edge:
        if edge.edge_id in self.edges or edge.edge_id in self.nodes:
            raise ValueError(f"duplicate id '{edge.edge_id}'")
        self.catalog.get(edge.item_id)
        for nid in (edge.from_node, edge.to_node):
            if nid not in self.nodes:
                raise ValueError(f"{edge.edge_id}: unknown node '{nid}'")
        if edge.from_node == edge.to_node:
            raise ValueError(f"{edge.edge_id}: self-loop")
        self.edges[edge.edge_id] = edge
        return edge

    def move_node(self, node_id: str, lat: float, lon: float):
        n = self.nodes[node_id]
        n.lat, n.lon = lat, lon

    def remove_node(self, node_id: str):
        self.nodes.pop(node_id)
        for eid in [e.edge_id for e in self.edges.values() if node_id in (e.from_node, e.to_node)]:
            self.edges.pop(eid)

    def kind(self, node_id: str) -> str:
        return self.catalog.get(self.nodes[node_id].item_id).category

    # ── geometry ──
    def edge_vertices(self, e: Edge) -> List[Tuple[float, float]]:
        a, b = self.nodes[e.from_node], self.nodes[e.to_node]
        return [(a.lat, a.lon), *[tuple(p) for p in e.route], (b.lat, b.lon)]

    def edge_length(self, e: Edge) -> float:
        if e.length_m is not None:
            return float(e.length_m)
        it = self.catalog.get(e.item_id)
        if not it.is_linear:
            return 0.0
        s = self.settings
        return tb_geo.route_length(self.edge_vertices(e), s.route_allowance_frac,
                                   s.end_allowance_m, s.datum)

    # ── graph helpers ──
    def _adjacency(self, kinds) -> Dict[str, List[Tuple[str, str]]]:
        adj: Dict[str, List[Tuple[str, str]]] = {n: [] for n in self.nodes}
        for e in self.edges.values():
            if self.catalog.get(e.item_id).category in kinds:
                adj[e.from_node].append((e.to_node, e.edge_id))
                adj[e.to_node].append((e.from_node, e.edge_id))
        return adj

    def path_to_host(self, start: str, kinds=PRODUCTION_EDGES):
        """BFS shortest (fewest hops) path; returns (node_ids, edge_ids) or None."""
        adj = self._adjacency(kinds)
        prev: Dict[str, Tuple[Optional[str], Optional[str]]] = {start: (None, None)}
        q = deque([start])
        while q:
            u = q.popleft()
            if self.kind(u) == "host" and u != start:
                nodes, edges = [u], []
                while prev[u][0] is not None:
                    p, eid = prev[u]
                    edges.append(eid)
                    nodes.append(p)
                    u = p
                return nodes[::-1], edges[::-1]
            for v, eid in adj[u]:
                if v not in prev:
                    prev[v] = (u, eid)
                    q.append(v)
        return None

    def reachable(self, start: str, kinds) -> set:
        adj = self._adjacency(kinds)
        seen, q = {start}, deque([start])
        while q:
            u = q.popleft()
            for v, _ in adj[u]:
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        return seen

    # ── validation ──
    def validate(self) -> List[Finding]:
        F: List[Finding] = []
        cat = self.catalog
        hosts = [n for n in self.nodes if self.kind(n) == "host"]
        wells = [n for n in self.nodes if self.kind(n) == "well"]
        if not hosts:
            F.append(Finding("error", "NO_HOST", "Layout has no host facility."))
        if not wells:
            F.append(Finding("warning", "NO_WELLS", "Layout has no wells."))

        degree = {n: 0 for n in self.nodes}
        for e in self.edges.values():
            it = cat.get(e.item_id)
            ka, kb = self.kind(e.from_node), self.kind(e.to_node)
            degree[e.from_node] += 1
            degree[e.to_node] += 1
            if it.category not in EDGE_KINDS:
                F.append(Finding("error", "EDGE_ITEM", f"'{it.name}' is not a linear/connection item.", e.edge_id))
                continue
            if not edge_allowed(it.category, ka, kb):
                F.append(Finding("error", "EDGE_ENDPOINTS",
                                 f"{it.category} cannot connect {ka} ↔ {kb}.", e.edge_id))
            if it.cost_basis == "per_inch_m" or it.max_diameter_in > 0:
                if e.diameter_in <= 0:
                    F.append(Finding("error", "DIAMETER_MISSING", f"{it.name} needs a diameter.", e.edge_id))
                elif not (it.min_diameter_in <= e.diameter_in <= it.max_diameter_in):
                    F.append(Finding("error", "DIAMETER_RANGE",
                                     f'{e.diameter_in}" outside {it.min_diameter_in}–{it.max_diameter_in}" '
                                     f"for {it.name}.", e.edge_id))
            if it.category == "flowline" and "well" in (ka, kb):
                F.append(Finding("info", "DIRECT_WELL_FLOWLINE",
                                 "Flowline lands directly on an XT — confirm a PLET/spool is not required.",
                                 e.edge_id))
            if it.is_linear and self.edge_length(e) < 1.0:
                F.append(Finding("warning", "ZERO_LENGTH", "Linear element has ~zero length.", e.edge_id))

        for nid, d in degree.items():
            if d == 0:
                F.append(Finding("warning", "ISOLATED", "Node has no connections.", nid))

        # slot capacity
        for nid in self.nodes:
            it = cat.get(self.nodes[nid].item_id)
            if it.category in ("template", "manifold") and it.slots > 0:
                n_wells = sum(1 for e in self.edges.values()
                              if nid in (e.from_node, e.to_node)
                              and cat.get(e.item_id).category in ("jumper", "flowline")
                              and self.kind(e.to_node if e.from_node == nid else e.from_node) == "well")
                if n_wells > it.slots:
                    F.append(Finding("error", "SLOTS_EXCEEDED",
                                     f"{n_wells} wells on {it.slots}-slot {it.category}.", nid))

        # production connectivity + pressure rating along path
        rating_demand: Dict[str, float] = {}
        rating_hipps: Dict[str, bool] = {}
        for w in wells:
            path = self.path_to_host(w) if hosts else None
            if path is None:
                F.append(Finding("error", "NO_PRODUCTION_PATH", "Well has no production path to a host.", w))
                continue
            sitp = self.nodes[w].sitp_psi
            node_path, edge_path = path
            # HIPPS on a node protects everything downstream of it (node itself sees SITP)
            protected = self.nodes[w].hipps
            for i, eid in enumerate(edge_path):
                if not protected:
                    rating_demand[eid] = max(rating_demand.get(eid, 0.0), sitp)
                nxt = node_path[i + 1]
                if not protected:
                    rating_demand[nxt] = max(rating_demand.get(nxt, 0.0), sitp)
                if self.nodes[nxt].hipps:
                    protected = True
            if sitp > cat.get(self.nodes[w].item_id).rating_psi:
                F.append(Finding("error", "RATING", f"SITP {sitp:.0f} psi exceeds XT rating.", w))
        for eid_or_nid, demand in rating_demand.items():
            obj = self.edges.get(eid_or_nid) or self.nodes.get(eid_or_nid)
            it = cat.get(obj.item_id)
            if it.category == "host":
                continue
            if demand > it.rating_psi:
                F.append(Finding("error", "RATING",
                                 f"Upstream SITP {demand:.0f} psi exceeds {it.name} rating "
                                 f"{it.rating_psi:.0f} psi (full-rated design). Add HIPPS or upgrade.",
                                 eid_or_nid))

        # control and power
        if hosts:
            controlled = set()
            for h in hosts:
                controlled |= self.reachable(h, ("umbilical",))
            prod_adj = self._adjacency(("jumper",))
            for w in wells:
                ok = w in controlled or any(v in controlled for v, _ in prod_adj[w])
                if not ok:
                    F.append(Finding("warning", "NO_CONTROL", "Well has no umbilical control path to host.", w))
            powered = set()
            for h in hosts:
                powered |= self.reachable(h, ("power_cable", "umbilical"))
            for nid in self.nodes:
                if self.kind(nid) in ("boosting", "compression", "separation") and nid not in powered:
                    F.append(Finding("warning", "NO_POWER", "Active subsea unit has no power supply path.", nid))
        return F

    # ── quantity take-off ──
    def quantities(self) -> List[dict]:
        rows = []
        for n in self.nodes.values():
            it = self.catalog.get(n.item_id)
            rows.append(dict(element_id=n.node_id, label=n.label or n.node_id, item_id=it.item_id,
                             item=it.name, category=it.category, basis="unit", quantity=1.0,
                             length_m=0.0, diameter_in=0.0, phase=n.phase))
        for e in self.edges.values():
            it = self.catalog.get(e.item_id)
            L = self.edge_length(e)
            rows.append(dict(element_id=e.edge_id, label=e.label or e.edge_id, item_id=it.item_id,
                             item=it.name, category=it.category,
                             basis=it.cost_basis, quantity=(L if it.is_linear else 1.0),
                             length_m=L, diameter_in=e.diameter_in, phase=e.phase))
        return rows

    # ── persistence ──
    def to_dict(self) -> dict:
        return {
            "schema": "tieback_layout/1",
            "settings": asdict(self.settings),
            "nodes": [asdict(n) for n in self.nodes.values()],
            "edges": [{**asdict(e), "route": [list(p) for p in e.route]} for e in self.edges.values()],
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False)

    @classmethod
    def from_dict(cls, d: dict, catalog: Catalog) -> "Layout":
        if d.get("schema") != "tieback_layout/1":
            raise ValueError("unsupported layout schema")
        lay = cls(catalog, LayoutSettings(**d.get("settings", {})))
        for n in d.get("nodes", []):
            lay.add_node(Node(**n))
        for e in d.get("edges", []):
            lay.add_edge(Edge(**{**e, "route": [tuple(p) for p in e.get("route", [])]}))
        return lay
