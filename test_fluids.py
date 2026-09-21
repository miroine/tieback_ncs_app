import sys, copy, yaml
import tb_fluids as f, tb_network as n, tb_catalog as c, tb_map as m, tb_flowassurance as fa, tb_project, tb_cost, tb_schedule
from _harness import Suite
S = Suite("test_fluids")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
WELLS = ["W1", "W2", "W3", "W4"]

# ── McCain classification ───────────────────────────────────────────────────
S.check("low GOR is a black oil", lambda: f.classify(100) == "black oil")
S.check("black/volatile boundary at 1750 scf/STB",
        lambda: f.classify(1749 / 5.6146, 45) == "black oil" and f.classify(1751 / 5.6146, 45) == "volatile oil")
S.check("volatile/condensate boundary at 3200 scf/STB",
        lambda: f.classify(3199 / 5.6146) == "volatile oil" and f.classify(3201 / 5.6146) == "gas condensate")
S.check("condensate/wet gas boundary at 15 000 scf/STB",
        lambda: f.classify(14999 / 5.6146) == "gas condensate" and f.classify(15001 / 5.6146) == "wet gas")
S.check("wet/dry gas boundary at 100 000 scf/STB",
        lambda: f.classify(99999 / 5.6146) == "wet gas" and f.classify(100001 / 5.6146) == "dry gas")
S.check("a heavy oil in the volatile GOR band stays a black oil",
        lambda: f.classify(400, api=30) == "black oil" and f.classify(400, api=45) == "volatile oil")
S.raises("negative GOR refused", ValueError, lambda: f.classify(-1))
S.check("reservoir fluid maps to the well's main fluid",
        lambda: [f.well_fluid_from_reservoir_fluid(k) for k in f.RESERVOIR_FLUIDS]
        == ["oil", "oil", "gas condensate", "gas", "gas"])
S.raises("unknown reservoir fluid refused", ValueError, lambda: f.well_fluid_from_reservoir_fluid("tar"))

# ── reservoirs ──────────────────────────────────────────────────────────────
S.raises("a reservoir needs a name", ValueError, lambda: f.Reservoir(name=" "))
S.raises("unknown reservoir fluid refused on the record", ValueError, lambda: f.Reservoir("R", fluid="bitumen"))
S.raises("water cut must be a fraction", ValueError, lambda: f.Reservoir("R", water_cut=1.2))
S.check("every preset is self-consistent",
        lambda: all(f.new_reservoir("R", k).consistent() for k in f.RESERVOIR_FLUIDS))
S.check("a black oil entered with a gas GOR is flagged",
        lambda: not f.Reservoir("R", fluid="black oil", gor_sm3_sm3=5000).consistent())
S.check("classified() reports what the GOR says",
        lambda: f.Reservoir("R", fluid="black oil", gor_sm3_sm3=5000).classified() == "wet gas")


def reservoir_crud():
    lay = demo()
    f.set_reservoir(lay, f.new_reservoir("Brent", "black oil"))
    f.set_reservoir(lay, f.new_reservoir("Garn", "gas condensate"))
    assert set(f.reservoirs(lay)) == {"Brent", "Garn"}
    f.assign_reservoir(lay, ["W1", "W2"], "Garn")
    freed = f.remove_reservoir(lay, "Garn")
    assert sorted(freed) == ["W1", "W2"], freed
    assert "reservoir" not in lay.nodes["W1"].attrs, "a removed reservoir must not leave dangling wells"
    assert set(f.reservoirs(lay)) == {"Brent"}
    return True
S.check("add, assign and remove reservoirs", reservoir_crud)
S.raises("assigning an unknown reservoir refused", ValueError,
         lambda: f.assign_reservoir(demo(), ["W1"], "Nowhere"))
S.check("assigning skips anything that is not a well",
        lambda: (lambda lay: (f.set_reservoir(lay, f.new_reservoir("R")),
                              f.assign_reservoir(lay, ["W1", "HOST_A"], "R"))[1])(demo()) == ["W1"])


def bad_record_does_not_hide_others():
    lay = demo()
    f.set_reservoir(lay, f.new_reservoir("Good"))
    lay.reservoirs["Bad"] = {"name": "Bad", "fluid": "lava"}
    return set(f.reservoirs(lay)) == {"Good"}
S.check("one corrupt reservoir record does not hide the rest", bad_record_does_not_hide_others)

# ── which fluid a well produces, and why we think so ───────────────────────
S.check("an unassigned well says so", lambda: f.well_fluid(demo(), "W1") == ("oil", f.UNASSIGNED))
S.check("is_assigned distinguishes a placeholder",
        lambda: not f.is_assigned(f.UNASSIGNED) and f.is_assigned("set on the well"))


