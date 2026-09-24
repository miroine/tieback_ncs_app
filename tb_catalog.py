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
    "boosting", "compression", "separation", "riser_base", "host", "control",
)
EDGE_KINDS = ("flowline", "umbilical", "jumper", "riser", "power_cable", "utility_line")

COST_BASIS = ("unit", "per_m", "per_inch_m")

# Which edge kinds may connect which node kinds (unordered pairs).
# Production-carrying edges: flowline, jumper, riser.
PRODUCTION_EDGES = ("flowline", "jumper", "riser")
CONTROL_EDGES = ("umbilical",)
POWER_EDGES = ("power_cable", "umbilical")
# Utility lines (chemical injection, gas lift, water injection, fibre, hydraulic) carry no
# production and are excluded from the production-path checks and flow-assurance network.
UTILITY_EDGES = ("utility_line",)

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
    "power_cable": {k: ("boosting", "compression", "separation", "host", "template", "manifold", "control")
                    for k in ("boosting", "compression", "separation", "host", "template", "manifold",
                              "control")},
    "utility_line": {k: NODE_KINDS for k in NODE_KINDS},
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
    # presentation / layout geometry (no effect on cost or schedule)
    symbol: str = ""              # map symbol: xt, template, manifold, plet, ssiv, pump, jacket,
                                  # semisub, fpso, riser_base, ilt (blank = by category)
    footprint_l_m: float = 0.0    # true-scale plan footprint, drawn when zoomed in
    footprint_w_m: float = 0.0
    line_color: str = ""          # line colour override (hex)
    line_dash: str = ""           # SVG dash pattern, e.g. "8 6"
    # pipe build-up (cross-section drawing only)
    wall_thickness_in: float = 0.0
    insulation_mm: float = 0.0
    coating_mm: float = 0.0

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
    lift_capacity_te: float = 0.0      # main-hook subsea lift limit (0 = not checked)


# ── Indicative defaults ──────────────────────────────────────────────────────
DEFAULT_SPREADS = [
    VesselSpread("csv", "Construction support vessel", 250_000, 1_500_000, lift_capacity_te=250),
    VesselSpread("plv", "Pipelay vessel (reel/S-lay)", 450_000, 4_000_000, lift_capacity_te=400),
    VesselSpread("hlv", "Heavy-lift vessel", 650_000, 5_000_000, lift_capacity_te=4_000),
    VesselSpread("rig", "Semi-sub rig (XT/completion ops)", 450_000, 3_000_000, lift_capacity_te=300),
    VesselSpread("ulv", "Umbilical/cable lay vessel", 300_000, 2_000_000, lift_capacity_te=150),
    VesselSpread("host", "Host platform campaign (topsides)", 120_000, 0, lift_capacity_te=0),
]

