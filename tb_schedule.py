"""
tb_schedule.py — CPM scheduling for TieBack Studio.

* Generic CPM: finish-to-start links with lag, forward/backward pass,
  total float, critical path. Calendar days.
* Weather windows: marine activities may be constrained to a seasonal window
  (default NCS summer season 1 Apr – 15 Oct). The forward pass snaps an
  activity start so the whole activity fits inside one window.
  Backward pass ignores windows; float on windowed activities is therefore
  indicative and flagged (`window_constrained=True`).
* `build_from_layout` generates a standard tie-back activity network
  (DG milestones → procurement → campaigns → drilling → tie-in → first oil).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DAYS_PER_MONTH = 30.4375


@dataclass
class Window:
    start_mmdd: Tuple[int, int] = (4, 1)
    end_mmdd: Tuple[int, int] = (10, 15)

    def fit(self, earliest: dt.date, duration_days: float) -> dt.date:
        """Earliest start >= `earliest` such that [start, start+dur] lies in one window."""
        dur = dt.timedelta(days=duration_days)
        for year in range(earliest.year - 1, earliest.year + 30):
            ws = dt.date(year, *self.start_mmdd)
            we = dt.date(year, *self.end_mmdd)
            if (we - ws) < dur:
                raise ValueError(f"activity of {duration_days:.0f} d cannot fit in weather window "
                                 f"({(we - ws).days} d) — split the campaign")
            start = max(earliest, ws)
            if start + dur <= we:
                return start
        raise ValueError("no weather window found within 30 years")


@dataclass
class Activity:
    act_id: str
    name: str
    duration_days: float
    predecessors: List[Tuple[str, float]] = field(default_factory=list)  # (act_id, lag_days)
    window: Optional[Window] = None
    group: str = ""
    phase: int = 1
    milestone: bool = False
    fixed_start: Optional[dt.date] = None   # "start no earlier than"
    # results
    es: Optional[dt.date] = None
    ef: Optional[dt.date] = None
    ls: Optional[dt.date] = None
    lf: Optional[dt.date] = None
    total_float_days: float = 0.0
    critical: bool = False

    @property
    def window_constrained(self) -> bool:
        return self.window is not None


class Schedule:
    def __init__(self, start_date: dt.date):
        self.start_date = start_date
        self.activities: Dict[str, Activity] = {}

    def add(self, act: Activity) -> Activity:
        if act.act_id in self.activities:
            raise ValueError(f"duplicate activity '{act.act_id}'")
        if act.duration_days < 0:
            raise ValueError(f"{act.act_id}: negative duration")
        self.activities[act.act_id] = act
        return act

    def _topo(self) -> List[str]:
        indeg = {a: 0 for a in self.activities}
        succ = defaultdict(list)
        for a in self.activities.values():
            for p, _ in a.predecessors:
                if p not in self.activities:
                    raise ValueError(f"{a.act_id}: unknown predecessor '{p}'")
                indeg[a.act_id] += 1
                succ[p].append(a.act_id)
        order, ready = [], [a for a, d in indeg.items() if d == 0]
        while ready:
            u = ready.pop(0)
            order.append(u)
            for v in succ[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    ready.append(v)
        if len(order) != len(self.activities):
            raise ValueError("schedule network contains a cycle")
        return order

    def compute(self) -> "Schedule":
        order = self._topo()
        td = dt.timedelta
        for aid in order:
            a = self.activities[aid]
            es = self.start_date
            for p, lag in a.predecessors:
                es = max(es, self.activities[p].ef + td(days=lag))
            if a.fixed_start and a.fixed_start > es:
                es = a.fixed_start
            if a.window is not None and a.duration_days > 0:
                es = a.window.fit(es, a.duration_days)
            a.es = es
            a.ef = es + td(days=a.duration_days)
        project_end = max(a.ef for a in self.activities.values())
        succ = defaultdict(list)
        for a in self.activities.values():
            for p, lag in a.predecessors:
                succ[p].append((a.act_id, lag))
        for aid in reversed(order):
            a = self.activities[aid]
            lf = project_end
            for s, lag in succ[aid]:
                lf = min(lf, self.activities[s].ls - td(days=lag))
            a.lf = lf
            a.ls = lf - td(days=a.duration_days)
            a.total_float_days = (a.ls - a.es).total_seconds() / 86400.0
            a.critical = a.total_float_days <= 0.5
        return self

    @property
    def finish(self) -> dt.date:
        return max(a.ef for a in self.activities.values())

    def critical_path(self) -> List[str]:
        return [a.act_id for a in sorted(self.activities.values(), key=lambda x: x.es) if a.critical]

    def table(self) -> List[dict]:
        return [dict(act_id=a.act_id, name=a.name, group=a.group, phase=a.phase,
                     start=a.es, finish=a.ef, duration_days=round(a.duration_days, 1),
                     total_float_days=round(a.total_float_days, 1), critical=a.critical,
                     window=a.window_constrained, milestone=a.milestone)
                for a in sorted(self.activities.values(), key=lambda x: (x.es, x.ef))]


# ─────────────────────────── layout-driven network ─────────────────────────

STRUCTURE_KINDS = ("template", "manifold", "plet", "plem", "ilt", "ssiv", "riser_base",
                   "boosting", "compression", "separation")
PIPELAY_KINDS = ("flowline", "riser")
CABLE_KINDS = ("umbilical", "power_cable")


@dataclass
class ScheduleSettings:
    dg2_date: dt.date = dt.date(2027, 1, 1)
    feed_days: float = 300.0             # DG2 -> DG3 / PDO submission
    pdo_approval_days: float = 180.0     # PDO submission -> approval
    award_after_dg3_days: float = 30.0   # main contracts awarded (conditional) after DG3
    drill_days_per_well: float = 55.0    # incl. completion + XT install
    rigs: int = 1
    metrology_days: float = 20.0
    precommissioning_days: float = 30.0
    commissioning_days: float = 45.0
    weather_factor: float = 1.25         # offshore duration multiplier (NCS)
    marine_window: Window = field(default_factory=Window)
    phase_offset_days: Dict[int, float] = field(default_factory=dict)  # phase -> delay of award


def build_from_layout(layout, settings: Optional[ScheduleSettings] = None):
    """Returns (Schedule, element_map) where element_map[element_id] =
    {'procure': act_id, 'install': act_id} for cost phasing."""
    s = settings or ScheduleSettings()
    cat = layout.catalog
    sch = Schedule(s.dg2_date)
    M = DAYS_PER_MONTH
    sch.add(Activity("DG2", "DG2 — concept selected", 0, milestone=True, group="Milestones"))
    sch.add(Activity("FEED", "FEED / pre-engineering", s.feed_days, [("DG2", 0)], group="Engineering"))
    sch.add(Activity("DG3", "DG3 / PDO submitted", 0, [("FEED", 0)], milestone=True, group="Milestones"))
    sch.add(Activity("PDO", "PDO approval", s.pdo_approval_days, [("DG3", 0)], group="Milestones"))
    sch.add(Activity("PDO_OK", "PDO approved (DG4 path)", 0, [("PDO", 0)], milestone=True, group="Milestones"))

    element_map: Dict[str, dict] = {}
    phases = sorted({n.phase for n in layout.nodes.values()} | {e.phase for e in layout.edges.values()}) or [1]
    first_oil_preds: List[Tuple[str, float]] = []

    for ph in phases:
        tag = f"P{ph}"
        award = f"{tag}_AWARD"
        sch.add(Activity(award, f"Phase {ph} — contract award", 0,
                         [("DG3", s.award_after_dg3_days + s.phase_offset_days.get(ph, 0.0))],
                         milestone=True, group="Milestones", phase=ph))

        nodes = [n for n in layout.nodes.values() if n.phase == ph]
        edges = [e for e in layout.edges.values() if e.phase == ph]

        # procurement per catalog item (grouped)
        proc_ids: Dict[str, str] = {}
        by_item = defaultdict(list)
        for el in nodes:
            by_item[el.item_id].append(el.node_id)
        for el in edges:
            if cat.get(el.item_id).category != "jumper":
                by_item[el.item_id].append(el.edge_id)
        for item_id, els in by_item.items():
            it = cat.get(item_id)
            if it.category == "host":
                continue
            aid = f"{tag}_PROC_{item_id}"
            sch.add(Activity(aid, f"Procure & fabricate: {it.name} (×{len(els)})",
                             it.lead_time_months * M, [(award, 0)], group="Procurement", phase=ph))
            proc_ids[item_id] = aid
            for e_id in els:
                element_map[e_id] = {"procure": aid}

        def camp_days(elements, linear):
            tot = 0.0
            for el in elements:
                it = cat.get(el.item_id)
                if linear:
                    tot += it.install_days * layout.edge_length(el) / 1000.0
                else:
                    tot += it.install_days
            return max(tot * s.weather_factor, 1.0) if elements else 0.0

        def preds_for(elements):
            return sorted({(proc_ids[el.item_id], 0.0) for el in elements if el.item_id in proc_ids})

        structs = [n for n in nodes if cat.get(n.item_id).category in STRUCTURE_KINDS]
        pipes = [e for e in edges if cat.get(e.item_id).category in PIPELAY_KINDS]
        cables = [e for e in edges if cat.get(e.item_id).category in CABLE_KINDS]
        jumpers = [e for e in edges if cat.get(e.item_id).category == "jumper"]
        wells = [n for n in nodes if cat.get(n.item_id).category == "well"]
        hosts = [n for n in nodes if cat.get(n.item_id).category == "host"]
        infra_done: List[Tuple[str, float]] = []

        def campaign(aid, name, elements, linear, extra_preds):
            if not elements:
                return None
            preds = preds_for(elements) + extra_preds
            sch.add(Activity(aid, name, camp_days(elements, linear), preds or [(award, 0)],
                             window=s.marine_window, group="Installation", phase=ph))
            for el in elements:
                element_map.setdefault(getattr(el, "node_id", None) or el.edge_id, {})["install"] = aid
            return aid

        st_id = campaign(f"{tag}_INST_STRUCT", "Install structures (HLV/CSV)", structs, False, [])
        pl_id = campaign(f"{tag}_INST_PIPE", "Pipelay & risers", pipes, True,
                         [(st_id, 0)] if st_id else [])
        cb_id = campaign(f"{tag}_INST_UMB", "Umbilical / power cable lay", cables, True,
                         [(pl_id, 0)] if pl_id else ([(st_id, 0)] if st_id else []))
        for x in (st_id, pl_id, cb_id):
            if x:
                infra_done.append((x, 0))

        if jumpers:
            met = f"{tag}_METROLOGY"
            sch.add(Activity(met, "Metrology", s.metrology_days * s.weather_factor,
                             [p for p in ((pl_id, 0), (st_id, 0)) if p[0]] or [(award, 0)],
                             window=s.marine_window, group="Installation", phase=ph))
            j_item = cat.get(jumpers[0].item_id)
            jf = f"{tag}_JUMPER_FAB"
            sch.add(Activity(jf, f"Jumper/spool fabrication (×{len(jumpers)})",
                             max(cat.get(j.item_id).lead_time_months for j in jumpers) * M,
                             [(met, 0)], group="Procurement", phase=ph))
            ti = f"{tag}_TIEIN"
            sch.add(Activity(ti, "Tie-in campaign (jumpers/spools)", camp_days(jumpers, False),
                             [(jf, 0)] + ([(cb_id, 0)] if cb_id else []),
                             window=s.marine_window, group="Installation", phase=ph))
            for j in jumpers:
                element_map[j.edge_id] = {"procure": jf, "install": ti}
            infra_done.append((ti, 0))

        for h in hosts:
            it = cat.get(h.item_id)
            hp = f"{tag}_HOST_ENG_{h.node_id}"
            hi = f"{tag}_HOST_MOD_{h.node_id}"
            sch.add(Activity(hp, f"Host modifications — engineering & prefab ({h.label or h.node_id})",
                             it.lead_time_months * M, [(award, 0)], group="Host", phase=ph))
            sch.add(Activity(hi, f"Host modifications — offshore ({h.label or h.node_id})",
                             it.install_days, [(hp, 0)], group="Host", phase=ph))
            element_map[h.node_id] = {"procure": hp, "install": hi}
            infra_done.append((hi, 0))

        # drilling: rigs in parallel lanes, wells sequential per lane
        first_well_done = None
        if wells:
            xt_preds = sorted({(proc_ids[w.item_id], 0.0) for w in wells if w.item_id in proc_ids})
            base_preds = xt_preds + ([(st_id, 0)] if st_id else []) + [("PDO_OK", 0)]
            lanes: List[Optional[str]] = [None] * max(1, s.rigs)
            for i, w in enumerate(sorted(wells, key=lambda x: x.node_id)):
                lane = i % len(lanes)
                aid = f"{tag}_DRILL_{w.node_id}"
                preds = list(base_preds) + ([(lanes[lane], 0)] if lanes[lane] else [])
                sch.add(Activity(aid, f"Drill & complete {w.label or w.node_id}", s.drill_days_per_well,
                                 preds, group="Drilling", phase=ph))
                lanes[lane] = aid
                element_map.setdefault(w.node_id, {})["install"] = aid
                if first_well_done is None:
                    first_well_done = aid
            sch.add(Activity(f"{tag}_DRILL_END", f"Phase {ph} drilling complete", 0,
                             [(l, 0) for l in lanes if l], milestone=True, group="Drilling", phase=ph))

        pc = f"{tag}_PRECOMM"
        sch.add(Activity(pc, "Pre-commissioning (flooding, leak test, controls)", s.precommissioning_days,
                         infra_done or [(award, 0)], group="Commissioning", phase=ph))
        cm = f"{tag}_COMM"
        cm_preds = [(pc, 0)] + ([(first_well_done, 0)] if first_well_done else [])
        sch.add(Activity(cm, "Commissioning & start-up", s.commissioning_days, cm_preds,
                         group="Commissioning", phase=ph))
        fo = f"{tag}_FIRST_OIL"
        sch.add(Activity(fo, f"Phase {ph} first production", 0, [(cm, 0), ("PDO_OK", 0)],
                         milestone=True, group="Milestones", phase=ph))
        first_oil_preds.append((fo, 0))

    return sch.compute(), element_map
