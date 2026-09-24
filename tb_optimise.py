"""
tb_optimise.py — Search the concept space instead of sweeping it by hand.

The app can cost, schedule, solve and screen one concept. This module takes the
concept on screen as the base case and builds the obvious variants around it:

* **number of producers** — clone or drop wells on the main structure, keeping
  slot capacity honest (a 4-slot template becomes a 6-slot one when it has to);
* **flowline size** — the same bore on every production line;
* **one line or a loop** — a second line between the same ends, which is what
  round-trip pigging and a dead-leg-free restart actually cost;
* **boosting** — a multiphase station spliced into the main line, with power
  from the host.

Each variant is costed, scheduled, solved for flow assurance, given a
production profile and an NPV, and comes back as one row. `pareto` then picks
the ones nothing else beats on both cost and value, which is the short list
worth drawing.

This is a **screening search, not an optimisation**: a few dozen variants of
one topology, scored by the same simplified models as the rest of the app. It
will not invent a layout, move a host, or tell you the field is uneconomic. It
narrows five hundred combinations down to the three worth an engineer's
afternoon.
"""
from __future__ import annotations

import copy
import itertools
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import tb_cost
import tb_economics as ec
import tb_flowassurance as tb_fa
import tb_network as net
import tb_production as pr
import tb_schedule

BOOST_ITEM = "mpp_2x"
POWER_ITEM = "pwr_cable"


@dataclass
class SearchSpec:
    """What to vary. An empty list means "leave it as the base case has it"."""
    well_counts: List[int] = field(default_factory=list)          # e.g. [3, 4, 5, 6]
    diameters_in: List[float] = field(default_factory=list)       # e.g. [8, 10, 12, 14]
    loop: List[bool] = field(default_factory=lambda: [False])
    boosting: List[bool] = field(default_factory=lambda: [False])
    max_variants: int = 60

    def combinations(self, base_wells: int, base_dia: float) -> List[dict]:
        # the base case is always in the set, so every variant has something to be compared with
        wells = sorted(set((self.well_counts or [base_wells]) + [base_wells]))
        dias = sorted(set((self.diameters_in or [base_dia]) + [base_dia]))
        out = [dict(wells=w, diameter_in=d, loop=l, boosting=b)
               for w, d, l, b in itertools.product(wells, dias, self.loop or [False],
                                                   self.boosting or [False])]
        return out[: max(1, self.max_variants)]


# ───────────────────────────── building a variant ──────────────────────────

def production_lines(layout) -> List[str]:
    cat = layout.catalog
    return [e.edge_id for e in layout.edges.values()
            if cat.get(e.item_id).category in ("flowline", "riser")]


def main_line(layout) -> Optional[str]:
    """The longest flowline — the one a loop or a booster is about."""
    cat = layout.catalog
    fl = [(layout.edge_length(e), e.edge_id) for e in layout.edges.values()
          if cat.get(e.item_id).category == "flowline"]
    return max(fl)[1] if fl else None


def producers(layout) -> List[str]:
    import tb_fluids
    return sorted(w for w in layout.nodes if layout.kind(w) == "well"
                  and not tb_fluids.is_injector(layout, w))


def set_diameter(layout, diameter_in: float):
    cat = layout.catalog
    for e in layout.edges.values():
        it = cat.get(e.item_id)
        if it.category in ("flowline", "riser") and e.diameter_in:
            e.diameter_in = float(min(max(diameter_in, it.min_diameter_in or diameter_in),
                                      it.max_diameter_in or diameter_in))


def add_loop(layout, edge_id: Optional[str] = None) -> Optional[str]:
    """A second line beside the main one, same ends, same size: the loop for round-trip pigging."""
    eid = edge_id or main_line(layout)
    if not eid or eid not in layout.edges:
        return None
    e = layout.edges[eid]
    new = f"{eid}_B"
    i = 2
    while new in layout.edges or new in layout.nodes:
        new, i = f"{eid}_B{i}", i + 1
    layout.add_edge(net.Edge(new, e.item_id, e.from_node, e.to_node, diameter_in=e.diameter_in,
                             route=[(la + 0.0015, lo) for la, lo in e.route], length_m=e.length_m,
                             phase=e.phase, label=f"{e.label or eid} (loop)",
                             attrs={"from_optimiser": "loop"}))
    return new


