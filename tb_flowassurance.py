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

import tb_bathymetry as tb_bath
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
# The upstream march is stopped here. Nothing subsea is rated anywhere near this, so a
# line that needs more is simply too small at that rate: marching on produces pressures
# and temperatures no correlation is valid at (and used to crash the page).
MAX_MARCH_BARA = 1500.0
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
    use_seabed_profile: bool = True     # follow stored EMODnet profiles instead of a straight slope
    free_span_gap_m: float = 0.5        # seabed clearance that counts as a span
    max_free_span_m: float = 400.0
    include_jt: bool = True             # Joule-Thomson cooling in the thermal march
    pt_iterations: int = 3              # P/T coupling passes (JT needs > 1)
    severe_slug_vsg_m_s: float = 3.0    # riser gas velocity below which slugging is flagged
    # subsea boosting: the differential pressure a station adds, and the lowest suction
    # pressure it may be asked to work at. A node can override with attrs["boost_dp_bar"].
    boost_dp_bar: float = 80.0
    min_suction_bara: float = 10.0

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


INJECTOR_WELL_FLUIDS = ("water injector", "gas injector")


def well_inputs(layout) -> Dict[str, WellFA]:
    """Read (or default) per-well inputs from node attrs — producers only.

    An injector is fed from the host and puts nothing into the production
    network; solving it as a producer would add a phantom stream to every line
    and to the host intake.
    """
    out = {}
    for n in layout.nodes.values():
        if layout.kind(n.node_id) != "well":
            continue
        if str(n.attrs.get("well_fluid", "")).lower() in INJECTOR_WELL_FLUIDS:
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


def station_elevations(layout, edge, upstream: str, length_m: float, n_seg: int, s: "FASettings") -> List[float]:
    """Elevation (m, negative below sea level) at each station boundary.

    Uses the stored EMODnet seabed profile when present, oriented from the
    upstream end; otherwise interpolates straight between the end-node depths.
    """
    z_up, z_dn = -_depth(layout, upstream, s), -_depth(
        layout, edge.to_node if edge.from_node == upstream else edge.from_node, s)
    prof = edge.attrs.get("seabed_profile") if s.use_seabed_profile else None
    cat = layout.catalog.get(edge.item_id).category
    if prof and len(prof) >= 2 and cat != "riser":
        zs = [-float(d) for d in prof]
        if upstream == edge.to_node:
            zs = zs[::-1]
        out = []
        for i in range(n_seg + 1):
            x = i * (len(zs) - 1) / n_seg
            lo, hi = int(math.floor(x)), min(int(math.ceil(x)), len(zs) - 1)
            out.append(zs[lo] if hi == lo else zs[lo] + (zs[hi] - zs[lo]) * (x - lo))
        return out
    return [z_up + (z_dn - z_up) * i / n_seg for i in range(n_seg + 1)]


def station_inclinations(elevs: List[float], dl_m: float) -> List[float]:
    """Inclination (deg, + = uphill in flow direction) for each station."""
    out = []
    for a, b in zip(elevs[:-1], elevs[1:]):
        out.append(math.degrees(math.asin(max(-1.0, min(1.0, (b - a) / max(dl_m, 1e-9))))))
    return out


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
    liquid_inventory_m3: float = 0.0
    free_spans: List[dict] = field(default_factory=list)
    uses_seabed_profile: bool = False
    elevations: List[float] = field(default_factory=list)
    min_gas_velocity_m_s: float = 0.0
    slug_risk: bool = False
    profile: List[dict] = field(default_factory=list)


@dataclass
class FAResult:
    edges: Dict[str, EdgeResult]
    node_pressure_bara: Dict[str, float]
    node_temp_c: Dict[str, float]
    wells: List[dict]
    host: dict
    findings: List[Tuple[str, str, str]]     # (severity, element, message)


