"""
tb_fluids.py — What comes out of each reservoir and each well.

A concept is not only a network of pipes: it matters whether a well produces
oil, gas or condensate, and whether it is a producer at all. That one fact
changes the default PVT, the colour a line is drawn in, the hydrate duty, the
host capacity it loads and the production chemistry it brings with it.

Three layers, most specific first:

1. **Set on the well** — `node.attrs["well_fluid"]`, the engineer's call.
2. **From its reservoir** — `node.attrs["reservoir"]` names one of the layout's
   reservoirs, which carries the fluid type and its PVT.
3. **From the numbers** — classified from the well's own GOR and gravity using
   McCain's producing-GOR ranges.

`well_fluid` returns the answer *and* which layer it came from, so the app can
say "gas, from GOR" rather than presenting an inference as a decision.

Classification (McCain, *The Properties of Petroleum Fluids*, initial producing GOR):

    black oil        < 1 750 scf/STB   (<  312 Sm³/Sm³)
    volatile oil     1 750 – 3 200     (312 – 570)
    gas condensate   3 200 – 15 000    (570 – 2 670)
    wet gas          15 000 – 100 000  (2 670 – 17 800)
    dry gas          > 100 000         (> 17 800)

These bands overlap in practice — a rich condensate and a volatile oil can share
a GOR — so a classification from GOR alone is a starting point for the PVT
report, not a substitute for it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Dict, List, Optional, Tuple

SCF_STB_PER_SM3_SM3 = 5.6146

# What a well does.
WELL_FLUIDS = ("oil", "gas", "gas condensate", "water injector", "gas injector")
PRODUCER_FLUIDS = ("oil", "gas", "gas condensate")
INJECTOR_FLUIDS = ("water injector", "gas injector")

UNASSIGNED = "default — not assigned"

# What a reservoir holds.
RESERVOIR_FLUIDS = ("black oil", "volatile oil", "gas condensate", "wet gas", "dry gas")

# McCain producing-GOR boundaries, Sm³/Sm³.
GOR_BOUNDS = [(1750 / SCF_STB_PER_SM3_SM3, "black oil"),
              (3200 / SCF_STB_PER_SM3_SM3, "volatile oil"),
              (15000 / SCF_STB_PER_SM3_SM3, "gas condensate"),
              (100000 / SCF_STB_PER_SM3_SM3, "wet gas")]

RESERVOIR_TO_WELL = {"black oil": "oil", "volatile oil": "oil", "gas condensate": "gas condensate",
                     "wet gas": "gas", "dry gas": "gas"}

# NCS convention (Sodir): oil green, gas red; condensate between them.
FLUID_COLORS = {"oil": "#1E7A3C", "gas": "#EB0037", "gas condensate": "#E9A23B",
                "water injector": "#2E86AB", "gas injector": "#7D4EBF"}

# What the line carrying this well's stream is drawn as (tb_map fluid names).
WELL_TO_LINE = {"oil": "oil", "gas": "gas", "gas condensate": "condensate",
                "water injector": "water injection", "gas injector": "gas lift"}


def classify(gor_sm3_sm3: float, api: Optional[float] = None) -> str:
    """Reservoir fluid type from producing GOR (McCain), with gravity as a tie-break.

    At the black-oil / volatile-oil boundary a heavy oil (< 40° API) stays a black
    oil — McCain notes volatile oils are typically above 40° API.
    """
    if gor_sm3_sm3 is None or gor_sm3_sm3 < 0:
        raise ValueError("GOR must be >= 0")
    kind = "dry gas"
    for bound, name in GOR_BOUNDS:
        if gor_sm3_sm3 < bound:
            kind = name
            break
    if kind == "volatile oil" and api is not None and api < 40:
        kind = "black oil"
    return kind


def well_fluid_from_reservoir_fluid(res_fluid: str) -> str:
    if res_fluid not in RESERVOIR_TO_WELL:
        raise ValueError(f"unknown reservoir fluid '{res_fluid}'")
    return RESERVOIR_TO_WELL[res_fluid]


# ───────────────────────────────── reservoirs ──────────────────────────────

@dataclass
class Reservoir:
    """One reservoir or fluid region, and what it produces."""
    name: str
    fluid: str = "black oil"
    gor_sm3_sm3: float = 150.0
    api: float = 35.0
    gas_sg: float = 0.75
    water_cut: float = 0.10
    salinity_wt_pct: float = 3.5
    pres_bara: float = 300.0          # initial reservoir pressure
    tres_c: float = 100.0             # reservoir temperature
    co2_mol_pct: float = 0.0
    h2s_ppm: float = 0.0
    mercury_ug_nm3: float = 0.0
    # volumetrics and drainage strategy (tb_production); 0 = not filled in
    area_km2: float = 0.0
    thickness_m: float = 0.0
    ntg: float = 0.70
    porosity: float = 0.22
    water_saturation: float = 0.25
    fvf: float = 0.0                  # Bo (rm³/Sm³) for oil, Bg for gas; 0 = typical for the fluid
    drive: str = ""                   # tb_production.DRIVES; "" = the usual one for this fluid
    recovery_factor: float = 0.0      # 0 = use the suggested one
    notes: str = ""

    def __post_init__(self):
        if not str(self.name).strip():
            raise ValueError("a reservoir needs a name")
        if self.fluid not in RESERVOIR_FLUIDS:
            raise ValueError(f"unknown reservoir fluid '{self.fluid}'")
        if self.gor_sm3_sm3 < 0:
            raise ValueError("GOR must be >= 0")
        if not 0 <= self.water_cut < 1:
            raise ValueError("water cut must be in [0, 1)")

    @property
    def well_fluid(self) -> str:
        return RESERVOIR_TO_WELL[self.fluid]

    def classified(self) -> str:
        """What the GOR says this is — to compare with what was entered."""
        return classify(self.gor_sm3_sm3, self.api)

    def consistent(self) -> bool:
        """False when the stated fluid and the GOR point to different families.

        A black oil entered with a 5 000 Sm³/Sm³ GOR is almost certainly a typo,
        and it would drive every flow-assurance number the wrong way.
        """
        return RESERVOIR_TO_WELL[self.classified()] == RESERVOIR_TO_WELL[self.fluid]


# Starting points for a new reservoir of each type — typical NCS values, to be
# replaced from the PVT report.
RESERVOIR_PRESETS: Dict[str, dict] = {
    "black oil":      dict(gor_sm3_sm3=120.0, api=32.0, gas_sg=0.78, water_cut=0.10, pres_bara=280.0, tres_c=95.0),
    "volatile oil":   dict(gor_sm3_sm3=420.0, api=44.0, gas_sg=0.74, water_cut=0.05, pres_bara=380.0, tres_c=120.0),
    "gas condensate": dict(gor_sm3_sm3=1600.0, api=52.0, gas_sg=0.70, water_cut=0.02, pres_bara=420.0, tres_c=140.0),
    "wet gas":        dict(gor_sm3_sm3=6000.0, api=58.0, gas_sg=0.66, water_cut=0.02, pres_bara=350.0, tres_c=110.0),
    "dry gas":        dict(gor_sm3_sm3=40000.0, api=60.0, gas_sg=0.60, water_cut=0.01, pres_bara=300.0, tres_c=90.0),
}


def new_reservoir(name: str, fluid: str = "black oil") -> Reservoir:
    if fluid not in RESERVOIR_PRESETS:
        raise ValueError(f"unknown reservoir fluid '{fluid}'")
    return Reservoir(name=name, fluid=fluid, **RESERVOIR_PRESETS[fluid])


def reservoirs(layout) -> Dict[str, Reservoir]:
    """The layout's reservoirs. Stored as plain dicts so they round-trip in YAML."""
    raw = getattr(layout, "reservoirs", None) or {}
    out = {}
    known = {f.name for f in fields(Reservoir)}
    for name, d in raw.items():
        try:
            out[name] = Reservoir(**{k: v for k, v in dict(d).items() if k in known})
        except (TypeError, ValueError):
            continue          # one bad record must not hide the others
    return out


