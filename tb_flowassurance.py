"""
tb_flowassurance.py — Steady-state network hydraulics and thermal screening
on a TieBack Studio layout.

Method
------
1. Each well's production path to the host (tb_network) defines flow
   direction on every edge. Paths must form a tree (one downstream edge per
   node); loops are reported, not solved.
2. Streams are blended at junctions (standard volumes; API and gas SG
   volume-weighted).
3. Temperature is marched DOWNSTREAM from each wellhead with the analytic
   steady-state solution (tb_thermal) and adiabatic mixing at junctions.
4. Pressure is marched UPSTREAM from the host arrival pressure with the
   Beggs-Brill gradient (tb_multiphase) at local P and T, giving the
   wellhead pressure each well needs to deliver its rate.

Checks: required vs available wellhead pressure, host liquid/gas capacity,
hydrate margin along every line (Towler-Mokhatab + Hammerschmidt), no-touch
cool-down time, erosional velocity ratio (API RP 14E).

Well and pipe inputs are stored on the layout (node.attrs["fa"],
edge.attrs["u_w_m2k"]) so they persist with the project file.

Units: engine field units (psia, °F, ft, in, STB/d, scf/STB); inputs from the
UI are SI and converted here (bara, °C, m, Sm³/d, Sm³/Sm³).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import tb_multiphase as mp
import tb_thermal as th

BARA_TO_PSIA = 14.503774
M_TO_FT = 3.28084
SM3_TO_STB = 1.0 / 0.158987
SM3SM3_TO_SCFSTB = 5.614583

DEFAULT_U_W_M2K = {          # overall heat-transfer coefficient, ID-referenced (screening)
    "fl_rigid_cs": 15.0, "fl_rigid_cra": 15.0, "fl_pip": 1.0, "fl_deh": 3.0, "fl_flex": 5.0,
    "riser_flex": 5.0, "jumper_rigid": 20.0,
}
HEATED_ITEMS = {"fl_deh"}
DEFAULT_U_BY_CATEGORY = {"flowline": 10.0, "riser": 5.0, "jumper": 20.0}
WALL_FRACTION = 0.06         # steel wall thickness as fraction of ID (cool-down only)


@dataclass
class WellFA:
    """Per-well flow-assurance inputs (SI, as entered)."""
    oil_sm3_d: float = 1000.0        # oil or condensate
    water_cut: float = 0.10
    gor_sm3_sm3: float = 250.0
    api: float = 40.0
    gas_sg: float = 0.70
    salinity_wt_pct: float = 3.5
    wht_c: float = 70.0              # flowing wellhead temperature
    max_whp_bara: float = 150.0      # pressure available upstream of choke at this rate

    def __post_init__(self):
        if self.oil_sm3_d < 0 or self.gor_sm3_sm3 < 0 or self.max_whp_bara <= 0:
            raise ValueError("rates, GOR must be >= 0 and available WHP > 0")
        if not 0 <= self.water_cut < 1:
            raise ValueError("water cut must be in [0, 1)")

    def fluid(self) -> mp.Fluid:
        return mp.Fluid(api=self.api, gas_sg=self.gas_sg, gor_scf_stb=self.gor_sm3_sm3 * SM3SM3_TO_SCFSTB,
                        water_cut=self.water_cut, salinity_wt_pct=self.salinity_wt_pct)

    def rates_stb_d(self) -> Tuple[float, float]:
        q_o = self.oil_sm3_d * SM3_TO_STB
        q_w = q_o * self.water_cut / (1 - self.water_cut)
        return q_o, q_w


@dataclass
class FASettings:
    arrival_bara: float = 30.0          # host inlet (riser top / separator inlet) pressure
    seabed_temp_c: float = 6.0
    air_temp_c: float = 8.0             # riser upper section — screening uses seabed temp throughout
    default_water_depth_m: float = 120.0
    jumper_length_m: float = 50.0
    well_jumper_diameter_in: float = 6.0
    segment_length_m: float = 250.0
    roughness_in: float = 0.0018        # absolute (carbon steel ≈ 0.0018 in)
    inhibitor: str = "None"
    inhibitor_wt_pct: float = 0.0
    no_touch_hours: float = 8.0
    host_liquid_capacity_sm3_d: float = 15000.0
    host_gas_capacity_msm3_d: float = 5.0     # million Sm³/d
    erosional_c: float = 100.0

    def __post_init__(self):
        if self.inhibitor not in th.INHIBITORS:
            raise ValueError(f"unknown inhibitor '{self.inhibitor}'")
        if self.segment_length_m <= 0 or self.arrival_bara <= 0:
            raise ValueError("segment length and arrival pressure must be > 0")


def blend(streams: List[Tuple[mp.Fluid, float, float]]) -> Tuple[mp.Fluid, float, float]:
    """Blend (fluid, q_oil, q_water) streams at standard conditions."""
    q_o = sum(s[1] for s in streams)
    q_w = sum(s[2] for s in streams)
    if q_o <= 0:
        f0 = streams[0][0]
        return f0, 0.0, q_w
    gas = sum(s[0].gor_scf_stb * s[1] for s in streams)
    api = sum(s[0].api * s[1] for s in streams) / q_o
    sg = sum(s[0].gas_sg * s[0].gor_scf_stb * s[1] for s in streams) / gas if gas > 0 else streams[0][0].gas_sg
    sal = sum(s[0].salinity_wt_pct * s[2] for s in streams) / q_w if q_w > 0 else streams[0][0].salinity_wt_pct
    wc = q_w / (q_o + q_w)
    sig = sum(s[0].sigma_dyn_cm * s[1] for s in streams) / q_o
    return mp.Fluid(api=api, gas_sg=sg, gor_scf_stb=gas / q_o, water_cut=min(wc, 0.999),
                    salinity_wt_pct=sal, sigma_dyn_cm=sig), q_o, q_w


def well_inputs(layout) -> Dict[str, WellFA]:
    """Read (or default) per-well inputs from node attrs."""
    out = {}
    for n in layout.nodes.values():
        if layout.kind(n.node_id) != "well":
            continue
        d = n.attrs.get("fa") or {}
        valid = {k: v for k, v in d.items() if k in WellFA.__dataclass_fields__}
        out[n.node_id] = WellFA(**valid)
    return out


def set_well_inputs(layout, node_id: str, well: WellFA):
    layout.nodes[node_id].attrs["fa"] = asdict(well)


def u_value(layout, edge) -> float:
    if "u_w_m2k" in edge.attrs and edge.attrs["u_w_m2k"] is not None:
        return float(edge.attrs["u_w_m2k"])
    cat = layout.catalog.get(edge.item_id).category
    return DEFAULT_U_W_M2K.get(edge.item_id, DEFAULT_U_BY_CATEGORY.get(cat, 10.0))


def _depth(layout, node_id, s: FASettings) -> float:
    """Water depth; blank subsea nodes inherit the deepest connected subsea neighbour."""
    n = layout.nodes[node_id]
    if layout.kind(node_id) == "host":
        return 0.0
    if n.water_depth_m > 0:
        return n.water_depth_m
    neigh = [layout.nodes[e.to_node if e.from_node == node_id else e.from_node]
             for e in layout.edges.values() if node_id in (e.from_node, e.to_node)]
    depths = [m.water_depth_m for m in neigh if m.water_depth_m > 0 and layout.kind(m.node_id) != "host"]
    return max(depths) if depths else s.default_water_depth_m


def edge_diameter_in(layout, edge, s: FASettings) -> float:
    """Diameter, or for unsized jumpers: XT jumpers use the setting, others match adjacent lines."""
    if edge.diameter_in and edge.diameter_in > 0:
        return float(edge.diameter_in)
    ends = (edge.from_node, edge.to_node)
    if any(layout.kind(x) == "well" for x in ends):
        return s.well_jumper_diameter_in
    adj = [e.diameter_in for e in layout.edges.values()
           if e.edge_id != edge.edge_id and (e.from_node in ends or e.to_node in ends)
           and layout.catalog.get(e.item_id).category in ("flowline", "riser") and e.diameter_in > 0]
    return max(adj) if adj else s.well_jumper_diameter_in


def edge_geometry(layout, edge, upstream: str, s: FASettings) -> Tuple[float, float]:
    """(length_m, inclination_deg in flow direction; + = uphill)."""
    downstream = edge.to_node if edge.from_node == upstream else edge.from_node
    cat = layout.catalog.get(edge.item_id).category
    rise = _depth(layout, upstream, s) - _depth(layout, downstream, s)   # shallower downstream = uphill
    if cat == "jumper":
        length = s.jumper_length_m
    else:
        length = layout.edge_length(edge)
    if cat == "riser" and length < abs(rise):
        length = abs(rise)
    length = max(length, 1.0)
    theta = math.degrees(math.asin(max(-1.0, min(1.0, rise / length))))
    return length, theta


@dataclass
class EdgeResult:
    edge_id: str
    upstream: str
    downstream: str
    length_m: float
    theta_deg: float
    d_in: float
    u_w_m2k: float
    p_in_bara: float = 0.0
    p_out_bara: float = 0.0
    t_in_c: float = 0.0
    t_out_c: float = 0.0
    oil_sm3_d: float = 0.0
    water_sm3_d: float = 0.0
    gas_msm3_d: float = 0.0
    dominant_pattern: str = ""
    max_holdup: float = 0.0
    max_velocity_m_s: float = 0.0
    erosional_ratio: float = 0.0
    min_hydrate_margin_c: float = 0.0
    cooldown_h: float = math.inf
    heated: bool = False
    profile: List[dict] = field(default_factory=list)


@dataclass
class FAResult:
    edges: Dict[str, EdgeResult]
    node_pressure_bara: Dict[str, float]
    node_temp_c: Dict[str, float]
    wells: List[dict]
    host: dict
    findings: List[Tuple[str, str, str]]     # (severity, element, message)


def _flow_tree(layout):
    """Returns (downstream_edge {node: (edge_id, downstream_node)}, wells_on_edge, errors)."""
    down: Dict[str, Tuple[str, str]] = {}
    wells_on_edge: Dict[str, List[str]] = defaultdict(list)
    errors = []
    for w in sorted(n for n in layout.nodes if layout.kind(n) == "well"):
        path = layout.path_to_host(w)
        if path is None:
            errors.append(("error", w, "No production path to a host — excluded from hydraulics."))
            continue
        nodes, edges = path
        for i, eid in enumerate(edges):
            u, v = nodes[i], nodes[i + 1]
            if u in down and down[u] != (eid, v):
                errors.append(("error", u, "Wells route through this node in different directions "
                                           "(looped network) — not supported by the screening solver."))
                continue
            down[u] = (eid, v)
            wells_on_edge[eid].append(w)
    return down, wells_on_edge, errors


def solve(layout, settings: Optional[FASettings] = None, wells: Optional[Dict[str, WellFA]] = None,
          diameter_override: Optional[Dict[str, float]] = None) -> FAResult:
    s = settings or FASettings()
    wells = wells if wells is not None else well_inputs(layout)
    dia = diameter_override or {}
    down, wells_on_edge, findings = _flow_tree(layout)
    findings = list(findings)
    rough_default = s.roughness_in

    # upstream adjacency and topological order (wells → host)
    ups: Dict[str, List[str]] = defaultdict(list)
    for u, (eid, v) in down.items():
        ups[v].append(u)
    order: List[str] = []
    seen = set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for u in ups.get(n, []):
            visit(u)
        order.append(n)
    hosts = {v for _, (_, v) in down.items() if layout.kind(v) == "host"}
    for h in hosts:
        visit(h)                                   # order: upstream nodes before downstream ones

    # stream at each node (fluid, q_o, q_w) and temperature (°C)
    node_stream: Dict[str, Tuple[mp.Fluid, float, float]] = {}
    node_temp: Dict[str, float] = {}
    edge_res: Dict[str, EdgeResult] = {}
    incoming: Dict[str, List[Tuple[mp.Fluid, float, float, float, float]]] = defaultdict(list)  # +cap, T

    for n in order:
        if layout.kind(n) == "well" and n in wells:
            wf = wells[n]
            q_o, q_w = wf.rates_stb_d()
            flu = wf.fluid()
            m = th.stream_mass(q_o, q_w, flu.gor_scf_stb, flu.api, flu.gas_sg)
            incoming[n].append((flu, q_o, q_w, m.capacity_w_k, wf.wht_c))
        if not incoming[n]:
            continue
        flu, q_o, q_w = blend([(f, a, b) for f, a, b, _, _ in incoming[n]])
        node_stream[n] = (flu, q_o, q_w)
        node_temp[n] = th.mix_temperature([(c, t) for _, _, _, c, t in incoming[n]])
        if n not in down:
            continue
        eid, v = down[n]
        e = layout.edges[eid]
        length_m, theta = edge_geometry(layout, e, n, s)
        d_in = float(dia.get(eid, edge_diameter_in(layout, e, s)))
        u = u_value(layout, e)
        m = th.stream_mass(q_o, q_w, flu.gor_scf_stb, flu.api, flu.gas_sg)
        lam = th.decay_length_m(m.total, m.cp, u, d_in * 0.0254)
        heated = e.item_id in HEATED_ITEMS
        t_in = node_temp[n]
        t_out = th.temperature_at(length_m, t_in, s.seabed_temp_c, lam)
        r = EdgeResult(eid, n, v, length_m, theta, d_in, u, t_in_c=t_in, t_out_c=t_out,
                       oil_sm3_d=q_o / SM3_TO_STB, water_sm3_d=q_w / SM3_TO_STB,
                       gas_msm3_d=q_o * flu.gor_scf_stb / SM3SM3_TO_SCFSTB / SM3_TO_STB / 1e6, heated=heated)
        r._lam = lam      # noqa: SLF001 — transient for pressure pass
        edge_res[eid] = r
        incoming[v].append((flu, q_o, q_w, m.capacity_w_k, t_out))

    # pressure pass: downstream → upstream
    node_p: Dict[str, float] = {h: s.arrival_bara * BARA_TO_PSIA for h in hosts}
    for n in reversed(order):
        if n not in down or n not in node_stream:
            continue
        eid, v = down[n]
        r = edge_res[eid]
        flu, q_o, q_w = node_stream[n]
        e = layout.edges[eid]
        rel_rough = rough_default / r.d_in
        n_seg = max(4, math.ceil(r.length_m / s.segment_length_m))
        dl_m = r.length_m / n_seg
        p = node_p[v]
        patterns = defaultdict(float)
        prof = []
        min_margin, max_v, max_ero, max_h = math.inf, 0.0, 0.0, 0.0
        for i in range(n_seg, 0, -1):                       # station i at distance i·dl from edge inlet
            x_mid = (i - 0.5) * dl_m
            t_c = th.temperature_at(x_mid, r.t_in_c, s.seabed_temp_c, r._lam)
            g = mp.segment_gradient(flu, q_o, q_w, p, th.c_to_f(t_c), r.d_in, r.theta_deg, rel_rough)
            p_new = max(p + g["dpdl"] * dl_m * M_TO_FT, 1.0)
            patterns[g["pattern"]] += dl_m
            rho_mix = g["rho_ns"]
            v_m = g["v_m"]
            ero = v_m / mp.erosional_velocity_ft_s(rho_mix, s.erosional_c)
            margin = th.hydrate_margin_c(t_c, (p + p_new) / 2, flu.gas_sg, s.inhibitor, s.inhibitor_wt_pct)
            min_margin, max_v = min(min_margin, margin), max(max_v, v_m / M_TO_FT)
            max_ero, max_h = max(max_ero, ero), max(max_h, g["holdup"])
            prof.append(dict(x_m=(i - 0.5) * dl_m, p_bara=(p + p_new) / 2 / BARA_TO_PSIA, t_c=t_c,
                             holdup=g["holdup"], pattern=g["pattern"], v_m_m_s=v_m / M_TO_FT,
                             hydrate_margin_c=margin))
            p = p_new
        node_p[n] = p
        r.p_in_bara, r.p_out_bara = p / BARA_TO_PSIA, node_p[v] / BARA_TO_PSIA
        r.dominant_pattern = max(patterns, key=patterns.get) if patterns else ""
        r.max_holdup, r.max_velocity_m_s, r.erosional_ratio = max_h, max_v, max_ero
        r.min_hydrate_margin_c = min_margin
        r.profile = sorted(prof, key=lambda d: d["x_m"])
        # cool-down from the colder end, at the higher (settle-out-conservative) pressure
        p_shut = max(r.p_in_bara, r.p_out_bara) * BARA_TO_PSIA
        t_h_c = th.f_to_c(th.hydrate_temperature_f(p_shut, flu.gas_sg)
                          - th.hammerschmidt_depression_f(s.inhibitor, s.inhibitor_wt_pct))
        rho_l = mp.local_properties(flu, q_o, q_w, p_shut, th.c_to_f(r.t_out_c))["rho_l"] * 16.018
        r.cooldown_h = th.cooldown_hours(min(r.t_in_c, r.t_out_c), t_h_c, s.seabed_temp_c, r.u_w_m2k,
                                         r.d_in * 0.0254, WALL_FRACTION * r.d_in * 0.0254, rho_l,
                                         th.CP_WATER * flu.water_cut + th.CP_OIL * (1 - flu.water_cut))
        del r._lam

    # ── checks ──
    well_rows = []
    for w, wf in sorted(wells.items()):
        if w not in node_p:
            continue
        req = node_p[w] / BARA_TO_PSIA
        ok = req <= wf.max_whp_bara
        well_rows.append(dict(well=w, label=layout.nodes[w].label or w, oil_sm3_d=wf.oil_sm3_d,
                              required_whp_bara=req, available_whp_bara=wf.max_whp_bara,
                              margin_bar=wf.max_whp_bara - req, deliverable=ok))
        if not ok:
            findings.append(("error", w, f"Needs {req:.1f} bara at wellhead; only {wf.max_whp_bara:.1f} bara "
                                         f"available — rate not deliverable to host."))
    for eid, r in edge_res.items():
        if r.min_hydrate_margin_c < 0 and not r.heated:
            findings.append(("error", eid, f"Enters hydrate region (margin {r.min_hydrate_margin_c:.1f} °C) "
                                           f"in steady operation."))
        elif r.min_hydrate_margin_c < 3 and not r.heated:
            findings.append(("warning", eid, f"Hydrate margin only {r.min_hydrate_margin_c:.1f} °C."))
        if not r.heated and r.cooldown_h < s.no_touch_hours and layout.catalog.get(
                layout.edges[eid].item_id).category in ("flowline", "riser"):
            findings.append(("warning", eid, f"Cool-down to hydrate temperature in {r.cooldown_h:.1f} h "
                                             f"(< {s.no_touch_hours:.0f} h no-touch)."))
        if r.erosional_ratio > 1.0:
            findings.append(("warning", eid, f"Mixture velocity {r.erosional_ratio:.2f}× API RP 14E erosional limit."))
    host = {}
    for h in hosts:
        streams = [node_stream[u] for u in ups.get(h, []) if u in node_stream]
        liq = sum((a + b) / SM3_TO_STB for _, a, b in streams)
        gas = sum(f.gor_scf_stb * a / SM3SM3_TO_SCFSTB / SM3_TO_STB / 1e6 for f, a, _ in streams)
        arr_t = th.mix_temperature([(th.stream_mass(node_stream[u][1], node_stream[u][2],
                                                    node_stream[u][0].gor_scf_stb, node_stream[u][0].api,
                                                    node_stream[u][0].gas_sg).capacity_w_k,
                                     edge_res[down[u][0]].t_out_c)
                                    for u in ups.get(h, []) if u in down and u in node_stream])
        host[h] = dict(liquid_sm3_d=liq, gas_msm3_d=gas, arrival_bara=s.arrival_bara, arrival_t_c=arr_t)
        if liq > s.host_liquid_capacity_sm3_d:
            findings.append(("error", h, f"Liquid {liq:,.0f} Sm³/d exceeds host capacity "
                                         f"{s.host_liquid_capacity_sm3_d:,.0f} Sm³/d."))
        if gas > s.host_gas_capacity_msm3_d:
            findings.append(("error", h, f"Gas {gas:.2f} MSm³/d exceeds host capacity "
                                         f"{s.host_gas_capacity_msm3_d:.2f} MSm³/d."))
    return FAResult(edge_res, {k: v / BARA_TO_PSIA for k, v in node_p.items()}, node_temp, well_rows,
                    host, findings)


def diameter_sweep(layout, edge_id: str, diameters_in, settings: Optional[FASettings] = None) -> List[dict]:
    """Required worst-case WHP and arrival temperature vs. one line's diameter."""
    rows = []
    wells = well_inputs(layout)
    for d in diameters_in:
        res = solve(layout, settings, wells, {edge_id: float(d)})
        worst = max((w["required_whp_bara"] - w["available_whp_bara"] for w in res.wells), default=math.nan)
        req = max((w["required_whp_bara"] for w in res.wells), default=math.nan)
        er = res.edges.get(edge_id)
        rows.append(dict(diameter_in=float(d), max_required_whp_bara=req, worst_shortfall_bar=worst,
                         edge_t_out_c=er.t_out_c if er else math.nan,
                         edge_dp_bar=(er.p_in_bara - er.p_out_bara) if er else math.nan,
                         erosional_ratio=er.erosional_ratio if er else math.nan))
    return rows