def e_item(layout, edge_id: str) -> str:
    return layout.edges[edge_id].item_id


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

    # ── stream blending, then coupled temperature (downstream) / pressure (upstream) passes ──
    node_stream: Dict[str, Tuple[mp.Fluid, float, float]] = {}
    edge_res: Dict[str, EdgeResult] = {}
    seg_counts: Dict[str, int] = {}
    station_p: Dict[str, List[float]] = {}        # per edge, pressure at each station (psia)
    node_temp: Dict[str, float] = {}
    edge_t: Dict[str, List[float]] = {}           # per edge, temperature at each station (°C)
    node_p: Dict[str, float] = {}

    def blend_streams():
        incoming: Dict[str, List[Tuple[mp.Fluid, float, float]]] = defaultdict(list)
        for n_ in order:
            if layout.kind(n_) == "well" and n_ in wells:
                wf = wells[n_]
                q_o_, q_w_ = wf.rates_stb_d()
                incoming[n_].append((wf.fluid(), q_o_, q_w_))
            if not incoming[n_]:
                continue
            node_stream[n_] = blend(incoming[n_])
            if n_ in down:
                incoming[down[n_][1]].append(node_stream[n_])

    def geometry():
        for n_ in order:
            if n_ not in down or n_ not in node_stream:
                continue
            eid, v = down[n_]
            e = layout.edges[eid]
            flu, q_o_, q_w_ = node_stream[n_]
            length_m, theta = edge_geometry(layout, e, n_, s)
            d_in = float(dia.get(eid, edge_diameter_in(layout, e, s)))
            u = u_value(layout, e)
            edge_res[eid] = EdgeResult(eid, n_, v, length_m, theta, d_in, u,
                                       oil_sm3_d=q_o_ / SM3_TO_STB, water_sm3_d=q_w_ / SM3_TO_STB,
                                       gas_msm3_d=q_o_ * flu.gor_scf_stb / SM3SM3_TO_SCFSTB / SM3_TO_STB / 1e6,
                                       heated=e.item_id in HEATED_ITEMS)
            seg_counts[eid] = max(4, math.ceil(length_m / s.segment_length_m))

    def temperature_pass():
        """Downstream march: pipe heat loss plus Joule-Thomson over each station."""
        arriving: Dict[str, List[Tuple[float, float]]] = defaultdict(list)   # (m·cp, T)
        for n_ in order:
            if n_ not in node_stream:
                continue
            flu, q_o_, q_w_ = node_stream[n_]
            m = th.stream_mass(q_o_, q_w_, flu.gor_scf_stb, flu.api, flu.gas_sg)
            if layout.kind(n_) == "well" and n_ in wells:
                arriving[n_].append((m.capacity_w_k, wells[n_].wht_c))
            node_temp[n_] = th.mix_temperature(arriving[n_]) if arriving[n_] else s.seabed_temp_c
            if n_ not in down:
                continue
            eid, v = down[n_]
            r = edge_res[eid]
            n_seg = seg_counts[eid]
            dl_m = r.length_m / n_seg
            lam = th.decay_length_m(m.total, m.cp, r.u_w_m2k, r.d_in * 0.0254)
            gas_frac = m.gas_kg_s / m.total if m.total > 0 else 0.0
            t = node_temp[n_]
            temps = []
            ps = station_p.get(eid)
            for i in range(n_seg):
                t_mid = th.temperature_at(dl_m / 2.0, t, s.seabed_temp_c, lam)   # exponential midpoint
                t_next = th.temperature_at(dl_m, t, s.seabed_temp_c, lam)
                if s.include_jt and ps:
                    p_in = ps[i - 1] if i > 0 else ps[0]
                    dp_bar = (ps[i] - p_in) / BARA_TO_PSIA if i > 0 else 0.0
                    mu = th.jt_coefficient_mixture_k_per_bar(max(ps[i], 1.0), th.c_to_f(t), flu.gas_sg,
                                                             gas_frac, mp.z_factor)
                    t_mid += mu * dp_bar / 2.0
                    t_next += mu * dp_bar
                temps.append(t_mid)
                t = t_next
            edge_t[eid] = temps
            r.t_in_c, r.t_out_c = node_temp[n_], t
            arriving[v].append((m.capacity_w_k, t))

    over_limit = set()
    boosted: Dict[str, float] = {}

    def pressure_pass():
        """Upstream march from the host arrival pressure."""
        node_p.clear()
        over_limit.clear()
        boosted.clear()
        for h in hosts:
            node_p[h] = s.arrival_bara * BARA_TO_PSIA
        for n_ in reversed(order):
            if n_ not in down or n_ not in node_stream:
                continue
            eid, v = down[n_]
            r = edge_res[eid]
            edge_obj = layout.edges[eid]
            flu, q_o_, q_w_ = node_stream[n_]
            rel_rough = rough_default / r.d_in
            n_seg = seg_counts[eid]
            dl_m = r.length_m / n_seg
            p = node_p[v]
            patterns = defaultdict(float)
            prof, ps = [], [0.0] * n_seg
            min_margin, max_v, max_ero, max_h = math.inf, 0.0, 0.0, 0.0
            liquid_m3, min_vsg = 0.0, math.inf
            temps = edge_t.get(eid) or [r.t_in_c] * n_seg
            area_m2 = math.pi * (r.d_in * 0.0254) ** 2 / 4.0
            elevs = station_elevations(layout, edge_obj, n_, r.length_m, n_seg, s)
            thetas = station_inclinations(elevs, dl_m)
            r.elevations = elevs
            r.uses_seabed_profile = bool(edge_obj.attrs.get("seabed_profile")) and s.use_seabed_profile \
                and layout.catalog.get(edge_obj.item_id).category != "riser"
            for i in range(n_seg - 1, -1, -1):
                t_c = temps[i]
                g = mp.segment_gradient(flu, q_o_, q_w_, p, th.c_to_f(t_c), r.d_in, thetas[i], rel_rough)
                p_new = max(p + g["dpdl"] * dl_m * M_TO_FT, 1.0)
                if p_new > MAX_MARCH_BARA * BARA_TO_PSIA:
                    p_new = MAX_MARCH_BARA * BARA_TO_PSIA
                    over_limit.add(eid)
                ps[i] = (p + p_new) / 2.0
                patterns[g["pattern"]] += dl_m
                ero = g["v_m"] / mp.erosional_velocity_ft_s(g["rho_ns"], s.erosional_c)
                margin = th.hydrate_margin_c(t_c, ps[i], flu.gas_sg, s.inhibitor, s.inhibitor_wt_pct)
                min_margin, max_v = min(min_margin, margin), max(max_v, g["v_m"] / M_TO_FT)
                max_ero, max_h = max(max_ero, ero), max(max_h, g["holdup"])
                liquid_m3 += g["holdup"] * area_m2 * dl_m
                min_vsg = min(min_vsg, g["v_sg"] / M_TO_FT)
                prof.append(dict(x_m=(i + 0.5) * dl_m, p_bara=ps[i] / BARA_TO_PSIA, t_c=t_c,
                                 holdup=g["holdup"], pattern=g["pattern"], v_m_m_s=g["v_m"] / M_TO_FT,
                                 hydrate_margin_c=margin, elev_m=(elevs[i] + elevs[i + 1]) / 2,
                                 incl_deg=thetas[i]))
                p = p_new
            # a boosting or compression station lifts the pressure: everything upstream of it
            # only has to reach its suction pressure
            dp = 0.0
            kind_n = layout.catalog.get(layout.nodes[n_].item_id).category if n_ in layout.nodes else ""
            if kind_n in ("boosting", "compression"):
                dp = float(layout.nodes[n_].attrs.get("boost_dp_bar", s.boost_dp_bar) or 0.0)
                if dp > 0:
                    suction = max(p - dp * BARA_TO_PSIA, s.min_suction_bara * BARA_TO_PSIA)
                    boosted[n_] = (p - suction) / BARA_TO_PSIA
                    p = suction
            node_p[n_] = p
            station_p[eid] = ps
            r.p_in_bara, r.p_out_bara = p / BARA_TO_PSIA, node_p[v] / BARA_TO_PSIA
            r.dominant_pattern = max(patterns, key=patterns.get) if patterns else ""
            r.max_holdup, r.max_velocity_m_s, r.erosional_ratio = max_h, max_v, max_ero
            r.min_hydrate_margin_c = min_margin
            r.liquid_inventory_m3 = liquid_m3
            r.min_gas_velocity_m_s = 0.0 if min_vsg is math.inf else min_vsg
            cat_ = layout.catalog.get(e_item(layout, eid)).category
            r.slug_risk = (cat_ == "riser" and r.dominant_pattern in ("intermittent", "segregated", "transition")
                           and r.min_gas_velocity_m_s < s.severe_slug_vsg_m_s)
            r.profile = sorted(prof, key=lambda d: d["x_m"])

    blend_streams()
    geometry()
    for _ in range(max(1, int(s.pt_iterations) if s.include_jt else 1)):
        temperature_pass()
        pressure_pass()

    for nid, dp in sorted(boosted.items()):
        if dp > 0:
            findings.append(("info", nid, f"Boosting station adds {dp:,.0f} bar; the wells upstream see "
                                          f"{node_p.get(nid, 0.0) / BARA_TO_PSIA:,.0f} bara at its suction."))

    for eid in sorted(over_limit):
        findings.append(("error", eid,
                         f"Pressure runs past {MAX_MARCH_BARA:,.0f} bara in this line — it is too small for "
                         f"this rate (or the rate is too high). The march was stopped there, so the numbers "
                         f"on this line are a floor, not a result. Increase the bore, add boosting, or lower the rate."))

    # free-span screening from the stored seabed profiles
    for eid, r in edge_res.items():
        e = layout.edges[eid]
        prof = e.attrs.get("seabed_profile")
        if prof and len(prof) >= 3 and s.use_seabed_profile:
            dx = r.length_m / (len(prof) - 1)
            r.free_spans = tb_bath.free_spans([float(d) for d in prof], dx, s.free_span_gap_m,
                                              s.max_free_span_m)

    # cool-down, once the converged profiles are known
    for eid, r in edge_res.items():
        flu, q_o_, q_w_ = node_stream[r.upstream]
        p_shut = max(r.p_in_bara, r.p_out_bara) * BARA_TO_PSIA
        t_h_c = th.f_to_c(th.hydrate_temperature_f(p_shut, flu.gas_sg)
                          - th.hammerschmidt_depression_f(s.inhibitor, s.inhibitor_wt_pct))
        rho_l = mp.local_properties(flu, q_o_, q_w_, p_shut, th.c_to_f(r.t_out_c))["rho_l"] * 16.018
        r.cooldown_h = th.cooldown_hours(min(r.t_in_c, r.t_out_c), t_h_c, s.seabed_temp_c, r.u_w_m2k,
                                         r.d_in * 0.0254, WALL_FRACTION * r.d_in * 0.0254, rho_l,
                                         th.CP_WATER * flu.water_cut + th.CP_OIL * (1 - flu.water_cut))

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
        if r.slug_risk:
            findings.append(("warning", eid, f"Severe slugging risk: riser gas velocity down to "
                                             f"{r.min_gas_velocity_m_s:.1f} m/s in {r.dominant_pattern} flow."))
        if r.free_spans:
            longest = max(r.free_spans, key=lambda x: x["length_m"])
            findings.append(("warning", eid, f"{len(r.free_spans)} potential free span(s) on the seabed "
                                             f"profile; longest {longest['length_m']:,.0f} m with "
                                             f"{longest['max_gap_m']:.1f} m clearance — survey and span check."))
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