def set_reservoir(layout, res: Reservoir):
    if not hasattr(layout, "reservoirs") or layout.reservoirs is None:
        layout.reservoirs = {}
    layout.reservoirs[res.name] = asdict(res)


def remove_reservoir(layout, name: str) -> List[str]:
    """Delete a reservoir and detach the wells that pointed at it. Returns those wells."""
    (getattr(layout, "reservoirs", None) or {}).pop(name, None)
    freed = []
    for n in layout.nodes.values():
        if n.attrs.get("reservoir") == name:
            n.attrs.pop("reservoir", None)
            freed.append(n.node_id)
    return freed


def assign_reservoir(layout, well_ids, name: Optional[str]) -> List[str]:
    """Point wells at a reservoir (None detaches). Returns the wells changed."""
    if name is not None and name not in (getattr(layout, "reservoirs", None) or {}):
        raise ValueError(f"no reservoir named '{name}'")
    changed = []
    for wid in well_ids:
        n = layout.nodes.get(wid)
        if n is None or layout.kind(wid) != "well":
            continue
        if name is None:
            n.attrs.pop("reservoir", None)
        else:
            n.attrs["reservoir"] = name
        changed.append(wid)
    return changed


# ─────────────────────────────────── wells ─────────────────────────────────

def well_fluid(layout, node_id: str) -> Tuple[str, str]:
    """(fluid, where it came from) for one well."""
    n = layout.nodes[node_id]
    stated = str(n.attrs.get("well_fluid") or "").strip().lower()
    if stated in WELL_FLUIDS:
        return stated, "set on the well"
    rname = n.attrs.get("reservoir")
    if rname:
        res = reservoirs(layout).get(rname)
        if res is not None:
            return res.well_fluid, f"from reservoir {rname}"
    fa = n.attrs.get("fa") or {}
    if "gor_sm3_sm3" in fa:
        try:
            kind = classify(float(fa["gor_sm3_sm3"]), fa.get("api"))
            return RESERVOIR_TO_WELL[kind], f"from GOR ({kind})"
        except (TypeError, ValueError):
            pass
    return "oil", UNASSIGNED