def add_boosting(layout, edge_id: Optional[str] = None, item_id: str = BOOST_ITEM) -> Optional[str]:
    """Splice a boosting station into the upstream end of the main line, powered from the host."""
    eid = edge_id or main_line(layout)
    if not eid or eid not in layout.edges or item_id not in layout.catalog.items:
        return None
    e = layout.edges[eid]
    up = e.from_node
    if layout.kind(up) == "host":                       # the line runs the other way round
        up, e.from_node, e.to_node = e.to_node, e.to_node, e.from_node
    un = layout.nodes[up]
    bid = "BOOST"
    i = 2
    while bid in layout.nodes or bid in layout.edges:
        bid, i = f"BOOST{i}", i + 1
    layout.add_node(net.Node(bid, item_id, un.lat + 0.002, un.lon + 0.002, label="Boosting station",
                             water_depth_m=un.water_depth_m, phase=un.phase,
                             attrs={"from_optimiser": "boosting"}))
    e.from_node = bid                                   # the main line now starts at the station
    e.route = []
    jid = f"J_{bid}"
    layout.add_edge(net.Edge(jid, "jumper_rigid", up, bid, phase=un.phase,
                             attrs={"from_optimiser": "boosting"}))
    hosts = [n for n in layout.nodes if layout.kind(n) == "host"]
    if hosts:
        pid = f"PC_{bid}"
        layout.add_edge(net.Edge(pid, POWER_ITEM, hosts[0], bid, phase=un.phase,
                                 attrs={"from_optimiser": "boosting"}))
    return bid


def set_well_count(layout, target: int, rates: Optional[Dict[str, float]] = None) -> dict:
    """Clone or drop producers until there are `target` of them.

    A clone sits 250 m from its twin, carries the same tree, reservoir, fluid and
    flow-assurance inputs, and is jumpered into the same structure. If that
    structure runs out of slots, it is upgraded to the next size in the
    catalogue rather than quietly breaking the slot check.
    """
    import tb_fluids
    ps = producers(layout)
    if not ps or target <= 0:
        return dict(wells=len(ps), added=[], removed=[], note="no producers to work from")
    added, removed = [], []
    while len(ps) > target:
        drop = ps[-1]
        layout.remove_node(drop)
        removed.append(drop)
        ps = producers(layout)
    while len(ps) < target:
        src = layout.nodes[ps[-1]]
        struct = src.attrs.get("in_structure") or _jumper_structure(layout, src.node_id)
        if struct and layout.free_slots(struct) <= 0:
            _upgrade_slots(layout, struct)
        n_ = len(ps) + 1
        wid = f"W{n_}"
        i = n_
        while wid in layout.nodes or wid in layout.edges:
            i += 1
            wid = f"W{i}"
        layout.add_node(net.Node(wid, src.item_id, src.lat + 0.0022, src.lon + 0.0022,
                                 label=wid, water_depth_m=src.water_depth_m, sitp_psi=src.sitp_psi,
                                 phase=src.phase, attrs={**copy.deepcopy(src.attrs),
                                                         "from_optimiser": "well"}))
        if struct:
            layout.nodes[wid].attrs["in_structure"] = struct
            jid = f"J_{wid}"
            j = 2
            while jid in layout.edges or jid in layout.nodes:
                jid, j = f"J_{wid}_{j}", j + 1
            layout.add_edge(net.Edge(jid, layout.SLOT_ITEM if layout.nodes[wid].attrs.get("in_structure")
                                     else "jumper_rigid", wid, struct, phase=src.phase,
                                     attrs={"from_optimiser": "well"}))
        added.append(wid)
        ps = producers(layout)
    return dict(wells=len(ps), added=added, removed=removed, note="")


def _jumper_structure(layout, well_id: str) -> Optional[str]:
    for e in layout.edges.values():
        if layout.catalog.get(e.item_id).category != "jumper":
            continue
        if well_id in (e.from_node, e.to_node):
            other = e.to_node if e.from_node == well_id else e.from_node
            if layout.kind(other) in ("template", "manifold", "plem", "plet", "boosting"):
                return other
    return None


def _upgrade_slots(layout, structure_id: str) -> bool:
    """Swap a full structure for the next bigger one of the same kind in the catalogue."""
    cat = layout.catalog
    cur = cat.get(layout.nodes[structure_id].item_id)
    bigger = sorted((i for i in cat.items.values()
                     if i.category == cur.category and i.slots > cur.slots), key=lambda i: i.slots)
    if not bigger:
        return False
    layout.nodes[structure_id].item_id = bigger[0].item_id
    return True