def path_section(layout, result: FAResult, well: str, settings: Optional[FASettings] = None) -> List[dict]:
    """Longitudinal section from a wellhead to the host: seabed and as-laid line
    elevation with the local pressure, temperature and hydrate curve.

    Elevation is metres relative to sea level (negative below). Flowlines and
    jumpers follow the seabed; a riser leaves the seabed at its base and climbs
    to the host, so the seabed stays at the riser-base depth beneath it.
    """
    s = settings or FASettings()
    path = layout.path_to_host(well) if well in layout.nodes else None
    if path is None:
        return []
    nodes, edges = path
    rows: List[dict] = []
    x0 = 0.0
    for i, eid in enumerate(edges):
        r = result.edges.get(eid)
        if r is None:
            continue
        cat = layout.catalog.get(layout.edges[eid].item_id).category
        z_up = -_depth(layout, nodes[i], s)
        z_dn = -_depth(layout, nodes[i + 1], s)
        for pt in r.profile:
            f = min(max(pt["x_m"] / max(r.length_m, 1e-9), 0.0), 1.0)
            z_pipe = pt.get("elev_m", z_up + (z_dn - z_up) * f)
            z_bed = min(z_up, z_dn) if cat == "riser" else z_pipe
            rows.append(dict(distance_m=x0 + pt["x_m"], line=eid, kind=cat,
                             pipe_elev_m=z_pipe, seabed_elev_m=z_bed,
                             p_bara=pt["p_bara"], t_c=pt["t_c"],
                             hydrate_t_c=pt["t_c"] - pt["hydrate_margin_c"],
                             hydrate_margin_c=pt["hydrate_margin_c"], pattern=pt["pattern"],
                             velocity_m_s=pt["v_m_m_s"], holdup=pt["holdup"]))
        x0 += r.length_m
    if rows:      # close the section at the host so the riser reaches sea level
        last_e = result.edges.get(edges[-1])
        rows.append(dict(rows[-1], distance_m=x0, pipe_elev_m=-_depth(layout, nodes[-1], s),
                         seabed_elev_m=rows[-1]["seabed_elev_m"],
                         p_bara=last_e.p_out_bara if last_e else rows[-1]["p_bara"],
                         t_c=last_e.t_out_c if last_e else rows[-1]["t_c"]))
    return rows


