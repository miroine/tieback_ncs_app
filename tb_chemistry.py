"""
tb_chemistry.py — Production chemistry screening for a tie-back concept.

Two questions this answers at concept stage:

1. **MEG or methanol?** Both depress the hydrate curve; the choice is decided by
   how much inhibitor has to be carried, whether it is recovered, what it does to
   the export specs and what it costs over field life. `inhibitor_options` sizes
   both and `recommend_inhibitor` says which one the numbers point to, with the
   reasons written out so the recommendation can be argued with.

2. **What else will bite?** Hydrates are the one everybody screens. Wax,
   asphaltenes, scale, emulsions, corrosion, souring, sand and bacteria decide
   just as much of the operating philosophy. `screen` walks them with the data
   the concept actually has and, where it does not have it, says which test
   produces it rather than inventing a number.

Everything here is **screening**: correlations valid over a limited range, and
rules of thumb that an operator's own chemistry data should replace. Nothing in
this module substitutes for a flow-assurance study with a thermodynamic package
(Multiflash, PVTsim, OLGA/LedaFlow) or for laboratory work on the real fluid.

Units: °C, bar, Sm³/d, te/d, wt %.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import tb_thermal as th

HIGH, MEDIUM, LOW, UNKNOWN, NA = "high", "medium", "low", "unknown", "not applicable"
RISK_ORDER = {HIGH: 0, MEDIUM: 1, UNKNOWN: 2, LOW: 3, NA: 4}

# Molar masses (g/mol) and Hammerschmidt constants, matching tb_thermal.INHIBITORS.
INHIBITOR_MW = {"Methanol": 32.04, "MEG": 62.07}
WATER_MW = 18.015

# Densities at ~15 °C (kg/m³) for volume and umbilical sizing.
INHIBITOR_DENSITY = {"Methanol": 792.0, "MEG": 1113.0}

# Indicative delivered cost, USD/te. Placeholders — replace with contract prices.
INHIBITOR_COST_USD_TE = {"Methanol": 450.0, "MEG": 1500.0}

# Hammerschmidt is fitted to dilute solutions. Beyond these concentrations it
# overstates the depression and a thermodynamic model is required.
HAMMERSCHMIDT_LIMIT_WT = {"Methanol": 25.0, "MEG": 30.0}


# ─────────────────────────── hydrate inhibition ────────────────────────────

def hammerschmidt_wt_pct(subcooling_c: float, inhibitor: str) -> float:
    """Inhibitor concentration in the free water needed for `subcooling_c` °C.

    Inversion of tb_thermal.hammerschmidt_depression_f, so the two always agree.
    """
    if inhibitor not in INHIBITOR_MW:
        raise ValueError(f"unknown inhibitor '{inhibitor}'")
    if subcooling_c <= 0:
        return 0.0
    k, m = th.INHIBITORS[inhibitor]
    dt_f = subcooling_c * 1.8
    w = 100.0 * dt_f * m / (k + dt_f * m)
    return min(w, 99.0)


def nielsen_bucklin_wt_pct(subcooling_c: float) -> float:
    """Methanol concentration by Nielsen & Bucklin (1983), ΔT = −72·ln(x_water) °C.

    Valid to about 88 wt % and the better answer above ~25 wt %, where
    Hammerschmidt runs out. Reported next to Hammerschmidt so the divergence is
    visible rather than hidden.
    """
    if subcooling_c <= 0:
        return 0.0
    x_water = math.exp(-subcooling_c / 72.0)
    x_meoh = 1.0 - x_water
    mass_meoh = x_meoh * INHIBITOR_MW["Methanol"]
    return 100.0 * mass_meoh / (mass_meoh + x_water * WATER_MW)


@dataclass
class InhibitorDuty:
    inhibitor: str
    subcooling_c: float
    wt_pct: float                 # required in the aqueous phase
    lean_wt_pct: float            # concentration as injected
    injection_te_d: float         # total, including losses
    injection_m3_d: float
    aqueous_te_d: float           # the part that does the inhibiting
    loss_te_d: float              # to the gas and condensate phases
    annual_te: float
    annual_cost_musd: float
    correlation_valid: bool
    note: str = ""


def inhibitor_duty(inhibitor: str, subcooling_c: float, water_rate_sm3_d: float,
                   lean_wt_pct: float = 0.0, loss_fraction: float = 0.0,
                   uptime: float = 0.95, cost_usd_te: Optional[float] = None,
                   regenerated: bool = False, regeneration_recovery: float = 0.95) -> InhibitorDuty:
    """Continuous-injection duty for one inhibitor.

    `loss_fraction` is inhibitor leaving with the gas and condensate rather than
    the water — real for methanol (volatile, and it partitions into condensate),
    small for MEG. It cannot be derived here: it comes from a flash with a
    thermodynamic package, so it is an input with an indicative default.

    `regenerated` credits a regeneration unit: only the make-up is bought.
    """
    if inhibitor not in INHIBITOR_MW:
        raise ValueError(f"unknown inhibitor '{inhibitor}'")
    if water_rate_sm3_d < 0:
        raise ValueError("water rate must be >= 0")
    w = hammerschmidt_wt_pct(subcooling_c, inhibitor)
    lean = float(lean_wt_pct) if lean_wt_pct > 0 else (80.0 if inhibitor == "MEG" else 100.0)
    lean = min(max(lean, w + 1e-6), 100.0)
    water_te_d = water_rate_sm3_d * 1.02      # produced water ≈ 1020 kg/m³

    # inhibitor / (inhibitor + all water) = w/100 with a lean stream of strength c:
    #   L·c / (L + W) = w/100   →   L = W·w / (lean − w)
    if w >= lean:
        aqueous = float("inf")
    else:
        aqueous = water_te_d * w / (lean - w)
    losses = aqueous * max(0.0, loss_fraction)
    total = aqueous + losses
    make_up = total * (1 - regeneration_recovery) + losses if regenerated else total
    density = INHIBITOR_DENSITY[inhibitor]
    unit_cost = INHIBITOR_COST_USD_TE[inhibitor] if cost_usd_te is None else float(cost_usd_te)
    annual = make_up * 365.0 * uptime
    limit = HAMMERSCHMIDT_LIMIT_WT[inhibitor]
    valid = w <= limit
    note = ""
    if not valid:
        note = (f"{w:.0f} wt % is beyond the {limit:.0f} wt % Hammerschmidt range — "
                f"the real requirement is higher; confirm with a thermodynamic model")
    return InhibitorDuty(
        inhibitor=inhibitor, subcooling_c=subcooling_c, wt_pct=w, lean_wt_pct=lean,
        injection_te_d=total, injection_m3_d=(total * 1000.0 / density if math.isfinite(total) else float("inf")),
        aqueous_te_d=aqueous, loss_te_d=losses, annual_te=annual,
        annual_cost_musd=annual * unit_cost / 1e6, correlation_valid=valid, note=note)


@dataclass
class InhibitorCase:
    """What the choice between MEG and methanol is being decided on."""
    subcooling_c: float = 8.0
    water_rate_sm3_d: float = 200.0
    gas_rate_msm3_d: float = 1.0
    condensate_rate_sm3_d: float = 0.0
    tieback_km: float = 15.0
    field_life_years: float = 15.0
    duty: str = "continuous"              # continuous | start-up and shutdown only
    host_has_meg_system: bool = False
    host_has_methanol: bool = True
    formation_water_salinity_wt_pct: float = 3.5
    co2_mol_pct: float = 0.0
    umbilical_cores: int = 1
    methanol_loss_fraction: float = 0.25   # to gas + condensate, indicative
    meg_loss_fraction: float = 0.02
    meg_unit_capex_musd: float = 45.0      # MEG regeneration + reclamation on the host
    meg_recovery: float = 0.95


def inhibitor_options(case: InhibitorCase) -> List[InhibitorDuty]:
    """Both inhibitors sized for the same duty, so they can be compared."""
    regen = case.duty == "continuous"
    return [
        inhibitor_duty("MEG", case.subcooling_c, case.water_rate_sm3_d,
                       loss_fraction=case.meg_loss_fraction, regenerated=regen,
                       regeneration_recovery=case.meg_recovery),
        inhibitor_duty("Methanol", case.subcooling_c, case.water_rate_sm3_d,
                       loss_fraction=case.methanol_loss_fraction, regenerated=False),
    ]


def recommend_inhibitor(case: InhibitorCase) -> dict:
    """Which one the numbers point to, with the reasoning on both sides.

    This is a screening argument, not a selection study: the real decision also
    weighs the host's existing utilities, the operator's chemical contracts,
    discharge permits and the produced-water plant.
    """
    meg, meoh = inhibitor_options(case)
    by_name = {"MEG": meg, "Methanol": meoh}
    for_meg: List[str] = []
    for_meoh: List[str] = []

    if case.duty != "continuous":
        for_meoh.append("Duty is start-up and shutdown only — batch methanol avoids a "
                        "regeneration unit and a dedicated umbilical core entirely.")
        for_meg.append("MEG only pays back against continuous injection; for batch duty "
                       "its regeneration plant is idle capital.")
    else:
        for_meg.append("Continuous inhibition over field life — MEG is regenerated and "
                       "recycled, so only make-up is bought.")
        for_meoh.append("Methanol is normally once-through: every tonne injected is bought "
                        "and most of it leaves with the gas and condensate.")

    if case.subcooling_c > 0:
        for_meoh.append(f"Per tonne, methanol depresses the curve further — "
                        f"{meoh.wt_pct:.0f} wt % against {meg.wt_pct:.0f} wt % MEG for "
                        f"{case.subcooling_c:.1f} °C of subcooling — so the umbilical and "
                        f"topsides pumps are smaller.")
    if case.subcooling_c > 10:
        for_meg.append(f"At {case.subcooling_c:.0f} °C subcooling the MEG rate is large but "
                       f"bounded; methanol at this duty is a continuous purchase.")

    life_meg = meg.annual_cost_musd * case.field_life_years + (case.meg_unit_capex_musd if case.duty == "continuous" else 0.0)
    life_meoh = meoh.annual_cost_musd * case.field_life_years
    if math.isfinite(life_meg) and math.isfinite(life_meoh):
        if life_meoh > life_meg * 1.2:
            for_meg.append(f"Over {case.field_life_years:.0f} years the indicative spend is "
                           f"{life_meg:,.0f} MUSD with MEG against {life_meoh:,.0f} MUSD with "
                           f"methanol, including the regeneration unit.")
        elif life_meg > life_meoh * 1.2:
            for_meoh.append(f"Over {case.field_life_years:.0f} years methanol is the cheaper "
                            f"route on these numbers — {life_meoh:,.0f} against "
                            f"{life_meg:,.0f} MUSD including the MEG plant.")

    if case.host_has_meg_system:
        for_meg.append("The host already runs a MEG system — reusing it removes most of the "
                       "topsides scope and the long-lead reclamation package.")
    elif case.duty == "continuous":
        for_meg.append("No MEG system on the host: allow for a regeneration and reclamation "
                       "package, storage and a dedicated umbilical core in the cost and the "
                       "topsides weight.")
    if case.host_has_methanol:
        for_meoh.append("Methanol is already available on the host for start-up and "
                        "shutdown duty.")

    if case.condensate_rate_sm3_d > 0:
        for_meg.append("Methanol partitions into the condensate and the gas, which puts it on "
                       "the export streams — check the condensate and gas specifications and "
                       "the downstream refinery's methanol limit.")
    if case.co2_mol_pct >= 1.0:
        for_meg.append(f"With {case.co2_mol_pct:.1f} mol % CO₂, MEG with a pH stabiliser is "
                       f"the established NCS route for wet-gas corrosion control — methanol "
                       f"offers no corrosion benefit.")
    if case.formation_water_salinity_wt_pct > 5.0:
        for_meoh.append(f"Formation water at {case.formation_water_salinity_wt_pct:.1f} wt % "
                        f"salt loads the MEG reclaimer — salt removal is the part of a MEG "
                        f"plant that most often limits availability.")
    if case.tieback_km > 30 and case.duty == "continuous":
        for_meoh.append(f"Over {case.tieback_km:.0f} km, rich MEG return and lean MEG supply "
                        f"hydraulics get demanding — check umbilical core sizes and cold "
                        f"viscosity before committing.")

    for_meg.append("Lower toxicity and a lower fire hazard than methanol, which matters for "
                   "storage, bunkering and offshore handling.")
    for_meoh.append("No regeneration plant, no reclaimer, far less topsides weight and space.")

    if case.duty != "continuous":
        pick, why = "Methanol", "batch duty does not justify a regeneration plant"
    elif case.host_has_meg_system:
        pick, why = "MEG", "continuous duty into a host that already has the system"
    elif math.isfinite(life_meoh) and math.isfinite(life_meg) and life_meoh > life_meg * 1.2:
        pick, why = "MEG", "continuous duty where methanol purchase dominates over field life"
    elif math.isfinite(life_meg) and math.isfinite(life_meoh) and life_meg > life_meoh * 1.2:
        pick, why = "Methanol", "the inhibitor bill is small enough that a MEG plant is not earned"
    else:
        pick, why = "MEG", "continuous duty on the NCS, where MEG with pH stabilisation is the norm"

    return {"recommended": pick, "because": why, "duties": by_name,
            "life_cost_musd": {"MEG": life_meg, "Methanol": life_meoh},
            "for_meg": for_meg, "for_methanol": for_meoh,
            "caveat": ("Screening only. Concentration comes from Hammerschmidt, losses to the "
                       "gas and condensate are an assumed fraction, and prices are placeholders. "
                       "Confirm with a thermodynamic flash on the real fluid and the operator's "
                       "chemical contracts before selection.")}


# ───────────────────────── production chemistry screen ─────────────────────

@dataclass
class ChemistryInputs:
    """What the screen knows about the fluid beyond the black-oil description.

    Anything left at its default is reported as *unknown* with the test that
    would settle it, rather than being guessed.
    """
    wax_appearance_c: Optional[float] = None       # WAT / cloud point
    pour_point_c: Optional[float] = None
    wax_content_wt_pct: Optional[float] = None
    asphaltene_wt_pct: Optional[float] = None
    saturation_pressure_bara: Optional[float] = None
    co2_mol_pct: Optional[float] = None
    h2s_ppm: Optional[float] = None
    formation_water_salinity_wt_pct: Optional[float] = None
    barium_mg_l: Optional[float] = None
    sulphate_injection: bool = False               # seawater injection without sulphate removal
    sand_expected: bool = False
    material: str = "carbon steel"                 # carbon steel | CRA
    inhibitor: str = "None"
    mercury_expected: bool = False
    naphthenate_risk: bool = False                 # high TAN crude
    tan_mg_koh_g: Optional[float] = None


def _row(issue, risk, basis, why, mitigation, data_needed=""):
    return dict(issue=issue, risk=risk, basis=basis, why=why,
                mitigation=mitigation, data_needed=data_needed)


def screen(fluid, inputs: ChemistryInputs, min_temp_c: float, arrival_temp_c: float,
           seabed_temp_c: float, min_pressure_bara: float, water_cut: Optional[float] = None,
           hydrate_margin_c: Optional[float] = None, tieback_km: float = 15.0) -> List[dict]:
    """Production-chemistry threats for this concept, worst first.

    `min_temp_c` is the coldest point in the system at design rate — for most
    tie-backs the host end of the longest line, or a shut-in approaching seabed
    temperature.
    """
    wc = fluid.water_cut if water_cut is None else water_cut
    rows: List[dict] = []

    # ── hydrates ──
    if hydrate_margin_c is None:
        rows.append(_row("Hydrates", UNKNOWN, "no flow-assurance solve",
                         "Every wet gas or oil with free water below about 20 °C and above "
                         "50 bar sits in the hydrate region.",
                         "Run the flow-assurance solve, then size inhibition on the subcooling.",
                         "Hydrate curve from a thermodynamic model on the real gas composition"))
    else:
        risk = LOW if hydrate_margin_c >= 3 else (MEDIUM if hydrate_margin_c >= 0 else HIGH)
        rows.append(_row(
            "Hydrates", risk, f"margin {hydrate_margin_c:+.1f} °C at design rate",
            "Blockage on shutdown and restart is the dominant tie-back risk; the margin at "
            "turndown and during cool-down is tighter than at design rate.",
            ("Insulation or active heating to stay outside the curve, inhibitor for start-up "
             "and shutdown, and a depressurisation route for an extended shutdown."
             if risk != LOW else
             "Keep the margin under review at turndown and late life, when rates fall and "
             "water cut rises."),
            "Hydrate curve on the real composition; cool-down and restart transient in OLGA/LedaFlow"))

    # ── wax ──
    if inputs.wax_appearance_c is None:
        rows.append(_row(
            "Wax deposition", UNKNOWN, "no WAT measured",
            "Wax appearance temperature cannot be inferred from API gravity — two crudes of "
            "the same gravity can differ by 30 °C. It decides whether the line runs above or "
            "below the deposition threshold for most of its length.",
            "Measure WAT and pour point early; they set the insulation and pigging philosophy.",
            "WAT by cross-polar microscopy or DSC, pour point (ASTM D5853), wax content by "
            "HTGC, and a deposition rate from a cold-finger or flow-loop test"))
    else:
        wat = inputs.wax_appearance_c
        if min_temp_c < wat:
            below = wat - min_temp_c
            risk = HIGH if below > 10 else MEDIUM
            rows.append(_row(
                "Wax deposition", risk,
                f"coldest point {min_temp_c:.1f} °C, WAT {wat:.1f} °C ({below:.1f} °C below)",
                "Below the WAT, wax builds on the cold wall, restricting the bore and raising "
                "back-pressure; the deposit also shields the steel from a corrosion inhibitor.",
                "Insulate to keep the wall above the WAT where possible, plan round-trip "
                "pigging from the start (a pig launcher is far cheaper designed in than "
                "retrofitted), and budget for a wax inhibitor or pour-point depressant.",
                "Deposition rate at the design wall temperature; gel-strength restart pressure "
                "if the pour point is above seabed temperature"))
        else:
            rows.append(_row("Wax deposition", LOW,
                             f"coldest point {min_temp_c:.1f} °C stays above WAT {wat:.1f} °C",
                             "The fluid does not reach the wax appearance temperature at design rate.",
                             "Re-check at turndown and late life — the margin closes as rates fall.",
                             ""))
        if inputs.pour_point_c is not None and inputs.pour_point_c > seabed_temp_c:
            rows.append(_row(
                "Gelling on shutdown", HIGH,
                f"pour point {inputs.pour_point_c:.1f} °C above seabed {seabed_temp_c:.1f} °C",
                "A line left to cool below the pour point gels, and the pressure needed to "
                "break the gel can exceed the system rating.",
                "Displace to dead oil or diesel before a planned shutdown, keep an unplanned "
                "shutdown inside the no-touch time, and calculate the restart pressure.",
                "Gel-strength and restart-pressure measurement on live fluid"))

    # ── asphaltenes ──
    if inputs.asphaltene_wt_pct is None and inputs.saturation_pressure_bara is None:
        rows.append(_row(
            "Asphaltenes", UNKNOWN, "no SARA or onset data",
            "Asphaltene precipitation is driven by pressure passing through the bubble point, "
            "not by cooling, so it shows up in the well and at the choke rather than along the "
            "flowline. Light, gassy oils with a low asphaltene content are often the worst.",
            "SARA analysis plus a depressurisation onset test if the oil is light and gassy.",
            "SARA, asphaltene onset pressure, de Boer screening plot"))
    else:
        risky = (fluid.api > 30 and fluid.gor_scf_stb > 800
                 and (inputs.asphaltene_wt_pct or 0) > 0.5)
        crosses = (inputs.saturation_pressure_bara is not None
                   and min_pressure_bara < inputs.saturation_pressure_bara)
        risk = HIGH if (risky and crosses) else (MEDIUM if (risky or crosses) else LOW)
        rows.append(_row(
            "Asphaltenes", risk,
            f"{fluid.api:.0f}° API, GOR {fluid.gor_scf_stb:,.0f} scf/stb"
            + (f", onset {inputs.saturation_pressure_bara:.0f} bara vs {min_pressure_bara:.0f} bara"
               if inputs.saturation_pressure_bara else ""),
            "Deposits at the point of lowest pressure — tubing, choke, and any restriction — "
            "and is much harder to remove than wax.",
            "Keep the operating envelope above the onset pressure where possible, and provide "
            "an injection point upstream of the choke for a dispersant.",
            "Onset pressure on live fluid; dispersant selection test"))

    # ── scale ──
    scale_risk, scale_basis = LOW, "low salinity, no injection water"
    if inputs.sulphate_injection:
        scale_risk = HIGH
        scale_basis = "seawater injection without sulphate removal"
    elif (inputs.barium_mg_l or 0) > 50:
        scale_risk = MEDIUM
        scale_basis = f"{inputs.barium_mg_l:.0f} mg/l barium in formation water"
    elif inputs.formation_water_salinity_wt_pct is None:
        scale_risk, scale_basis = UNKNOWN, "no water analysis"
    elif inputs.formation_water_salinity_wt_pct > 15:
        scale_risk = MEDIUM
        scale_basis = f"{inputs.formation_water_salinity_wt_pct:.0f} wt % salinity — halite on cooling"
    rows.append(_row(
        "Scale", scale_risk, scale_basis,
        "Barium and strontium sulphate form where injected seawater meets formation water and "
        "are effectively insoluble once laid down; carbonate scale follows the CO₂ flash across "
        "the choke; halite drops out when a high-salinity brine cools.",
        "Sulphate removal on the injection water, continuous scale inhibitor down the "
        "umbilical or by squeeze, and a scale-inhibitor injection point at the wellhead.",
        "Full water analysis (formation and injection), scale-prediction run, inhibitor "
        "compatibility with the chosen hydrate inhibitor"))

    # ── emulsions ──
    if wc < 0.05:
        rows.append(_row("Emulsions", LOW, f"water cut {wc:.0%}",
                         "Too little water to form a stable emulsion.",
                         "Revisit as water cut rises — the problem arrives with the water.", ""))
    else:
        near_inversion = 0.3 <= wc <= 0.7
        risk = HIGH if (near_inversion and fluid.api < 30) else (MEDIUM if wc >= 0.3 else LOW)
        rows.append(_row(
            "Emulsions", risk, f"water cut {wc:.0%}, {fluid.api:.0f}° API",
            "Water-in-oil emulsions peak in viscosity near the inversion point, typically "
            "50–70 % water cut. The viscosity rise shows up as back-pressure the network model "
            "does not see, and as separator carry-over at the host.",
            "Demulsifier at the wellhead or the tree, sized on a bottle test with the real "
            "crude; confirm the host separator can take the residence time and that its "
            "chemicals are compatible with yours.",
            "Bottle tests across the water-cut range, emulsion viscosity against shear, "
            "inversion point"))

    # ── corrosion ──
    if inputs.co2_mol_pct is None and inputs.h2s_ppm is None:
        rows.append(_row(
            "Internal corrosion", UNKNOWN, "no CO₂ / H₂S analysis",
            "CO₂ partial pressure with free water decides whether carbon steel is viable at "
            "all, and the answer changes the flowline material — one of the largest single "
            "cost lines in the concept.",
            "Get the gas composition early: material selection cannot wait for FEED.",
            "CO₂ and H₂S content, water chemistry and pH, de Waard-Milliams or NORSOK M-506 rate"))
    else:
        co2 = inputs.co2_mol_pct or 0.0
        h2s = inputs.h2s_ppm or 0.0
        cra = "cra" in inputs.material.lower() or "clad" in inputs.material.lower()
        if cra:
            risk, basis = LOW, f"{inputs.material}, {co2:.1f} mol % CO₂"
        elif co2 >= 2.0 or h2s >= 100:
            risk, basis = HIGH, f"{co2:.1f} mol % CO₂, {h2s:.0f} ppm H₂S on {inputs.material}"
        elif co2 >= 0.5:
            risk, basis = MEDIUM, f"{co2:.1f} mol % CO₂ on {inputs.material}"
        else:
            risk, basis = LOW, f"{co2:.1f} mol % CO₂ on {inputs.material}"
        mitigation = ("Carbon steel with a continuous corrosion inhibitor and a corrosion "
                      "allowance, or clad/CRA where the inhibitor cannot be relied on. "
                      "On a wet-gas tie-back with MEG, pH stabilisation is the established "
                      "NCS route and removes the inhibitor dependency.")
        rows.append(_row("Internal corrosion", risk, basis,
                         "Sets the pipe material, the corrosion allowance and whether a "
                         "chemical injection line is mandatory rather than optional.",
                         mitigation,
                         "NORSOK M-506 rate at the design conditions; inhibitor availability "
                         "assumption; water wetting along the profile"))
        if h2s > 0:
            rows.append(_row(
                "Sour service", HIGH if h2s >= 100 else MEDIUM, f"{h2s:.0f} ppm H₂S",
                "H₂S brings sulphide stress cracking, and the limits in NACE MR0175 / ISO 15156 "
                "govern every wetted component, not just the pipe.",
                "Qualify all wetted materials to NACE MR0175 / ISO 15156 and confirm the host "
                "can take a sour stream.",
                "H₂S partial pressure at the lowest temperature, and a souring forecast if "
                "seawater injection is planned"))
    if inputs.sulphate_injection and not (inputs.h2s_ppm or 0):
        rows.append(_row(
            "Reservoir souring", MEDIUM, "seawater injection planned, no H₂S today",
            "Sulphate-reducing bacteria on injected seawater generate H₂S over years. A field "
            "that is sweet at start-up can turn sour mid-life, after the materials are in the "
            "water.",
            "Run a souring forecast now and decide whether to pre-qualify materials to sour "
            "service, or to remove sulphate from the injection water.",
            "Souring model; reservoir temperature and sulphate concentration"))

    # ── sand and erosion ──
    if inputs.sand_expected:
        rows.append(_row(
            "Sand production and erosion", HIGH, "sand expected",
            "Sand erodes chokes, bends and the tree at a rate that the standard API 14E "
            "erosional velocity does not cover — that constant assumes a clean, non-erosive "
            "fluid.",
            "Lower the erosional velocity constant, sand screens or gravel pack downhole, "
            "erosion-resistant choke trim, sand detection at the tree, and a way to remove "
            "accumulated sand from the host separator.",
            "Sand rate forecast and particle size; erosion modelling to DNV-RP-O501 at the "
            "bends and the choke"))
    if fluid.gas_sg > 0 and tieback_km > 20:
        rows.append(_row(
            "Under-deposit and dead-leg corrosion", MEDIUM if tieback_km > 30 else LOW,
            f"{tieback_km:.0f} km tie-back",
            "Long lines accumulate water and solids at low points, where an inhibitor does not "
            "reach and bacteria can establish; dead legs and the pig trap are the usual sites.",
            "Design for round-trip pigging, plan a biocide batch programme, and keep dead legs "
            "short.",
            "Bacterial count on produced water; low-point survey from the seabed profile"))
    if inputs.naphthenate_risk or (inputs.tan_mg_koh_g or 0) > 0.5:
        rows.append(_row(
            "Naphthenate and soap formation", MEDIUM,
            f"TAN {inputs.tan_mg_koh_g:.1f} mg KOH/g" if inputs.tan_mg_koh_g else "high-TAN crude",
            "Calcium naphthenate deposits at the point of CO₂ flash and fouls the separator "
            "and the water-treatment train — a topsides problem created by a subsea decision.",
            "Acid or naphthenate inhibitor injection upstream of the first separation stage.",
            "TAN, ARN acid content, calcium in the formation water"))
    if inputs.mercury_expected:
        rows.append(_row(
            "Mercury", MEDIUM, "mercury expected in the reservoir fluid",
            "Attacks aluminium heat exchangers downstream and is an HSE issue for anyone "
            "opening equipment.",
            "Confirm the host can accept the stream, and plan mercury removal if it exports to "
            "an LNG or cryogenic plant.",
            "Mercury content by sampling at the wellhead"))

    rows.sort(key=lambda r: RISK_ORDER.get(r["risk"], 5))
    return rows


def summary(rows: List[dict]) -> Dict[str, int]:
    out = {HIGH: 0, MEDIUM: 0, LOW: 0, UNKNOWN: 0, NA: 0}
    for r in rows:
        out[r["risk"]] = out.get(r["risk"], 0) + 1
    return out


def verdict(rows: List[dict]) -> str:
    c = summary(rows)
    if c[HIGH]:
        return f"{c[HIGH]} production-chemistry threat(s) need a plan before DG3"
    if c[UNKNOWN]:
        return f"{c[UNKNOWN]} threat(s) cannot be assessed yet — the fluid data is missing"
    if c[MEDIUM]:
        return f"Manageable — {c[MEDIUM]} item(s) to carry into FEED"
    return "No production-chemistry threat flagged on the data entered"


def data_gaps(rows: List[dict]) -> List[str]:
    """The tests and analyses this screen asked for, de-duplicated."""
    seen, out = set(), []
    for r in rows:
        for part in str(r.get("data_needed", "")).split(";"):
            t = part.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out