def build_variant(base_layout, option: dict, rates: Optional[Dict[str, float]] = None):
    """A deep copy of the base layout with this option's changes applied."""
    lay = net.Layout.from_dict(copy.deepcopy(base_layout.to_dict()),
                               base_layout.catalog.__class__.from_dict(base_layout.catalog.to_dict()))
    lay.reservoirs = copy.deepcopy(getattr(base_layout, "reservoirs", {}) or {})
    changes = []
    if option.get("wells"):
        r = set_well_count(lay, int(option["wells"]))
        if r["added"]:
            changes.append(f"+{len(r['added'])} well(s)")
        if r["removed"]:
            changes.append(f"−{len(r['removed'])} well(s)")
    if option.get("diameter_in"):
        set_diameter(lay, float(option["diameter_in"]))
        changes.append(f'{option["diameter_in"]:.0f}" lines')
    if option.get("loop"):
        if add_loop(lay):
            changes.append("looped")
    if option.get("boosting"):
        if add_boosting(lay):
            changes.append("boosting")
    return lay, ", ".join(changes) or "base case"


# ──────────────────────────────── evaluation ───────────────────────────────

def evaluate(layout, cost_settings=None, sched_settings=None, fa_settings=None,
             profile_settings=None, econ_settings=None, per_well_rate_sm3_d: float = 0.0) -> dict:
    """Cost, schedule, flow assurance, production and NPV for one layout."""
    row = dict(wells=len(producers(layout)), errors=0, warnings=0, note="")
    findings = layout.validate()
    row["errors"] = sum(f.severity == "error" for f in findings)
    row["warnings"] = sum(f.severity == "warning" for f in findings)
    est = tb_cost.estimate(layout, cost_settings)
    row["capex_musd"] = est["total_usd"] / 1e6
    annual = {}
    try:
        sched, emap = tb_schedule.build_from_layout(layout, sched_settings)
        fo = sorted([a for a in sched.activities.values() if a.act_id.endswith("_FIRST_OIL")],
                    key=lambda a: a.es)
        row["first_production"] = fo[0].es.isoformat() if fo else ""
        annual = tb_cost.phase_costs(est, sched, emap, cost_settings)["annual"]
    except Exception as exc:  # noqa: BLE001 — an unschedulable variant still has a cost
        row["first_production"] = ""
        row["note"] = f"not scheduled ({exc})"
    # flow assurance: give every well the same design rate so the comparison is about the layout
    wells_in = tb_fa.well_inputs(layout)
    if per_well_rate_sm3_d > 0:
        for w, v in wells_in.items():
            if layout.kind(w) == "well":
                v.oil_sm3_d = per_well_rate_sm3_d
                tb_fa.set_well_inputs(layout, w, v)
        wells_in = tb_fa.well_inputs(layout)
    try:
        res = tb_fa.solve(layout, fa_settings, wells_in)
        row["deliverable"] = all(w["deliverable"] for w in res.wells) if res.wells else None
        row["worst_margin_bar"] = min((w["margin_bar"] for w in res.wells), default=math.nan)
        margins = [r.min_hydrate_margin_c for r in res.edges.values() if not r.heated]
        row["hydrate_margin_c"] = min(margins) if margins else math.nan
        row["max_erosional"] = max((r.erosional_ratio for r in res.edges.values()), default=math.nan)
        row["capped"] = any("too small for" in f[2] for f in res.findings)
    except Exception as exc:  # noqa: BLE001
        row.update(deliverable=None, worst_margin_bar=math.nan, hydrate_margin_c=math.nan,
                   max_erosional=math.nan, capped=True)
        row["note"] = (row["note"] + f" flow solve failed ({exc})").strip()
    fp = pr.field_profile(layout, profile_settings, fa_settings)
    row["plateau_sm3_d"] = sum(s["plateau_sm3_d"] for s in fp["streams"])
    row["recoverable_msm3_oe"] = fp["total_boe_sm3"] / 1e6
    row["field_life_years"] = (fp["last_year"] - fp["first_year"] + 1) if fp["years"] else 0
    cf = ec.cashflow(fp["years"], annual, econ_settings)
    row["npv_musd"] = cf["npv_usd"] / 1e6
    row["breakeven_usd_bbl"] = cf.get("breakeven_oil_usd_bbl")
    row["capex_usd_boe"] = cf.get("capex_usd_boe")
    row["unit_cost_usd_boe"] = cf.get("unit_technical_cost_usd_boe")
    row["feasible"] = bool(row["errors"] == 0 and row.get("deliverable") is not False
                           and not row.get("capped"))
    return row