def section_nodes(layout, result: FAResult, well: str, settings: Optional[FASettings] = None) -> List[dict]:
    """Node markers (name, distance along the section, elevation) for the same path."""
    s = settings or FASettings()
    path = layout.path_to_host(well) if well in layout.nodes else None
    if path is None:
        return []
    nodes, edges = path
    out, x = [], 0.0
    for i, nid in enumerate(nodes):
        out.append(dict(node=nid, label=layout.nodes[nid].label or nid, kind=layout.kind(nid),
                        distance_m=x, elev_m=-_depth(layout, nid, s),
                        p_bara=result.node_pressure_bara.get(nid),
                        t_c=result.node_temp_c.get(nid)))
        if i < len(edges):
            r = result.edges.get(edges[i])
            x += r.length_m if r else 0.0
    return out


def pipe_cross_section(item, d_in: float) -> List[dict]:
    """Concentric build-up (mm diameters) for a pipe cross-section drawing."""
    bore = max(float(d_in), 0.0) * 25.4
    layers = [dict(name="Bore (fluid)", outer_mm=bore, color="#7FB2D6", note=f'{d_in:g}" ID')]
    od = bore
    if item.wall_thickness_in > 0:
        od = bore + 2 * item.wall_thickness_in * 25.4
        layers.append(dict(name="Steel wall", outer_mm=od, color="#54616C",
                           note=f'{item.wall_thickness_in:g}" WT'))
    else:
        od = bore + 2 * 20.0      # flexible: armour/pressure sheath layers
        layers.append(dict(name="Flexible armour layers", outer_mm=od, color="#54616C", note="≈20 mm"))
    if item.insulation_mm > 0:
        od += 2 * item.insulation_mm
        layers.append(dict(name="Insulation", outer_mm=od, color="#E9C46A", note=f"{item.insulation_mm:g} mm"))
    if item.coating_mm > 0:
        od += 2 * item.coating_mm
        label = "Concrete weight coating" if item.coating_mm >= 30 else "External coating"
        layers.append(dict(name=label, outer_mm=od, color="#B8B8B8", note=f"{item.coating_mm:g} mm"))
    if item.item_id == "fl_pip":
        od += 2 * 12.0
        layers.append(dict(name="Outer carrier pipe", outer_mm=od, color="#3C4750", note="12 mm"))
    if item.item_id == "fl_deh":
        layers.append(dict(name="DEH piggyback cable", outer_mm=od, color="#C4561B", note="riser/piggyback"))
    return layers