def precedence():
    """Stated beats reservoir beats GOR."""
    lay = demo()
    lay.nodes["W1"].attrs["fa"] = {"gor_sm3_sm3": 5000.0, "api": 55.0}
    assert f.well_fluid(lay, "W1")[0] == "gas" and "GOR" in f.well_fluid(lay, "W1")[1]
    f.set_reservoir(lay, f.new_reservoir("Brent", "black oil"))
    f.assign_reservoir(lay, ["W1"], "Brent")
    assert f.well_fluid(lay, "W1") == ("oil", "from reservoir Brent"), f.well_fluid(lay, "W1")
    f.set_well_fluid(lay, ["W1"], "water injector")
    assert f.well_fluid(lay, "W1") == ("water injector", "set on the well")
    f.set_well_fluid(lay, ["W1"], None)
    assert f.well_fluid(lay, "W1")[0] == "oil", "clearing the stated fluid falls back to the reservoir"
    return True
S.check("stated fluid beats reservoir beats GOR", precedence)
S.raises("unknown well fluid refused", ValueError, lambda: f.set_well_fluid(demo(), ["W1"], "steam"))
S.check("injector recognised", lambda: (lambda lay: (f.set_well_fluid(lay, ["W2"], "gas injector"),
                                                     f.is_injector(lay, "W2"))[1])(demo()))


def table_and_summary():
    lay = demo()
    f.set_well_fluid(lay, ["W1", "W2"], "gas")
    f.set_well_fluid(lay, ["W3"], "oil")
    rows = {r["id"]: r for r in f.fluid_table(lay)}
    assert set(rows) == set(WELLS), rows
    assert rows["W1"]["fluid"] == "gas" and rows["W1"]["source"] == "set on the well"
    s = f.summary(lay)
    assert s["gas"] == 2 and s["oil"] == 2, s          # W4 unassigned counts as its placeholder
    assert f.field_type(lay) == "mixed oil and gas development"
    return True
S.check("fluid table and development type", table_and_summary)
S.check("an all-gas field is called one",
        lambda: (lambda lay: (f.set_well_fluid(lay, WELLS, "gas"), f.field_type(lay))[1])(demo()) == "gas development")
S.check("a condensate field is called one",
        lambda: (lambda lay: (f.set_well_fluid(lay, WELLS, "gas condensate"), f.field_type(lay))[1])(demo())
        == "gas-condensate development")

# ── the fluid a line carries, from the wells upstream ──────────────────────
def lines_follow_the_wells():
    lay = demo()
    f.set_well_fluid(lay, WELLS, "gas")
    assert m.fluid_of(lay, lay.edges["FL1"]) == "gas", m.fluid_of(lay, lay.edges["FL1"])
    f.set_well_fluid(lay, WELLS, "oil")
    assert m.fluid_of(lay, lay.edges["FL1"]) == "oil"
    f.set_well_fluid(lay, WELLS, "gas condensate")
    assert m.fluid_of(lay, lay.edges["FL1"]) == "condensate"
    return True
S.check("a flowline carries what its wells produce", lines_follow_the_wells)


def commingled_is_multiphase():
    lay = demo()
    f.set_well_fluid(lay, ["W1", "W2"], "oil")
    f.set_well_fluid(lay, ["W3", "W4"], "gas")
    return m.fluid_of(lay, lay.edges["FL1"]) == "multiphase"
S.check("oil and gas wells commingled make a multiphase line", commingled_is_multiphase)
S.check("unassigned wells leave the line at its catalogue default",
        lambda: m.fluid_of(demo(), demo().edges["FL1"]) == "multiphase")


def injectors_do_not_colour_production():
    lay = demo()
    f.set_well_fluid(lay, ["W1", "W2", "W3"], "gas")
    f.set_well_fluid(lay, ["W4"], "water injector")
    return m.fluid_of(lay, lay.edges["FL1"]) == "gas"
S.check("an injector on the same template does not turn a gas line multiphase",
        injectors_do_not_colour_production)
S.check("a line stated on the edge still wins",
        lambda: (lambda lay: (f.set_well_fluid(lay, WELLS, "gas"),
                              lay.edges["FL1"].attrs.update(fluid="oil"),
                              m.fluid_of(lay, lay.edges["FL1"]))[2])(demo()) == "oil")
S.check("the umbilical is not recoloured by its wells",
        lambda: (lambda lay: (f.set_well_fluid(lay, WELLS, "gas"),
                              m.fluid_of(lay, lay.edges["UMB1"]))[1])(demo()) == "control")