def is_assigned(source: str) -> bool:
    """True when the fluid is known (stated, reservoir or GOR), not a placeholder."""
    return source != UNASSIGNED


def set_well_fluid(layout, well_ids, fluid: Optional[str]) -> List[str]:
    """State the main fluid on wells (None clears it back to reservoir / GOR)."""
    if fluid is not None and fluid not in WELL_FLUIDS:
        raise ValueError(f"unknown well fluid '{fluid}'")
    changed = []
    for wid in well_ids:
        n = layout.nodes.get(wid)
        if n is None or layout.kind(wid) != "well":
            continue
        if fluid is None:
            n.attrs.pop("well_fluid", None)
        else:
            n.attrs["well_fluid"] = fluid
        changed.append(wid)
    return changed


def is_injector(layout, node_id: str) -> bool:
    return well_fluid(layout, node_id)[0] in INJECTOR_FLUIDS


def fluid_table(layout) -> List[dict]:
    """One row per well: what it produces, why we think so, and its reservoir."""
    rows = []
    res = reservoirs(layout)
    for nid, n in layout.nodes.items():
        if layout.kind(nid) != "well":
            continue
        fl, src = well_fluid(layout, nid)
        rname = n.attrs.get("reservoir") or ""
        rows.append(dict(id=nid, label=n.label or nid, fluid=fl, source=src, reservoir=rname,
                         reservoir_fluid=res[rname].fluid if rname in res else ""))
    return rows


# ─────────────────── the fluid a line carries, from its wells ─────────────

def wells_by_edge(layout, kinds=None) -> Dict[str, List[str]]:
    """{edge_id: [wells whose route to the host runs through it]}."""
    import tb_network as net
    kinds = kinds or net.PRODUCTION_EDGES
    out: Dict[str, List[str]] = {}
    for nid in layout.nodes:
        if layout.kind(nid) != "well":
            continue
        path = layout.path_to_host(nid, kinds)
        if not path:
            continue
        for eid in path[1]:
            out.setdefault(eid, []).append(nid)
    return out


def line_fluid(layout, edge, edge_wells: Optional[Dict[str, List[str]]] = None) -> Optional[str]:
    """What a production line carries, judged by the wells upstream of it.

    One fluid family → that fluid; oil and gas wells commingled → multiphase;
    no wells upstream → None (the caller keeps its catalogue default).
    Injector wells are left out — their lines are service lines, drawn by type.
    """
    wells = (edge_wells if edge_wells is not None else wells_by_edge(layout)).get(edge.edge_id, [])
    # A well nobody has characterised must not colour its line: that would state
    # a fluid the engineer never chose. Only known fluids count.
    known = [well_fluid(layout, w) for w in wells]
    kinds = {fl for fl, src in known if is_assigned(src)}
    kinds -= set(INJECTOR_FLUIDS)
    if not kinds:
        return None
    if len(kinds) == 1:
        return WELL_TO_LINE[next(iter(kinds))]
    return "multiphase"


# ───────────────────── presets for the flow-assurance inputs ───────────────

def wellfa_from_reservoir(res: Reservoir, rate_sm3_d: Optional[float] = None,
                          wht_c: Optional[float] = None, max_whp_bara: Optional[float] = None) -> dict:
    """Flow-assurance inputs for a well in this reservoir (tb_flowassurance.WellFA fields).

    For gas and condensate wells `rate_sm3_d` is still the liquid (condensate)
    rate, because that is what WellFA is keyed on; use `gas_well_rates` to get
    it from a gas rate and CGR.
    """
    return dict(oil_sm3_d=float(rate_sm3_d if rate_sm3_d is not None else default_liquid_rate(res)),
                water_cut=float(res.water_cut), gor_sm3_sm3=float(res.gor_sm3_sm3),
                api=float(res.api), gas_sg=float(res.gas_sg),
                salinity_wt_pct=float(res.salinity_wt_pct),
                wht_c=float(wht_c if wht_c is not None else max(20.0, res.tres_c * 0.7)),
                max_whp_bara=float(max_whp_bara if max_whp_bara is not None else max(30.0, res.pres_bara * 0.5)))