def scale_rates(wells: Dict[str, WellFA], fraction: float) -> Dict[str, WellFA]:
    out = {}
    for k, w in wells.items():
        w2 = WellFA(**{f: getattr(w, f) for f in WellFA.__dataclass_fields__})
        w2.oil_sm3_d = w.oil_sm3_d * fraction
        out[k] = w2
    return out


def rate_sensitivity(layout, settings: Optional[FASettings] = None, wells: Optional[Dict[str, WellFA]] = None,
                     fractions=(1.0, 0.7, 0.5, 0.3)) -> List[dict]:
    """Turndown check: the same layout at reduced rates.

    Reports how arrival conditions, hydrate margin and liquid inventory move with rate.
    `surge_vs_design_m3` is the inventory difference against the design case — positive
    means extra liquid sitting in the line that the host must absorb on ramp-up. Which
    way it goes depends on the fluid: holdup rises as velocity falls, but lower line
    pressure liberates gas and shrinks the liquid volume. This is a screening indicator,
    not a transient simulation.
    """
    s = settings or FASettings()
    wells = wells if wells is not None else well_inputs(layout)
    base_inventory = None
    rows = []
    for f in fractions:
        res = solve(layout, s, scale_rates(wells, f))
        inv = sum(r.liquid_inventory_m3 for r in res.edges.values())
        if base_inventory is None:
            base_inventory = inv
        host = next(iter(res.host.values())) if res.host else {}
        rows.append(dict(
            fraction=f,
            oil_sm3_d=sum(w.oil_sm3_d for w in scale_rates(wells, f).values()),
            arrival_t_c=host.get("arrival_t_c", math.nan),
            max_required_whp_bara=max((w["required_whp_bara"] for w in res.wells), default=math.nan),
            min_hydrate_margin_c=min((r.min_hydrate_margin_c for r in res.edges.values() if not r.heated),
                                     default=math.nan),
            min_cooldown_h=min((r.cooldown_h for r in res.edges.values() if not r.heated), default=math.nan),
            liquid_inventory_m3=inv,
            surge_vs_design_m3=inv - base_inventory,
            slug_risk=any(r.slug_risk for r in res.edges.values()),
            max_velocity_m_s=max((r.max_velocity_m_s for r in res.edges.values()), default=math.nan)))
    return rows


