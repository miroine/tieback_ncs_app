import sys, math
import tb_chemistry as ch, tb_thermal as th, tb_multiphase as mp
from _harness import Suite
S = Suite("test_chemistry")

# ── inhibitor concentration ────────────────────────────────────────────────
def inversion_round_trips():
    """The concentration we ask for must give back the depression we wanted —
    otherwise the sizing and the hydrate margin disagree with each other."""
    for inh in ("MEG", "Methanol"):
        for dt_c in (2.0, 5.0, 8.0, 12.0):
            w = ch.hammerschmidt_wt_pct(dt_c, inh)
            back = th.hammerschmidt_depression_f(inh, w) / 1.8
            assert abs(back - dt_c) < 1e-6, f"{inh} {dt_c}: got {back}"
    return True
S.check("required wt % inverts tb_thermal's depression exactly", inversion_round_trips)

S.check("no subcooling needs no inhibitor",
        lambda: ch.hammerschmidt_wt_pct(0, "MEG") == 0 and ch.hammerschmidt_wt_pct(-3, "Methanol") == 0)
S.check("more subcooling needs more inhibitor",
        lambda: ch.hammerschmidt_wt_pct(12, "MEG") > ch.hammerschmidt_wt_pct(6, "MEG"))
S.check("methanol needs less mass than MEG for the same depression",
        lambda: ch.hammerschmidt_wt_pct(8, "Methanol") < ch.hammerschmidt_wt_pct(8, "MEG"))
S.raises("unknown inhibitor refused", ValueError, lambda: ch.hammerschmidt_wt_pct(5, "Ethanol"))
S.check("concentration never reaches 100 %", lambda: ch.hammerschmidt_wt_pct(500, "MEG") <= 99.0)

S.check("Nielsen-Bucklin agrees with Hammerschmidt in the dilute range",
        lambda: abs(ch.nielsen_bucklin_wt_pct(6) - ch.hammerschmidt_wt_pct(6, "Methanol")) < 2.0)
S.check("the two correlations diverge where Hammerschmidt runs out",
        lambda: ch.nielsen_bucklin_wt_pct(25) - ch.hammerschmidt_wt_pct(25, "Methanol") > 3.0)
S.check("Nielsen-Bucklin is zero at no subcooling", lambda: ch.nielsen_bucklin_wt_pct(0) == 0.0)

# ── duty sizing ────────────────────────────────────────────────────────────
def duty_mass_balance():
    """The aqueous phase must come out at the concentration we asked for."""
    d = ch.inhibitor_duty("MEG", 8.0, 200.0, lean_wt_pct=80.0, loss_fraction=0.0)
    water_te = 200.0 * 1.02
    inhibitor_in_water = d.aqueous_te_d * d.lean_wt_pct / 100.0
    water_from_lean = d.aqueous_te_d * (1 - d.lean_wt_pct / 100.0)
    got = 100.0 * inhibitor_in_water / (inhibitor_in_water + water_te + water_from_lean)
    assert abs(got - d.wt_pct) < 1e-6, f"{got} vs {d.wt_pct}"
    return True
S.check("injection rate closes the aqueous mass balance", duty_mass_balance)

S.check("more water needs proportionally more inhibitor",
        lambda: abs(ch.inhibitor_duty("MEG", 8, 400).injection_te_d
                    - 2 * ch.inhibitor_duty("MEG", 8, 200).injection_te_d) < 1e-6)
S.check("no water needs no inhibitor",
        lambda: ch.inhibitor_duty("MEG", 8, 0).injection_te_d == 0.0)
S.check("losses add to the injection rate",
        lambda: ch.inhibitor_duty("Methanol", 8, 200, loss_fraction=0.25).injection_te_d
        > ch.inhibitor_duty("Methanol", 8, 200, loss_fraction=0.0).injection_te_d)
S.check("regeneration cuts the make-up, not the injection rate",
        lambda: (lambda a, b: abs(a.injection_te_d - b.injection_te_d) < 1e-9
                 and a.annual_te < b.annual_te / 5)(
            ch.inhibitor_duty("MEG", 8, 200, regenerated=True),
            ch.inhibitor_duty("MEG", 8, 200, regenerated=False)))
S.check("volume follows density",
        lambda: ch.inhibitor_duty("MEG", 8, 200).injection_m3_d
        < ch.inhibitor_duty("MEG", 8, 200).injection_te_d * 1000 / 900)
S.raises("negative water rate refused", ValueError, lambda: ch.inhibitor_duty("MEG", 8, -5))
S.raises("unknown inhibitor duty refused", ValueError, lambda: ch.inhibitor_duty("Glycerol", 8, 100))

def correlation_limit_flagged():
    """Past the Hammerschmidt range the answer must be marked, not quietly used."""
    deep = ch.inhibitor_duty("Methanol", 30.0, 100.0)
    assert not deep.correlation_valid and "Hammerschmidt" in deep.note, deep
    assert ch.inhibitor_duty("Methanol", 5.0, 100.0).correlation_valid
    return True