def fluid_colour_mode_uses_it():
    lay = demo()
    f.set_well_fluid(lay, WELLS, "gas")
    p = m.build_payload(lay, None, m.DisplaySettings(color_mode="fluid"))
    fl1 = next(e for e in p["edges"] if e["id"] == "FL1")
    return fl1["color"] == m.FLUID_COLORS["gas"] and fl1["fluid"] == "gas"
S.check("fluid colour mode draws a gas tie-back red", fluid_colour_mode_uses_it)

# ── the map payload ─────────────────────────────────────────────────────────
def payload_marks_wells():
    lay = demo()
    f.set_well_fluid(lay, ["W1"], "gas")
    nodes = {x["id"]: x for x in m.build_payload(lay)["nodes"]}
    assert nodes["W1"]["fluid"] == "gas" and nodes["W1"]["fluid_color"] == f.FLUID_COLORS["gas"]
    assert nodes["W1"]["fluid_source"] == "set on the well"
    assert nodes["W2"]["fluid_color"] == "", "an unassigned well must not be drawn as if it were oil"
    assert nodes["HOST_A"]["fluid"] == "", "only wells carry a fluid"
    return True
S.check("the map gets each well's fluid, colour and source", payload_marks_wells)

# ── gas wells, specified the way gas wells are specified ───────────────────
S.check("gas rate and CGR give the condensate rate",
        lambda: f.gas_well_rates(2.0, 50.0) == (100.0, 20000.0))
S.check("gas_well_rates and cgr_from are inverses",
        lambda: (lambda cond, gor: abs(f.cgr_from(cond, gor)[0] - 3.5) < 1e-9
                 and abs(f.cgr_from(cond, gor)[1] - 40.0) < 1e-9)(*f.gas_well_rates(3.5, 40.0)))
S.raises("a zero CGR has no liquid to key on", ValueError, lambda: f.gas_well_rates(2.0, 0.0))
S.raises("negative gas rate refused", ValueError, lambda: f.gas_well_rates(-1.0, 30.0))
S.check("a gas well from its gas rate classifies as gas",
        lambda: f.classify(f.gas_well_rates(2.0, 50.0)[1]) in ("wet gas", "dry gas"))

# ── reservoir PVT into the flow-assurance inputs ───────────────────────────
S.check("reservoir PVT becomes valid WellFA inputs",
        lambda: fa.WellFA(**f.wellfa_from_reservoir(f.new_reservoir("R", "gas condensate"))).gor_sm3_sm3 == 1600.0)


def apply_keeps_rates():
    lay = demo()
    fa.set_well_inputs(lay, "W1", fa.WellFA(oil_sm3_d=777.0, wht_c=66.0, max_whp_bara=133.0))
    f.set_reservoir(lay, f.new_reservoir("Garn", "gas condensate"))
    f.assign_reservoir(lay, ["W1", "W2"], "Garn")
    fa.set_well_inputs(lay, "W1", fa.WellFA(oil_sm3_d=777.0, wht_c=66.0, max_whp_bara=133.0,
                                            gor_sm3_sm3=1500.0, api=50.0))   # already a condensate well
    out = f.apply_reservoir_to_wells(lay)
    assert sorted(out["updated"]) == ["W1", "W2"], out
    assert out["rate_reset"] == [], out
    w1 = fa.well_inputs(lay)["W1"]
    assert w1.gor_sm3_sm3 == 1600.0 and w1.api == 52.0, "the fluid description must change"
    assert w1.oil_sm3_d == 777.0 and w1.wht_c == 66.0 and w1.max_whp_bara == 133.0, \
        "the well's own rate and wellhead conditions must be kept"
    return True
S.check("applying a reservoir changes the fluid, keeps the well's rate", apply_keeps_rates)


def apply_skips_injectors():
    lay = demo()
    f.set_reservoir(lay, f.new_reservoir("R", "black oil"))
    f.assign_reservoir(lay, ["W1"], "R")
    f.set_well_fluid(lay, ["W1"], "water injector")
    return f.apply_reservoir_to_wells(lay)["updated"] == []
S.check("injectors are not given producer PVT", apply_skips_injectors)


def family_change_resets_the_rate():
    """An oil well moved onto a gas reservoir must not carry its liquid rate across
    with a gas GOR — that would give 1 000 Sm³/d × 6 000 = 6 MSm³/d."""
    lay = demo()
    fa.set_well_inputs(lay, "W1", fa.WellFA(oil_sm3_d=1000.0, gor_sm3_sm3=150.0, api=32.0,
                                            wht_c=61.0, max_whp_bara=140.0))
    f.set_reservoir(lay, f.new_reservoir("Gas", "wet gas"))
    f.assign_reservoir(lay, ["W1"], "Gas")
    out = f.apply_reservoir_to_wells(lay)
    assert out["rate_reset"] == ["W1"], out
    w1 = fa.well_inputs(lay)["W1"]
    gas = w1.oil_sm3_d * w1.gor_sm3_sm3 / 1e6
    assert abs(gas - f.DEFAULT_GAS_RATE_MSM3_D) < 1e-6, f"gas rate {gas} MSm³/d"
    assert w1.wht_c == 61.0 and w1.max_whp_bara == 140.0, "wellhead conditions are still the well's"
    return True