def solve_coupled(layout, settings: Optional[FASettings] = None, wells: Optional[Dict[str, WellFA]] = None,
                  iprs: Optional[Dict[str, "tb_well.IPR"]] = None,
                  tubings: Optional[Dict[str, "tb_well.Tubing"]] = None,
                  iterations: int = 10, damping: float = 0.6, tol_frac: float = 0.01):
    """Nodal solution: rates come from each well's IPR/VLP against the network back-pressure.

    The network is linearised around the current rates (one extra solve at +10 %)
    so every well can find its own operating point cheaply; rates are then damped
    and the network re-solved until they stop moving.
    """
    import tb_well
    s = settings or FASettings()
    wells = dict(wells if wells is not None else well_inputs(layout))
    iprs = iprs or {}
    tubings = tubings or {}
    active = [w for w in wells if w in iprs]
    if not active:
        return solve(layout, s, wells), {w: v.oil_sm3_d for w, v in wells.items()}, dict(
            converged=True, iterations=0, note="No IPR defined — rates taken as entered.")
    history = []
    res = solve(layout, s, wells)
    for it in range(iterations):
        base = {w["well"]: w["required_whp_bara"] for w in res.wells}
        bumped = solve(layout, s, scale_rates(wells, 1.1))
        bump = {w["well"]: w["required_whp_bara"] for w in bumped.wells}
        moved = 0.0
        for w in active:
            q0 = wells[w].oil_sm3_d
            if q0 <= 0 or w not in base:
                continue
            slope = (bump.get(w, base[w]) - base[w]) / max(0.1 * q0, 1e-9)      # bara per Sm³/d
            whp0, q0_stb = base[w], q0 * SM3_TO_STB

            def required(q_stb, _s=slope, _w=whp0, _q0=q0_stb):
                return (_w + _s * (q_stb - _q0) / SM3_TO_STB) * BARA_TO_PSIA

            op = tb_well.operating_point(wells[w].fluid(), iprs[w], tubings.get(w, tb_well.Tubing()),
                                         required, water_cut=wells[w].water_cut)
            q_new_sm3 = op["rate_stb_d"] / SM3_TO_STB
            q_damped = q0 + damping * (q_new_sm3 - q0)
            moved = max(moved, abs(q_damped - q0) / max(q0, 1e-9))
            wells[w].oil_sm3_d = max(q_damped, 0.0)
        res = solve(layout, s, wells)
        history.append({w: wells[w].oil_sm3_d for w in active})
        if moved < tol_frac:
            return res, {w: v.oil_sm3_d for w, v in wells.items()}, dict(
                converged=True, iterations=it + 1, note="", history=history)
    return res, {w: v.oil_sm3_d for w, v in wells.items()}, dict(
        converged=False, iterations=iterations,
        note=f"Rates still moving by more than {tol_frac:.0%} — check the IPR and tubing inputs.",
        history=history)