S.check("a concentration beyond the correlation range is flagged", correlation_limit_flagged)

# ── recommendation ─────────────────────────────────────────────────────────
def batch_duty_picks_methanol():
    rec = ch.recommend_inhibitor(ch.InhibitorCase(duty="start-up and shutdown only"))
    assert rec["recommended"] == "Methanol", rec["recommended"]
    assert "regeneration" in rec["because"] or "batch" in rec["because"]
    return True
S.check("batch duty points to methanol", batch_duty_picks_methanol)

def continuous_high_water_picks_meg():
    rec = ch.recommend_inhibitor(ch.InhibitorCase(duty="continuous", water_rate_sm3_d=500,
                                                  subcooling_c=10, field_life_years=20))
    assert rec["recommended"] == "MEG", rec
    return True
S.check("continuous duty with real water rates points to MEG", continuous_high_water_picks_meg)

S.check("an existing host MEG system decides it",
        lambda: ch.recommend_inhibitor(ch.InhibitorCase(host_has_meg_system=True))["recommended"] == "MEG")
S.check("both sides of the argument are always given",
        lambda: (lambda r: len(r["for_meg"]) >= 3 and len(r["for_methanol"]) >= 3)(
            ch.recommend_inhibitor(ch.InhibitorCase())))
S.check("the recommendation carries its caveat",
        lambda: "Screening only" in ch.recommend_inhibitor(ch.InhibitorCase())["caveat"])
S.check("CO2 brings up pH stabilisation",
        lambda: any("pH stabilis" in s for s in
                    ch.recommend_inhibitor(ch.InhibitorCase(co2_mol_pct=3.0))["for_meg"]))
S.check("condensate raises the methanol export-spec argument",
        lambda: any("condensate" in s for s in
                    ch.recommend_inhibitor(ch.InhibitorCase(condensate_rate_sm3_d=500))["for_meg"]))
S.check("salty formation water raises the reclaimer argument",
        lambda: any("reclaimer" in s for s in ch.recommend_inhibitor(
            ch.InhibitorCase(formation_water_salinity_wt_pct=12))["for_methanol"]))
S.check("both options are always sized",
        lambda: set(ch.recommend_inhibitor(ch.InhibitorCase())["duties"]) == {"MEG", "Methanol"})

# ── production chemistry screen ────────────────────────────────────────────
OIL = mp.Fluid(api=32.0, gas_sg=0.75, gor_scf_stb=600.0, water_cut=0.4)
GAS = mp.Fluid(api=52.0, gas_sg=0.65, gor_scf_stb=9000.0, water_cut=0.05)


def issues_of(rows):
    return {r["issue"]: r for r in rows}


def unknown_when_no_data():
    rows = screen_default = ch.screen(OIL, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0)
    d = issues_of(rows)
    for name in ("Wax deposition", "Asphaltenes", "Internal corrosion"):
        assert d[name]["risk"] == ch.UNKNOWN, (name, d[name]["risk"])
        assert d[name]["data_needed"], f"{name} must say what test would settle it"
    return True
S.check("missing fluid data is reported as unknown with the test needed", unknown_when_no_data)


def wax_below_wat():
    rows = ch.screen(OIL, ch.ChemistryInputs(wax_appearance_c=30.0), 12.0, 14.0, 6.0, 80.0)
    w = issues_of(rows)["Wax deposition"]
    assert w["risk"] == ch.HIGH, w
    assert "18.0 °C below" in w["basis"], w["basis"]
    return True
S.check("a cold line below the WAT is a high wax risk", wax_below_wat)

S.check("a line above the WAT is low risk",
        lambda: issues_of(ch.screen(OIL, ch.ChemistryInputs(wax_appearance_c=8.0),
                                    25.0, 30.0, 6.0, 80.0))["Wax deposition"]["risk"] == ch.LOW)
S.check("a pour point above seabed temperature raises gelling separately",
        lambda: "Gelling on shutdown" in issues_of(ch.screen(
            OIL, ch.ChemistryInputs(wax_appearance_c=30.0, pour_point_c=15.0), 12.0, 14.0, 6.0, 80.0)))
S.check("no gelling row when the pour point is below seabed temperature",
        lambda: "Gelling on shutdown" not in issues_of(ch.screen(
            OIL, ch.ChemistryInputs(wax_appearance_c=30.0, pour_point_c=0.0), 12.0, 14.0, 6.0, 80.0)))

S.check("hydrate margin drives the hydrate row",
        lambda: issues_of(ch.screen(OIL, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0,
                                    hydrate_margin_c=-2.0))["Hydrates"]["risk"] == ch.HIGH)
S.check("a healthy hydrate margin is low risk",
        lambda: issues_of(ch.screen(OIL, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0,
                                    hydrate_margin_c=6.0))["Hydrates"]["risk"] == ch.LOW)
S.check("no solve means the hydrate row is unknown, not low",
        lambda: issues_of(ch.screen(OIL, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0))["Hydrates"]["risk"]
        == ch.UNKNOWN)