# Typical NCS well rates, in each fluid's own basis: oil wells are specified by
# oil rate, gas and condensate wells by gas rate.
DEFAULT_OIL_RATE_SM3_D = 1500.0
DEFAULT_GAS_RATE_MSM3_D = 1.5


def family(well_fluid: str) -> str:
    """'oil' or 'gas' — condensate is a gas well: it is specified and sized by gas rate."""
    return "oil" if well_fluid == "oil" else ("gas" if well_fluid in ("gas", "gas condensate") else "injector")


def default_liquid_rate(res: Reservoir) -> float:
    """The liquid rate WellFA is keyed on, from a typical rate in the fluid's own basis."""
    if res.well_fluid == "oil":
        return DEFAULT_OIL_RATE_SM3_D
    return DEFAULT_GAS_RATE_MSM3_D * 1e6 / max(res.gor_sm3_sm3, 1.0)


def gas_well_rates(gas_msm3_d: float, cgr_sm3_per_msm3: float) -> Tuple[float, float]:
    """(condensate Sm³/d, GOR Sm³/Sm³) from a gas rate and a condensate-gas ratio.

    Gas wells are thought of in MSm³/d of gas; the network model is keyed on the
    liquid rate. CGR is in Sm³ of condensate per million Sm³ of gas, as on the NCS.
    """
    if gas_msm3_d < 0 or cgr_sm3_per_msm3 < 0:
        raise ValueError("gas rate and CGR must be >= 0")
    if cgr_sm3_per_msm3 == 0:
        raise ValueError("a CGR of zero has no liquid to key the model on — use a small CGR")
    cond = gas_msm3_d * cgr_sm3_per_msm3
    gor = 1e6 / cgr_sm3_per_msm3
    return cond, gor


def cgr_from(oil_sm3_d: float, gor_sm3_sm3: float) -> Tuple[float, float]:
    """(gas MSm³/d, CGR Sm³/MSm³) — the inverse of gas_well_rates."""
    gas = oil_sm3_d * gor_sm3_sm3 / 1e6
    cgr = 1e6 / gor_sm3_sm3 if gor_sm3_sm3 > 0 else float("inf")
    return gas, cgr


def apply_reservoir_to_wells(layout, well_ids=None, keep_rates: bool = True) -> Dict[str, List[str]]:
    """Copy each well's reservoir PVT into its flow-assurance inputs.

    With `keep_rates` the well's own rate and wellhead conditions are kept — but
    only while the well stays in the same fluid family. An oil well moved onto a
    gas reservoir is a different well: carrying its liquid rate across with a gas
    GOR would give a gas rate out by an order of magnitude either way, so its
    rate is reset to a typical rate for the new fluid and reported.

    Returns {"updated": [...], "rate_reset": [...]}.
    """
    import tb_flowassurance as tb_fa
    res = reservoirs(layout)
    current = tb_fa.well_inputs(layout)
    updated, reset = [], []
    targets = well_ids if well_ids is not None else [n for n in layout.nodes if layout.kind(n) == "well"]
    for wid in targets:
        n = layout.nodes.get(wid)
        rname = n.attrs.get("reservoir") if n else None
        if not rname or rname not in res or is_injector(layout, wid):
            continue
        cur = current.get(wid)
        had_inputs = "fa" in n.attrs
        was = classify(cur.gor_sm3_sm3, cur.api) if (had_inputs and cur) else None
        same_family = was is not None and family(RESERVOIR_TO_WELL[was]) == family(res[rname].well_fluid)
        keep = keep_rates and had_inputs and cur is not None
        vals = wellfa_from_reservoir(
            res[rname],
            rate_sm3_d=cur.oil_sm3_d if (keep and same_family) else None,
            wht_c=cur.wht_c if keep else None,
            max_whp_bara=cur.max_whp_bara if keep else None)
        tb_fa.set_well_inputs(layout, wid, tb_fa.WellFA(**vals))
        updated.append(wid)
        if keep and not same_family:
            reset.append(wid)
    return {"updated": updated, "rate_reset": reset}


def summary(layout) -> Dict[str, int]:
    """Wells by main fluid."""
    out = {f: 0 for f in WELL_FLUIDS}
    for r in fluid_table(layout):
        out[r["fluid"]] = out.get(r["fluid"], 0) + 1
    return out


def field_type(layout) -> str:
    """One phrase for the development: an oil field, a gas field, or mixed."""
    s = summary(layout)
    oil, gas, cond = s["oil"], s["gas"], s["gas condensate"]
    producers = oil + gas + cond
    if producers == 0:
        return "no producers assigned"
    if oil and not (gas or cond):
        return "oil development"
    if (gas or cond) and not oil:
        return "gas-condensate development" if cond else "gas development"
    return "mixed oil and gas development"
