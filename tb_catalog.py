"""
tb_catalog.py — Subsea equipment catalog for TieBack Studio.

Every catalog item is data (no hard-coded logic elsewhere): unit cost,
cost basis (per unit / per metre / per inch-metre), installation spread and
duration, procurement lead time, fabrication duration, pressure rating,
connection rules, and uncertainty ranges.

⚠ DEFAULT RATES ARE INDICATIVE PLACEHOLDERS (order-of-magnitude, 2026 USD)
for demonstration and screening only. They are not benchmarked against any
operator or contractor data. Load a project cost library (YAML) for real work.

Internal units: USD, metres, inches (diameter), psi (pressure), days.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict, fields
from typing import Dict, List, Optional

import yaml

# Node categories (point equipment) and edge categories (linear equipment)
NODE_KINDS = (
    "well", "template", "manifold", "plet", "plem", "ilt", "ssiv",
    "boosting", "compression", "separation", "riser_base", "host",
)
EDGE_KINDS = ("flowline", "umbilical", "jumper", "riser", "power_cable")

COST_BASIS = ("unit", "per_m", "per_inch_m")

# Which edge kinds may connect which node kinds (unordered pairs).
# Production-carrying edges: flowline, jumper, riser.
PRODUCTION_EDGES = ("flowline", "jumper", "riser")
CONTROL_EDGES = ("umbilical",)
POWER_EDGES = ("power_cable", "umbilical")

_ANY_STRUCTURE = ("template", "manifold", "plet", "plem", "ilt", "ssiv",
                  "boosting", "compression", "separation", "riser_base", "host")

EDGE_RULES: Dict[str, Dict[str, tuple]] = {
    # edge kind -> {node kind: allowed counterpart kinds}
    "jumper": {
        "well": ("template", "manifold", "plet", "plem", "ilt", "boosting"),
        "template": ("well", "manifold", "plet", "plem", "boosting", "ssiv"),
        "manifold": ("well", "template", "plet", "plem", "boosting", "ssiv"),
        "plet": ("well", "template", "manifold", "ilt", "ssiv", "boosting", "riser_base"),
        "plem": ("well", "template", "manifold", "ssiv", "boosting", "riser_base"),
        "ilt": ("well", "plet"),
        "ssiv": ("template", "manifold", "plet", "plem", "riser_base"),
        "boosting": ("well", "template", "manifold", "plet", "plem"),
        "riser_base": ("plet", "plem", "ssiv"),
    },
    "flowline": {k: _ANY_STRUCTURE + ("well",) for k in _ANY_STRUCTURE + ("well",)},
    "riser": {"riser_base": ("host",), "host": ("riser_base", "plet", "plem", "ssiv"),
              "plet": ("host",), "plem": ("host",), "ssiv": ("host",)},
    "umbilical": {k: NODE_KINDS for k in NODE_KINDS},
    "power_cable": {k: ("boosting", "compression", "separation", "host", "template", "manifold")
                    for k in ("boosting", "compression", "separation", "host", "template", "manifold")},
}


@dataclass
class CatalogItem:
    item_id: str
    name: str
    category: str                    # one of NODE_KINDS or EDGE_KINDS
    cost_basis: str = "unit"         # unit | per_m | per_inch_m
    procurement_usd: float = 0.0     # per basis
    fabrication_usd: float = 0.0     # per basis (yard fab/assembly/testing)
    engineering_frac: float = 0.08   # engineering & PM as fraction of proc+fab
    install_spread: str = "csv"      # key into VESSEL_SPREADS
    install_days: float = 0.0        # per unit (unit basis) or per km (linear)
    lead_time_months: float = 12.0   # award -> ready for load-out
    rating_psi: float = 10000.0      # design pressure
    slots: int = 0                   # well slots (templates/manifolds)
    max_diameter_in: float = 0.0     # for linear items: allowed ID range max
    min_diameter_in: float = 0.0
    weight_te: float = 0.0           # per unit or per km
    uncertainty: tuple = (0.9, 1.0, 1.3)  # triangular multipliers (low, ml, high)
    notes: str = ""

    def __post_init__(self):
        if self.category not in NODE_KINDS + EDGE_KINDS:
            raise ValueError(f"{self.item_id}: unknown category '{self.category}'")
        if self.cost_basis not in COST_BASIS:
            raise ValueError(f"{self.item_id}: unknown cost_basis '{self.cost_basis}'")
        if self.category in EDGE_KINDS and self.cost_basis == "unit" and self.category != "jumper":
            raise ValueError(f"{self.item_id}: linear item must use per_m or per_inch_m basis")
        lo, ml, hi = self.uncertainty
        if not (0 < lo <= ml <= hi):
            raise ValueError(f"{self.item_id}: uncertainty must satisfy 0 < low <= ml <= high")
        for fname in ("procurement_usd", "fabrication_usd", "install_days", "lead_time_months"):
            if getattr(self, fname) < 0:
                raise ValueError(f"{self.item_id}: {fname} must be >= 0")
        self.uncertainty = tuple(float(x) for x in self.uncertainty)

    @property
    def is_linear(self) -> bool:
        return self.cost_basis in ("per_m", "per_inch_m")


@dataclass
class VesselSpread:
    key: str
    name: str
    day_rate_usd: float
    mob_demob_usd: float
    uncertainty: tuple = (0.85, 1.0, 1.4)


# ── Indicative defaults ──────────────────────────────────────────────────────
DEFAULT_SPREADS = [
    VesselSpread("csv", "Construction support vessel", 250_000, 1_500_000),
    VesselSpread("plv", "Pipelay vessel (reel/S-lay)", 450_000, 4_000_000),
    VesselSpread("hlv", "Heavy-lift vessel", 650_000, 5_000_000),
    VesselSpread("rig", "Semi-sub rig (XT/completion ops)", 450_000, 3_000_000),
    VesselSpread("ulv", "Umbilical/cable lay vessel", 300_000, 2_000_000),
    VesselSpread("host", "Host platform campaign (topsides)", 120_000, 0),
]

DEFAULT_ITEMS = [
    # wells / trees
    CatalogItem("xt_vxt_10k", "Vertical XT 10k", "well", procurement_usd=5.5e6, fabrication_usd=0.5e6,
                install_spread="rig", install_days=3, lead_time_months=18, rating_psi=10000, weight_te=35),
    CatalogItem("xt_hxt_10k", "Horizontal XT 10k", "well", procurement_usd=7.0e6, fabrication_usd=0.6e6,
                install_spread="rig", install_days=3, lead_time_months=20, rating_psi=10000, weight_te=45),
    CatalogItem("xt_hxt_15k", "Horizontal XT 15k HPHT", "well", procurement_usd=10.5e6, fabrication_usd=0.8e6,
                install_spread="rig", install_days=4, lead_time_months=24, rating_psi=15000, weight_te=60,
                uncertainty=(0.9, 1.0, 1.4)),
    # structures
    CatalogItem("tmpl_4slot", "4-slot template + protection", "template", procurement_usd=12e6,
                fabrication_usd=8e6, install_spread="hlv", install_days=4, lead_time_months=18,
                slots=4, weight_te=400),
    CatalogItem("tmpl_6slot", "6-slot template + protection", "template", procurement_usd=16e6,
                fabrication_usd=11e6, install_spread="hlv", install_days=5, lead_time_months=20,
                slots=6, weight_te=600),
    CatalogItem("mfld_4slot", "4-slot production manifold", "manifold", procurement_usd=14e6,
                fabrication_usd=5e6, install_spread="hlv", install_days=3, lead_time_months=18,
                slots=4, weight_te=250),
    CatalogItem("plet_std", "PLET (single hub)", "plet", procurement_usd=2.0e6, fabrication_usd=1.0e6,
                install_spread="plv", install_days=1, lead_time_months=12, weight_te=30),
    CatalogItem("plem_std", "PLEM (multi hub)", "plem", procurement_usd=4.0e6, fabrication_usd=2.0e6,
                install_spread="csv", install_days=2, lead_time_months=14, weight_te=80),
    CatalogItem("ilt_std", "In-line tee", "ilt", procurement_usd=2.5e6, fabrication_usd=1.0e6,
                install_spread="plv", install_days=1, lead_time_months=12, weight_te=25),
    CatalogItem("ssiv_std", "Subsea isolation valve", "ssiv", procurement_usd=6.0e6, fabrication_usd=1.5e6,
                install_spread="csv", install_days=2, lead_time_months=16, weight_te=60),
    CatalogItem("mpp_2x", "Multiphase boosting station (2 pumps)", "boosting", procurement_usd=90e6,
                fabrication_usd=25e6, install_spread="hlv", install_days=6, lead_time_months=30,
                weight_te=350, uncertainty=(0.9, 1.0, 1.5)),
    CatalogItem("comp_station", "Subsea compression station", "compression", procurement_usd=450e6,
                fabrication_usd=150e6, install_spread="hlv", install_days=15, lead_time_months=42,
                weight_te=1500, uncertainty=(0.85, 1.0, 1.6)),
    CatalogItem("sep_station", "Subsea separation + water reinjection", "separation", procurement_usd=180e6,
                fabrication_usd=70e6, install_spread="hlv", install_days=10, lead_time_months=36,
                weight_te=900, uncertainty=(0.85, 1.0, 1.6)),
    CatalogItem("riser_base", "Riser base", "riser_base", procurement_usd=3.0e6, fabrication_usd=2.0e6,
                install_spread="csv", install_days=2, lead_time_months=14, weight_te=120),
    CatalogItem("host_tiein", "Host tie-in modification (topsides)", "host", procurement_usd=25e6,
                fabrication_usd=15e6, engineering_frac=0.20, install_spread="host", install_days=60,
                lead_time_months=24, weight_te=300, uncertainty=(0.9, 1.0, 1.6),
                notes="Receiving facilities, pig receiver, control/HPU, chemical injection"),
    # linear
    CatalogItem("fl_rigid_cs", "Rigid CS flowline (coated)", "flowline", cost_basis="per_inch_m",
                procurement_usd=95, fabrication_usd=45, install_spread="plv", install_days=0.35,
                lead_time_months=12, min_diameter_in=4, max_diameter_in=20, weight_te=90),
    CatalogItem("fl_rigid_cra", "Rigid CRA-lined flowline", "flowline", cost_basis="per_inch_m",
                procurement_usd=260, fabrication_usd=70, install_spread="plv", install_days=0.4,
                lead_time_months=16, min_diameter_in=4, max_diameter_in=16, weight_te=95),
    CatalogItem("fl_pip", "Pipe-in-pipe insulated flowline", "flowline", cost_basis="per_inch_m",
                procurement_usd=240, fabrication_usd=110, install_spread="plv", install_days=0.5,
                lead_time_months=16, min_diameter_in=6, max_diameter_in=14, weight_te=160),
    CatalogItem("fl_deh", "DEH heated rigid flowline", "flowline", cost_basis="per_inch_m",
                procurement_usd=220, fabrication_usd=90, install_spread="plv", install_days=0.5,
                lead_time_months=18, min_diameter_in=6, max_diameter_in=14, weight_te=110,
                notes="Includes piggyback cable; topside power supply costed separately"),
    CatalogItem("fl_flex", "Flexible flowline", "flowline", cost_basis="per_inch_m",
                procurement_usd=210, fabrication_usd=0, install_spread="csv", install_days=0.3,
                lead_time_months=14, min_diameter_in=2, max_diameter_in=16, weight_te=70,
                rating_psi=7500),
    CatalogItem("umb_static", "Static steel-tube umbilical", "umbilical", cost_basis="per_m",
                procurement_usd=900, fabrication_usd=0, install_spread="ulv", install_days=0.25,
                lead_time_months=18, weight_te=25),
    CatalogItem("umb_dynamic", "Dynamic umbilical", "umbilical", cost_basis="per_m",
                procurement_usd=2500, fabrication_usd=0, install_spread="ulv", install_days=0.5,
                lead_time_months=20, weight_te=30),
    CatalogItem("jumper_rigid", "Rigid spool/jumper", "jumper", cost_basis="unit",
                procurement_usd=0.8e6, fabrication_usd=0.7e6, install_spread="csv", install_days=1.5,
                lead_time_months=6, weight_te=15,
                notes="Fabricated after metrology; lead time from metrology"),
    CatalogItem("riser_flex", "Flexible dynamic riser", "riser", cost_basis="per_inch_m",
                procurement_usd=420, fabrication_usd=0, install_spread="csv", install_days=2.0,
                lead_time_months=18, min_diameter_in=4, max_diameter_in=16, weight_te=120),
    CatalogItem("pwr_cable", "Subsea power cable (HV)", "power_cable", cost_basis="per_m",
                procurement_usd=1600, fabrication_usd=0, install_spread="ulv", install_days=0.3,
                lead_time_months=24, weight_te=40),
]


class Catalog:
    """Mutable catalog = defaults (or library file) + per-project overrides."""

    def __init__(self, items: Optional[List[CatalogItem]] = None,
                 spreads: Optional[List[VesselSpread]] = None):
        src_items = DEFAULT_ITEMS if items is None else items
        src_spreads = DEFAULT_SPREADS if spreads is None else spreads
        self.items: Dict[str, CatalogItem] = {}
        for it in src_items:
            self.add(copy.deepcopy(it))
        self.spreads: Dict[str, VesselSpread] = {s.key: copy.deepcopy(s) for s in src_spreads}
        self._validate_spreads()

    def add(self, item: CatalogItem):
        if item.item_id in self.items:
            raise ValueError(f"duplicate catalog id '{item.item_id}'")
        self.items[item.item_id] = item

    def get(self, item_id: str) -> CatalogItem:
        try:
            return self.items[item_id]
        except KeyError:
            raise KeyError(f"catalog item '{item_id}' not found") from None

    def by_category(self, category: str) -> List[CatalogItem]:
        return [i for i in self.items.values() if i.category == category]

    def override(self, item_id: str, /, **changes) -> CatalogItem:
        """Apply field overrides; re-validates by reconstructing the item."""
        base = asdict(self.get(item_id))
        valid = {f.name for f in fields(CatalogItem)}
        bad = set(changes) - valid
        if bad:
            raise ValueError(f"unknown catalog fields: {sorted(bad)}")
        if "item_id" in changes and changes["item_id"] != item_id:
            raise ValueError("item_id cannot be overridden")
        base.update(changes)
        new = CatalogItem(**base)
        self.items[item_id] = new
        return new

    def _validate_spreads(self):
        for it in self.items.values():
            if it.install_spread not in self.spreads:
                raise ValueError(f"{it.item_id}: spread '{it.install_spread}' not defined")

    # ── persistence ──
    def to_dict(self) -> dict:
        return {
            "schema": "tieback_catalog/1",
            "disclaimer": "Indicative rates unless replaced by a project cost library.",
            "spreads": [asdict(s) for s in self.spreads.values()],
            "items": [asdict(i) for i in self.items.values()],
        }

    def to_yaml(self) -> str:
        d = self.to_dict()
        for coll in (d["spreads"], d["items"]):
            for rec in coll:
                rec["uncertainty"] = list(rec["uncertainty"])
        return yaml.safe_dump(d, sort_keys=False)

    @classmethod
    def from_dict(cls, d: dict) -> "Catalog":
        if d.get("schema") != "tieback_catalog/1":
            raise ValueError("unsupported catalog schema")
        spreads = [VesselSpread(**{**s, "uncertainty": tuple(s.get("uncertainty", (0.85, 1.0, 1.4)))})
                   for s in d["spreads"]]
        items = [CatalogItem(**{**i, "uncertainty": tuple(i.get("uncertainty", (0.9, 1.0, 1.3)))})
                 for i in d["items"]]
        return cls(items, spreads)

    @classmethod
    def from_yaml(cls, text: str) -> "Catalog":
        return cls.from_dict(yaml.safe_load(text))


def edge_allowed(edge_kind: str, kind_a: str, kind_b: str) -> bool:
    rules = EDGE_RULES.get(edge_kind, {})
    return kind_b in rules.get(kind_a, ()) or kind_a in rules.get(kind_b, ())