def diameter_sweep(layout, edge_id: str, diameters_in, settings: Optional[FASettings] = None) -> List[dict]:
    """Required worst-case WHP and arrival temperature vs. one line's diameter.

    A size the field cannot flow through gives a row of NaN with a note, rather
    than stopping the sweep: that is the answer for that size.
    """
    rows = []
    wells = well_inputs(layout)
    for d in diameters_in:
        try:
            res = solve(layout, settings, wells, {edge_id: float(d)})
        except Exception as exc:  # noqa: BLE001 — one impossible size must not lose the others
            rows.append(dict(diameter_in=float(d), max_required_whp_bara=math.nan,
                             worst_shortfall_bar=math.nan, edge_t_out_c=math.nan, edge_dp_bar=math.nan,
                             erosional_ratio=math.nan, note=f"no solution at this size ({exc})"))
            continue
        worst = max((w["required_whp_bara"] - w["available_whp_bara"] for w in res.wells), default=math.nan)
        req = max((w["required_whp_bara"] for w in res.wells), default=math.nan)
        er = res.edges.get(edge_id)
        capped = any(f[1] == edge_id and "too small for" in f[2] for f in res.findings)
        rows.append(dict(diameter_in=float(d), max_required_whp_bara=req, worst_shortfall_bar=worst,
                         edge_t_out_c=er.t_out_c if er else math.nan,
                         edge_dp_bar=(er.p_in_bara - er.p_out_bara) if er else math.nan,
                         erosional_ratio=er.erosional_ratio if er else math.nan,
                         note=(f"pressure capped at {MAX_MARCH_BARA:,.0f} bara — too small at this rate"
                               if capped else "")))
    return rows