def search(base_layout, spec: SearchSpec, cost_settings=None, sched_settings=None, fa_settings=None,
           profile_settings=None, econ_settings=None, per_well_rate_sm3_d: float = 0.0,
           progress=None) -> List[dict]:
    """Every combination in the spec, evaluated and ranked by NPV (then by CAPEX)."""
    base_wells = len(producers(base_layout))
    dias = [e.diameter_in for e in base_layout.edges.values()
            if base_layout.catalog.get(e.item_id).category == "flowline" and e.diameter_in]
    base_dia = max(dias) if dias else 10.0
    if per_well_rate_sm3_d <= 0:
        w_in = tb_fa.well_inputs(base_layout)
        ps = producers(base_layout)
        per_well_rate_sm3_d = (sum(w_in[w].oil_sm3_d for w in ps if w in w_in) / len(ps)) if ps else 0.0
    combos = spec.combinations(base_wells, base_dia)
    rows = []
    for i, opt in enumerate(combos):
        if progress:
            progress(i / max(len(combos), 1), f"{opt['wells']} wells · {opt['diameter_in']:.0f}\"")
        try:
            lay, label = build_variant(base_layout, opt)
            row = evaluate(lay, cost_settings, sched_settings, fa_settings, profile_settings,
                           econ_settings, per_well_rate_sm3_d)
        except Exception as exc:  # noqa: BLE001 — one impossible variant must not stop the search
            rows.append(dict(opt, label="failed", note=str(exc)[:120], capex_musd=math.nan,
                             npv_musd=math.nan, errors=1))
            continue
        row.update(opt)
        row["label"] = label
        row["is_base"] = (opt["wells"] == base_wells and abs(opt["diameter_in"] - base_dia) < 1e-9
                          and not opt["loop"] and not opt["boosting"])
        rows.append(row)
    # infeasible variants stay in the table — you want to see why — but below the ones that work
    rows.sort(key=lambda r: (not r.get("feasible", False),
                             -(r.get("npv_musd") if r.get("npv_musd") == r.get("npv_musd") else -1e9),
                             r.get("capex_musd", 1e9)))
    return rows


def pareto(rows: List[dict], value: str = "npv_musd", cost: str = "capex_musd") -> List[dict]:
    """The rows nothing else beats on both value and cost."""
    live = [r for r in rows if r.get(value) == r.get(value) and r.get(cost) == r.get(cost)
            and r.get("feasible", not r.get("errors"))]
    out = []
    for r in live:
        if not any(o is not r and o[value] >= r[value] and o[cost] <= r[cost]
                   and (o[value] > r[value] or o[cost] < r[cost]) for o in live):
            out.append(r)
    return sorted(out, key=lambda r: r[cost])


def explain(row: dict, base: Optional[dict] = None) -> str:
    """One line a reviewer can read: what this variant is, and what it buys."""
    bits = [f"{row.get('wells', '?')} wells", f'{row.get("diameter_in", 0):.0f}" lines']
    if row.get("loop"):
        bits.append("looped")
    if row.get("boosting"):
        bits.append("boosting")
    txt = " · ".join(bits)
    txt += f" → CAPEX {row.get('capex_musd', float('nan')):,.0f} MUSD, NPV {row.get('npv_musd', float('nan')):,.0f} MUSD"
    if row.get("breakeven_usd_bbl"):
        txt += f", break-even {row['breakeven_usd_bbl']:,.0f} USD/bbl"
    if base and base is not row:
        d_npv = row.get("npv_musd", math.nan) - base.get("npv_musd", math.nan)
        d_cap = row.get("capex_musd", math.nan) - base.get("capex_musd", math.nan)
        txt += f" ({d_npv:+,.0f} MUSD NPV and {d_cap:+,.0f} MUSD CAPEX against the base case)"
    if row.get("errors"):
        txt += f" — {row['errors']} design error(s)"
    if row.get("capped"):
        txt += " — the line is too small at this rate"
    elif row.get("deliverable") is False:
        txt += " — the wells cannot deliver against this back-pressure"
    return txt