S.check("seawater injection without sulphate removal is a high scale risk",
        lambda: issues_of(ch.screen(OIL, ch.ChemistryInputs(sulphate_injection=True),
                                    12.0, 14.0, 6.0, 80.0))["Scale"]["risk"] == ch.HIGH)
S.check("barium in the formation water raises scale",
        lambda: issues_of(ch.screen(OIL, ch.ChemistryInputs(barium_mg_l=200.0),
                                    12.0, 14.0, 6.0, 80.0))["Scale"]["risk"] == ch.MEDIUM)
S.check("souring is raised when injection is planned but the fluid is sweet today",
        lambda: "Reservoir souring" in issues_of(ch.screen(
            OIL, ch.ChemistryInputs(sulphate_injection=True, co2_mol_pct=1.0, h2s_ppm=0.0),
            12.0, 14.0, 6.0, 80.0)))

S.check("emulsions peak near the inversion point for a heavier crude",
        lambda: issues_of(ch.screen(mp.Fluid(api=26.0, gas_sg=0.75, gor_scf_stb=400.0, water_cut=0.5),
                                    ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0,
                                    water_cut=0.5))["Emulsions"]["risk"] == ch.HIGH)
S.check("a lighter crude at the same water cut is a lesser emulsion risk",
        lambda: issues_of(ch.screen(OIL, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0,
                                    water_cut=0.5))["Emulsions"]["risk"] == ch.MEDIUM)
S.check("a dry well has no emulsion risk",
        lambda: issues_of(ch.screen(GAS, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0,
                                    water_cut=0.01))["Emulsions"]["risk"] == ch.LOW)

S.check("CO2 on carbon steel is a corrosion risk",
        lambda: issues_of(ch.screen(GAS, ch.ChemistryInputs(co2_mol_pct=3.0),
                                    12.0, 14.0, 6.0, 80.0))["Internal corrosion"]["risk"] == ch.HIGH)
S.check("the same CO2 on CRA is not",
        lambda: issues_of(ch.screen(GAS, ch.ChemistryInputs(co2_mol_pct=3.0, material="13Cr CRA"),
                                    12.0, 14.0, 6.0, 80.0))["Internal corrosion"]["risk"] == ch.LOW)
S.check("H2S adds a sour-service row",
        lambda: "Sour service" in issues_of(ch.screen(GAS, ch.ChemistryInputs(co2_mol_pct=1.0, h2s_ppm=300),
                                                      12.0, 14.0, 6.0, 80.0)))
S.check("sand brings its own erosion row",
        lambda: issues_of(ch.screen(OIL, ch.ChemistryInputs(sand_expected=True), 12.0, 14.0, 6.0, 80.0)
                          )["Sand production and erosion"]["risk"] == ch.HIGH)
S.check("a high-TAN crude raises naphthenates",
        lambda: "Naphthenate and soap formation" in issues_of(ch.screen(
            OIL, ch.ChemistryInputs(tan_mg_koh_g=1.2), 12.0, 14.0, 6.0, 80.0)))
S.check("a long tie-back raises under-deposit corrosion",
        lambda: "Under-deposit and dead-leg corrosion" in issues_of(ch.screen(
            OIL, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0, tieback_km=40)))

S.check("rows come back worst first",
        lambda: (lambda rs: [ch.RISK_ORDER[r["risk"]] for r in rs]
                 == sorted(ch.RISK_ORDER[r["risk"]] for r in rs))(
            ch.screen(OIL, ch.ChemistryInputs(sulphate_injection=True, wax_appearance_c=30.0),
                      12.0, 14.0, 6.0, 80.0, hydrate_margin_c=-1.0)))
S.check("every row carries a mitigation",
        lambda: all(r["mitigation"] for r in ch.screen(OIL, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0)))

def verdicts():
    high = ch.screen(OIL, ch.ChemistryInputs(sulphate_injection=True), 12.0, 14.0, 6.0, 80.0,
                     hydrate_margin_c=-2.0)
    assert "before DG3" in ch.verdict(high), ch.verdict(high)
    clean = ch.screen(GAS, ch.ChemistryInputs(wax_appearance_c=0.0, co2_mol_pct=0.1,
                                              asphaltene_wt_pct=0.01, formation_water_salinity_wt_pct=3.0,
                                              saturation_pressure_bara=10.0),
                      25.0, 30.0, 6.0, 80.0, water_cut=0.01, hydrate_margin_c=8.0)
    assert ch.summary(clean)[ch.HIGH] == 0, ch.summary(clean)
    return True
S.check("the verdict reflects the worst row", verdicts)

S.check("summary counts every row",
        lambda: (lambda rs: sum(ch.summary(rs).values()) == len(rs))(
            ch.screen(OIL, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0)))
S.check("data gaps are collected without duplicates",
        lambda: (lambda g: len(g) == len(set(x.lower() for x in g)) and len(g) > 3)(
            ch.data_gaps(ch.screen(OIL, ch.ChemistryInputs(), 12.0, 14.0, 6.0, 80.0))))

sys.exit(0 if S.report() else 1)