S.check("changing a well from oil to gas resets its rate and says so", family_change_resets_the_rate)
S.check("condensate counts as the gas family",
        lambda: f.family("gas condensate") == "gas" and f.family("oil") == "oil"
        and f.family("water injector") == "injector")
S.check("a new gas well defaults to a realistic gas rate",
        lambda: abs(f.default_liquid_rate(f.new_reservoir("R", "dry gas")) * 40000.0 / 1e6
                    - f.DEFAULT_GAS_RATE_MSM3_D) < 1e-9)


def flow_assurance_sees_the_gas():
    """End to end: a gas reservoir must actually change the solve, not just the colour."""
    lay = demo()
    base = fa.solve(lay, fa.FASettings())
    f.set_reservoir(lay, f.new_reservoir("Gas", "wet gas"))
    f.assign_reservoir(lay, WELLS, "Gas")
    f.apply_reservoir_to_wells(lay)
    gas = fa.solve(lay, fa.FASettings())
    h0 = next(iter(base.host.values()))
    h1 = next(iter(gas.host.values()))
    assert h1["gas_msm3_d"] > h0["gas_msm3_d"] * 3, (h0["gas_msm3_d"], h1["gas_msm3_d"])
    return True
S.check("assigning a gas reservoir changes the flow-assurance result", flow_assurance_sees_the_gas)

# ── persistence ─────────────────────────────────────────────────────────────
def round_trip():
    lay = demo()
    f.set_reservoir(lay, f.new_reservoir("Brent", "black oil"))
    f.set_reservoir(lay, f.new_reservoir("Garn", "gas condensate"))
    f.assign_reservoir(lay, ["W1"], "Garn")
    f.set_well_fluid(lay, ["W2"], "water injector")
    txt = tb_project.project_to_yaml("P", lay, tb_cost.CostSettings(), tb_schedule.ScheduleSettings())
    back = tb_project.project_from_yaml_full(txt)[1]
    assert set(f.reservoirs(back)) == {"Brent", "Garn"}, f.reservoirs(back)
    assert f.well_fluid(back, "W1") == ("gas condensate", "from reservoir Garn")
    assert f.well_fluid(back, "W2") == ("water injector", "set on the well")
    return True
S.check("reservoirs and well fluids are saved with the project", round_trip)
S.check("an older file with no reservoirs still loads",
        lambda: n.Layout.from_dict({k: v for k, v in copy.deepcopy(FIX).items() if k != "reservoirs"},
                                   c.Catalog()).reservoirs == {})


# ── injectors are not producers ────────────────────────────────────────────
def injector_leaves_the_production_solve():
    lay = demo()
    base = fa.solve(lay, fa.FASettings())
    f.set_well_fluid(lay, ["W4"], "water injector")
    assert "W4" not in fa.well_inputs(lay), "an injector has no production stream"
    res = fa.solve(lay, fa.FASettings())
    assert all(w["well"] != "W4" for w in res.wells)
    h0, h1 = next(iter(base.host.values())), next(iter(res.host.values()))
    assert h1["liquid_sm3_d"] < h0["liquid_sm3_d"], "the host intake must drop by the injector's phantom stream"
    return True
S.check("an injector is taken out of the production solve", injector_leaves_the_production_solve)


def injector_needs_no_production_path():
    lay = demo()
    lay.add_node(n.Node("WI1", "xt_vxt_10k", 60.62, 2.62, label="WI-1"))
    f.set_well_fluid(lay, ["WI1"], "water injector")
    codes = {(x.code, x.element_id) for x in lay.validate()}
    assert ("NO_PRODUCTION_PATH", "WI1") not in codes, "an injector is fed, not produced"
    f.set_well_fluid(lay, ["WI1"], None)
    codes = {(x.code, x.element_id) for x in lay.validate()}
    assert ("NO_PRODUCTION_PATH", "WI1") in codes, "a producer still needs its path"
    return True
S.check("an injector is not flagged for having no production path", injector_needs_no_production_path)
S.check("tb_network's injector list matches tb_fluids",
        lambda: tuple(n.INJECTOR_WELL_FLUIDS) == tuple(f.INJECTOR_FLUIDS) == tuple(fa.INJECTOR_WELL_FLUIDS))

sys.exit(0 if S.report() else 1)