DEFAULT_ITEMS = [
    # wells / trees
    CatalogItem("xt_vxt_10k", "Vertical XT 10k", "well", procurement_usd=5.5e6, fabrication_usd=0.5e6,
                install_spread="rig", install_days=3, lead_time_months=18, rating_psi=10000, weight_te=35,
                symbol="xt", footprint_l_m=5, footprint_w_m=5),
    CatalogItem("xt_hxt_10k", "Horizontal XT 10k", "well", procurement_usd=7.0e6, fabrication_usd=0.6e6,
                install_spread="rig", install_days=3, lead_time_months=20, rating_psi=10000, weight_te=45,
                symbol="xt", footprint_l_m=5, footprint_w_m=5),
    CatalogItem("xt_hxt_15k", "Horizontal XT 15k HPHT", "well", procurement_usd=10.5e6, fabrication_usd=0.8e6,
                install_spread="rig", install_days=4, lead_time_months=24, rating_psi=15000, weight_te=60,
                uncertainty=(0.9, 1.0, 1.4), symbol="xt", footprint_l_m=5, footprint_w_m=5),
    CatalogItem("xt_vxt_5k", "Vertical XT 5k", "well", procurement_usd=4.5e6, fabrication_usd=0.4e6,
                install_spread="rig", install_days=3, lead_time_months=16, rating_psi=5000, weight_te=30,
                symbol="xt", footprint_l_m=5, footprint_w_m=5, notes="345 bar (5 000 psi) — low-pressure fields"),
    CatalogItem("xt_vxt_15k", "Vertical XT 15k", "well", procurement_usd=9.0e6, fabrication_usd=0.8e6,
                install_spread="rig", install_days=4, lead_time_months=24, rating_psi=15000, weight_te=55,
                uncertainty=(0.9, 1.0, 1.4), symbol="xt", footprint_l_m=5, footprint_w_m=5,
                notes="1 034 bar (15 000 psi)"),
    CatalogItem("xt_hxt_20k", "Horizontal XT 20k HPHT", "well", procurement_usd=15e6, fabrication_usd=1.2e6,
                install_spread="rig", install_days=5, lead_time_months=30, rating_psi=20000, weight_te=75,
                uncertainty=(0.9, 1.0, 1.6), symbol="xt", footprint_l_m=6, footprint_w_m=6,
                notes="1 379 bar (20 000 psi) — few qualified suppliers"),
    # structures
    CatalogItem("tmpl_4slot", "4-slot template + protection", "template", procurement_usd=12e6,
                fabrication_usd=8e6, install_spread="hlv", install_days=4, lead_time_months=18,
                slots=4, weight_te=400, symbol="template", footprint_l_m=32, footprint_w_m=22),
    CatalogItem("tmpl_6slot", "6-slot template + protection", "template", procurement_usd=16e6,
                fabrication_usd=11e6, install_spread="hlv", install_days=5, lead_time_months=20,
                slots=6, weight_te=600, symbol="template", footprint_l_m=42, footprint_w_m=24),
    CatalogItem("mfld_4slot", "4-slot production manifold", "manifold", procurement_usd=14e6,
                fabrication_usd=5e6, install_spread="hlv", install_days=3, lead_time_months=18,
                slots=4, weight_te=250, symbol="manifold", footprint_l_m=20, footprint_w_m=14),
    CatalogItem("plet_std", "PLET (single hub)", "plet", procurement_usd=2.0e6, fabrication_usd=1.0e6,
                install_spread="plv", install_days=1, lead_time_months=12, weight_te=30,
                symbol="plet", footprint_l_m=12, footprint_w_m=6),
    CatalogItem("plem_std", "PLEM (multi hub)", "plem", procurement_usd=4.0e6, fabrication_usd=2.0e6,
                install_spread="csv", install_days=2, lead_time_months=14, weight_te=80,
                symbol="plet", footprint_l_m=16, footprint_w_m=10),
    CatalogItem("ilt_std", "In-line tee", "ilt", procurement_usd=2.5e6, fabrication_usd=1.0e6,
                install_spread="plv", install_days=1, lead_time_months=12, weight_te=25, symbol="ilt"),
    CatalogItem("ssiv_std", "Subsea isolation valve", "ssiv", procurement_usd=6.0e6, fabrication_usd=1.5e6,
                install_spread="csv", install_days=2, lead_time_months=16, weight_te=60, symbol="ssiv"),
    CatalogItem("mpp_2x", "Multiphase boosting station (2 pumps)", "boosting", procurement_usd=90e6,
                fabrication_usd=25e6, install_spread="hlv", install_days=6, lead_time_months=30,
                weight_te=350, uncertainty=(0.9, 1.0, 1.5), symbol="pump",
                footprint_l_m=24, footprint_w_m=16),
    CatalogItem("comp_station", "Subsea compression station", "compression", procurement_usd=450e6,
                fabrication_usd=150e6, install_spread="hlv", install_days=15, lead_time_months=42,
                weight_te=1500, uncertainty=(0.85, 1.0, 1.6), symbol="pump",
                footprint_l_m=60, footprint_w_m=30),
    CatalogItem("sep_station", "Subsea separation + water reinjection", "separation", procurement_usd=180e6,
                fabrication_usd=70e6, install_spread="hlv", install_days=10, lead_time_months=36,
                weight_te=900, uncertainty=(0.85, 1.0, 1.6), symbol="pump",
                footprint_l_m=45, footprint_w_m=25),
    # ── more pipeline-end, in-line and protection equipment ──
    CatalogItem("plet_valved", "PLET with isolation valve", "plet", procurement_usd=3.2e6,
                fabrication_usd=1.4e6, install_spread="plv", install_days=1.5, lead_time_months=14,
                weight_te=45, symbol="plet", footprint_l_m=14, footprint_w_m=7,
                notes="Remote-operated valve lets the line be isolated at the end structure"),
    CatalogItem("plet_pig", "PLET with subsea pig launcher/receiver", "plet", procurement_usd=4.5e6,
                fabrication_usd=2.0e6, install_spread="csv", install_days=2, lead_time_months=16,
                weight_te=70, symbol="plet", footprint_l_m=18, footprint_w_m=8,
                notes="For single-line (non-looped) systems that still need pigging"),
    CatalogItem("wye_pig", "Piggable wye (Y-piece)", "ilt", procurement_usd=3.0e6, fabrication_usd=1.2e6,
                install_spread="plv", install_days=1, lead_time_months=14, weight_te=30, symbol="ilt",
                notes="Joins two lines into one while keeping it piggable"),
    CatalogItem("hot_tap", "Hot-tap tie-in to an operating pipeline", "ilt", procurement_usd=6.0e6,
                fabrication_usd=2.0e6, install_spread="csv", install_days=8, lead_time_months=18,
                weight_te=35, uncertainty=(0.9, 1.0, 1.5), symbol="ilt",
                notes="Tee welded/clamped onto a live line; includes hot-tap machine and diving/ROV spread time"),
    CatalogItem("hipps_mod", "HIPPS module (subsea)", "ssiv", procurement_usd=12e6, fabrication_usd=3e6,
                install_spread="csv", install_days=2, lead_time_months=24, weight_te=90, symbol="ssiv",
                footprint_l_m=10, footprint_w_m=8,
                notes="Tick HIPPS on the node so lines downstream may be de-rated"),
    # ── boosting ──
    CatalogItem("pump_1ph", "Single-phase booster pump station (liquid)", "boosting", procurement_usd=45e6,
                fabrication_usd=15e6, install_spread="hlv", install_days=5, lead_time_months=30,
                weight_te=250, uncertainty=(0.9, 1.0, 1.5), symbol="pump", footprint_l_m=18, footprint_w_m=12,
                notes="Centrifugal pump for oil or water with low gas fraction (typically GVF < 15 %)"),
    CatalogItem("pump_mp1", "Multiphase pump module (single, retrievable)", "boosting", procurement_usd=50e6,
                fabrication_usd=15e6, install_spread="hlv", install_days=4, lead_time_months=30,
                weight_te=180, uncertainty=(0.9, 1.0, 1.5), symbol="pump", footprint_l_m=16, footprint_w_m=12,
                notes="Helico-axial or twin-screw; handles high gas fractions without separation"),
    CatalogItem("winj_pump", "Subsea raw-seawater injection pump", "boosting", procurement_usd=55e6,
                fabrication_usd=15e6, install_spread="hlv", install_days=5, lead_time_months=30,
                weight_te=220, uncertainty=(0.9, 1.0, 1.6), symbol="pump", footprint_l_m=20, footprint_w_m=12,
                notes="Filters and injects seawater at the seabed — no injection line from the host"),
    # ── compression and separation ──
    CatalogItem("wgc_mod", "Wet-gas compressor module (single train)", "compression", procurement_usd=200e6,
                fabrication_usd=60e6, install_spread="hlv", install_days=8, lead_time_months=40,
                weight_te=700, uncertainty=(0.85, 1.0, 1.6), symbol="pump", footprint_l_m=35, footprint_w_m=20,
                notes="Compact wet-gas compression without upstream separation"),
    CatalogItem("sep_gl", "Gas–liquid separator with liquid pump", "separation", procurement_usd=130e6,
                fabrication_usd=45e6, install_spread="hlv", install_days=8, lead_time_months=36,
                weight_te=650, uncertainty=(0.85, 1.0, 1.6), symbol="pump", footprint_l_m=35, footprint_w_m=20,
                notes="Separates gas from liquid, pumps the liquid; gas flows freely — for long, low-energy tie-backs"),
    CatalogItem("sep_inline", "Compact in-line separator / de-watering unit", "separation",
                procurement_usd=60e6, fabrication_usd=20e6, install_spread="hlv", install_days=5,
                lead_time_months=30, weight_te=300, uncertainty=(0.85, 1.0, 1.7), symbol="pump",
                footprint_l_m=25, footprint_w_m=10,
                notes="Pipe-type separator removing bulk water for reinjection or disposal"),
    # ── control, power and utilities at the seabed ──
    CatalogItem("sdu", "Subsea distribution unit (SDU)", "control", procurement_usd=3.5e6,
                fabrication_usd=1.0e6, install_spread="csv", install_days=1.5, lead_time_months=16,
                weight_te=30, symbol="control", footprint_l_m=8, footprint_w_m=6,
                notes="Splits hydraulic, chemical, power and signal lines from the umbilical to several structures"),
    CatalogItem("uta", "Umbilical termination assembly (UTA)", "control", procurement_usd=2.0e6,
                fabrication_usd=0.8e6, install_spread="ulv", install_days=1, lead_time_months=16,
                weight_te=15, symbol="control", footprint_l_m=6, footprint_w_m=4,
                notes="Terminates the umbilical at the field; flying leads run on to the trees and structures"),
    CatalogItem("sub_power", "Subsea power distribution (transformer + VSD)", "control",
                procurement_usd=55e6, fabrication_usd=15e6, install_spread="hlv", install_days=4,
                lead_time_months=36, weight_te=280, uncertainty=(0.85, 1.0, 1.6), symbol="control",
                footprint_l_m=20, footprint_w_m=12,
                notes="Step-down transformer and variable-speed drives for pumps or compressors at long step-out"),
    CatalogItem("chem_store", "Subsea chemical storage & injection unit", "control", procurement_usd=35e6,
                fabrication_usd=10e6, install_spread="hlv", install_days=3, lead_time_months=30,
                weight_te=200, uncertainty=(0.85, 1.0, 1.6), symbol="control", footprint_l_m=18,
                footprint_w_m=10, notes="Stores and doses chemicals at the seabed; refilled by vessel"),
    CatalogItem("riser_base", "Riser base", "riser_base", procurement_usd=3.0e6, fabrication_usd=2.0e6,
                install_spread="csv", install_days=2, lead_time_months=14, weight_te=120, symbol="riser_base",
                footprint_l_m=14, footprint_w_m=14),
    CatalogItem("host_tiein", "Host tie-in modification (topsides)", "host", procurement_usd=25e6,
                fabrication_usd=15e6, engineering_frac=0.20, install_spread="host", install_days=60,
                lead_time_months=24, weight_te=300, uncertainty=(0.9, 1.0, 1.6),
                notes="Receiving facilities, pig receiver, control/HPU, chemical injection",
                symbol="jacket", footprint_l_m=60, footprint_w_m=45),
    CatalogItem("host_semi", "Host tie-in — semi-submersible", "host", procurement_usd=30e6,
                fabrication_usd=18e6, engineering_frac=0.20, install_spread="host", install_days=70,
                lead_time_months=26, weight_te=350, uncertainty=(0.9, 1.0, 1.6), symbol="semisub",
                footprint_l_m=110, footprint_w_m=80,
                notes="Floater tie-in: riser porch/pull-in, receiving facilities, utilities"),
    CatalogItem("host_fpso", "Host tie-in — FPSO", "host", procurement_usd=35e6, fabrication_usd=20e6,
                engineering_frac=0.22, install_spread="host", install_days=80, lead_time_months=28,
                weight_te=400, uncertainty=(0.9, 1.0, 1.7), symbol="fpso", footprint_l_m=280,
                footprint_w_m=50, notes="Turret/swivel slot, riser pull-in, topsides tie-in"),
    # linear
    CatalogItem("fl_rigid_cs", "Rigid CS flowline (coated)", "flowline", cost_basis="per_inch_m",
                procurement_usd=95, fabrication_usd=45, install_spread="plv", install_days=0.35,
                lead_time_months=12, min_diameter_in=4, max_diameter_in=20, weight_te=90, line_color="#00243D", line_dash="", wall_thickness_in=0.75, insulation_mm=0.0, coating_mm=45.0),
    CatalogItem("fl_rigid_cra", "Rigid CRA-lined flowline", "flowline", cost_basis="per_inch_m",
                procurement_usd=260, fabrication_usd=70, install_spread="plv", install_days=0.4,
                lead_time_months=16, min_diameter_in=4, max_diameter_in=16, weight_te=95, line_color="#004B6B", line_dash="", wall_thickness_in=0.75, insulation_mm=0.0, coating_mm=45.0),
    CatalogItem("fl_pip", "Pipe-in-pipe insulated flowline", "flowline", cost_basis="per_inch_m",
                procurement_usd=240, fabrication_usd=110, install_spread="plv", install_days=0.5,
                lead_time_months=16, min_diameter_in=6, max_diameter_in=14, weight_te=160, line_color="#0F7A8A", line_dash="", wall_thickness_in=0.63, insulation_mm=50.0, coating_mm=6.0),
    CatalogItem("fl_deh", "DEH heated rigid flowline", "flowline", cost_basis="per_inch_m",
                procurement_usd=220, fabrication_usd=90, install_spread="plv", install_days=0.5,
                lead_time_months=18, min_diameter_in=6, max_diameter_in=14, weight_te=110,
                notes="Includes piggyback cable; topside power supply costed separately", line_color="#C4561B", line_dash="", wall_thickness_in=0.75, insulation_mm=50.0, coating_mm=6.0),
    CatalogItem("fl_flex", "Flexible flowline", "flowline", cost_basis="per_inch_m",
                procurement_usd=210, fabrication_usd=0, install_spread="csv", install_days=0.3,
                lead_time_months=14, min_diameter_in=2, max_diameter_in=16, weight_te=70,
                rating_psi=7500, line_color="#3E8A91", line_dash="", wall_thickness_in=0.0, insulation_mm=25.0, coating_mm=8.0),
    CatalogItem("fl_tcp", "Thermoplastic composite pipe (TCP) flowline", "flowline", cost_basis="per_inch_m",
                procurement_usd=230, fabrication_usd=0, install_spread="csv", install_days=0.25,
                lead_time_months=12, min_diameter_in=2, max_diameter_in=10, weight_te=30, rating_psi=10000,
                line_color="#2E7D6F", line_dash="", wall_thickness_in=0.0, insulation_mm=15.0, coating_mm=0.0,
                notes="Light, spoolable, corrosion-free; installed from a construction vessel"),
    CatalogItem("jumper_flex", "Flexible jumper", "jumper", cost_basis="unit",
                procurement_usd=0.6e6, fabrication_usd=0.1e6, install_spread="csv", install_days=1.0,
                lead_time_months=10, weight_te=10, line_color="#3E8A91",
                notes="Tolerant of metrology error; no spool fabrication after survey"),
    CatalogItem("umb_static", "Static steel-tube umbilical", "umbilical", cost_basis="per_m",
                procurement_usd=900, fabrication_usd=0, install_spread="ulv", install_days=0.25,
                lead_time_months=18, weight_te=25, line_color="#E9A23B", line_dash="8 6"),
    CatalogItem("umb_dynamic", "Dynamic umbilical", "umbilical", cost_basis="per_m",
                procurement_usd=2500, fabrication_usd=0, install_spread="ulv", install_days=0.5,
                lead_time_months=20, weight_te=30, line_color="#C98A1F", line_dash="8 6"),
    CatalogItem("slot_tiein", "Template slot tie-in (integral)", "jumper", cost_basis="unit",
                procurement_usd=0.25e6, fabrication_usd=0.15e6, install_spread="rig", install_days=0.5,
                lead_time_months=12, weight_te=4, line_color="#6F6F6F", line_dash="2 3",
                notes="XT landed directly in the template flowbase — no fabricated spool"),
    CatalogItem("jumper_rigid", "Rigid spool/jumper", "jumper", cost_basis="unit",
                procurement_usd=0.8e6, fabrication_usd=0.7e6, install_spread="csv", install_days=1.5,
                lead_time_months=6, weight_te=15,
                notes="Fabricated after metrology; lead time from metrology", wall_thickness_in=0.63, insulation_mm=0.0, coating_mm=6.0),
    CatalogItem("riser_flex", "Flexible dynamic riser", "riser", cost_basis="per_inch_m",
                procurement_usd=420, fabrication_usd=0, install_spread="csv", install_days=2.0,
                lead_time_months=18, min_diameter_in=4, max_diameter_in=16, weight_te=120, line_color="#7D4EBF", line_dash="", wall_thickness_in=0.0, insulation_mm=30.0, coating_mm=8.0),
    CatalogItem("riser_flex_lw", "Flexible lazy-wave riser (with buoyancy)", "riser", cost_basis="per_inch_m",
                procurement_usd=480, fabrication_usd=0, install_spread="csv", install_days=2.5,
                lead_time_months=18, min_diameter_in=4, max_diameter_in=16, weight_te=130, line_color="#9467BD",
                line_dash="", wall_thickness_in=0.0, insulation_mm=30.0, coating_mm=8.0,
                notes="Buoyancy section decouples floater motion — deeper water or larger offsets"),
    CatalogItem("riser_scr", "Steel catenary riser (SCR)", "riser", cost_basis="per_inch_m",
                procurement_usd=200, fabrication_usd=90, install_spread="plv", install_days=2.5,
                lead_time_months=16, min_diameter_in=6, max_diameter_in=24, weight_te=150, rating_psi=10000,
                line_color="#5E3A9E", line_dash="", wall_thickness_in=0.9, insulation_mm=0.0, coating_mm=6.0,
                notes="Low-motion hosts (TLP, spar, semi in deep water); fatigue at the touchdown point governs"),
    CatalogItem("riser_slwr", "Steel lazy-wave riser (SLWR)", "riser", cost_basis="per_inch_m",
                procurement_usd=260, fabrication_usd=110, install_spread="plv", install_days=3.5,
                lead_time_months=20, min_diameter_in=6, max_diameter_in=20, weight_te=170, rating_psi=10000,
                line_color="#4B2E83", line_dash="", wall_thickness_in=0.9, insulation_mm=0.0, coating_mm=6.0,
                notes="Steel riser with buoyancy for turret-moored FPSOs and harsh-environment motions"),
    CatalogItem("riser_ttr", "Top-tensioned riser (TTR)", "riser", cost_basis="per_inch_m",
                procurement_usd=320, fabrication_usd=120, install_spread="rig", install_days=4,
                lead_time_months=22, min_diameter_in=6, max_diameter_in=16, weight_te=180, rating_psi=10000,
                line_color="#7B3FA0", line_dash="", wall_thickness_in=0.9, insulation_mm=0.0, coating_mm=6.0,
                notes="Vertical riser tensioned from a TLP or spar; allows dry trees"),
    CatalogItem("riser_hybrid", "Hybrid riser tower / free-standing riser", "riser", cost_basis="per_inch_m",
                procurement_usd=600, fabrication_usd=250, install_spread="hlv", install_days=6,
                lead_time_months=30, min_diameter_in=6, max_diameter_in=20, weight_te=250, rating_psi=10000,
                uncertainty=(0.85, 1.0, 1.6), line_color="#3C1F66", line_dash="",
                wall_thickness_in=0.9, insulation_mm=40.0, coating_mm=6.0,
                notes="Buoyancy-can tensioned steel riser with flexible jumpers to the host; very deep water"),
    CatalogItem("riser_rigid_fixed", "Rigid riser clamped to a fixed platform", "riser", cost_basis="per_inch_m",
                procurement_usd=160, fabrication_usd=120, install_spread="hlv", install_days=3,
                lead_time_months=14, min_diameter_in=4, max_diameter_in=30, weight_te=140, rating_psi=10000,
                line_color="#6A4C93", line_dash="", wall_thickness_in=0.75, insulation_mm=0.0, coating_mm=6.0,
                notes="Pre-installed or retrofitted riser on a jacket, with a spool to the pipeline"),
    CatalogItem("riser_jtube", "J-tube pull-in riser", "riser", cost_basis="per_inch_m",
                procurement_usd=140, fabrication_usd=60, install_spread="csv", install_days=2,
                lead_time_months=12, min_diameter_in=2, max_diameter_in=16, weight_te=100, rating_psi=10000,
                line_color="#8A6BBE", line_dash="", wall_thickness_in=0.6, insulation_mm=0.0, coating_mm=6.0,
                notes="Line or umbilical pulled up through a J-tube on a fixed platform — uses a spare J-tube"),
    CatalogItem("umb_power", "Electro-hydraulic umbilical with power cores", "umbilical",
                cost_basis="per_m", procurement_usd=1500, install_spread="ulv", install_days=0.3,
                lead_time_months=22, weight_te=32, line_color="#B3801A", line_dash="8 6",
                notes="Control, chemical and LV/MV power in one umbilical"),
    CatalogItem("chem_line", "Chemical injection line (MEG/methanol)", "utility_line", cost_basis="per_m",
                procurement_usd=260, install_spread="ulv", install_days=0.2, lead_time_months=14,
                weight_te=12, rating_psi=10000, line_color="#9DBA00", line_dash="4 4",
                notes="Stand-alone bulk chemical line; often piggybacked on the flowline"),
    CatalogItem("gaslift_line", "Gas lift line", "utility_line", cost_basis="per_inch_m",
                procurement_usd=95, fabrication_usd=45, install_spread="plv", install_days=0.35,
                lead_time_months=12, min_diameter_in=3, max_diameter_in=10, weight_te=70,
                line_color="#5B8FB9", line_dash="10 4", wall_thickness_in=0.5, insulation_mm=0.0, coating_mm=40.0),
    CatalogItem("winj_line", "Water injection flowline", "utility_line", cost_basis="per_inch_m",
                procurement_usd=90, fabrication_usd=42, install_spread="plv", install_days=0.35,
                lead_time_months=12, min_diameter_in=4, max_diameter_in=16, weight_te=80,
                line_color="#2E86AB", line_dash="", wall_thickness_in=0.5, insulation_mm=0.0, coating_mm=40.0),
    CatalogItem("service_line", "Hydraulic/service line", "utility_line", cost_basis="per_m",
                procurement_usd=180, install_spread="ulv", install_days=0.2, lead_time_months=12,
                weight_te=8, line_color="#8C6D1F", line_dash="3 5"),
    CatalogItem("fibre_cable", "Fibre-optic cable", "utility_line", cost_basis="per_m",
                procurement_usd=120, install_spread="ulv", install_days=0.2, lead_time_months=12,
                weight_te=5, line_color="#6F6F6F", line_dash="1 5"),
    CatalogItem("pwr_cable", "Subsea power cable (HV)", "power_cable", cost_basis="per_m",
                procurement_usd=1600, fabrication_usd=0, install_spread="ulv", install_days=0.3,
                lead_time_months=24, weight_te=40, line_color="#C4561B", line_dash="2 6"),
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

    def missing_defaults(self) -> List[str]:
        """Built-in items this catalogue does not have (e.g. one saved by an older version)."""
        return [i.item_id for i in DEFAULT_ITEMS if i.item_id not in self.items]

    def add_missing_defaults(self) -> List[str]:
        """Add the built-in items this catalogue lacks. Existing items and rates are untouched."""
        added = self.missing_defaults()
        for i in DEFAULT_ITEMS:
            if i.item_id in added:
                self.add(copy.deepcopy(i))
        for s in DEFAULT_SPREADS:
            if s.key not in self.spreads:
                self.spreads[s.key] = copy.deepcopy(s)
        return added

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
