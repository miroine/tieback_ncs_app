"""A gas-condensate field is profiled, valued and reported in gas; an oil field in oil."""
import sys, copy, yaml
import tb_network as n, tb_catalog as c, tb_fluids as f, tb_production as pr, tb_economics as ec
import tb_flowassurance as fa, tb_basis as tb, tb_viability as tv, tb_optimise as op, tb_cost, tb_schedule
from _harness import Suite
S = Suite("test_main_phase")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))


def gas_field(giip=20e9, rate_msm3_d=1.5):
    lay = n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
    r = f.new_reservoir("G", "gas condensate")
    r.in_place_sm3 = giip
    f.set_reservoir(lay, r)
    ws = [w for w in lay.nodes if lay.kind(w) == "well"]
    f.assign_reservoir(lay, ws, "G")
    f.apply_reservoir_to_wells(lay)
    for w in ws:
        v = fa.well_inputs(lay)[w]
        v.oil_sm3_d = f.liquid_from_main("gas", rate_msm3_d, v.gor_sm3_sm3)
        fa.set_well_inputs(lay, w, v)
    return lay, ws


def oil_field():
    lay = n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
    r = f.new_reservoir("O", "black oil")
    r.in_place_sm3 = 40e6
    f.set_reservoir(lay, r)
    f.assign_reservoir(lay, [w for w in lay.nodes if lay.kind(w) == "well"], "O")
    return lay


PS = pr.ProfileSettings(max_years=60)


def main_phase_detected():
    g, _ = gas_field()
    return f.layout_main_phase(g) == "gas" and f.layout_main_phase(oil_field()) == "oil" \
        and f.reservoir_main_phase(f.new_reservoir("x", "wet gas")) == "gas"
S.check("the main phase is gas for a gas-condensate field and oil for an oil field", main_phase_detected)


def rate_conversions():
    v = fa.WellFA(oil_sm3_d=937.5, gor_sm3_sm3=1600.0)
    assert abs(f.main_rate(v, "gas") - 1.5) < 1e-12 and f.main_rate(v, "oil") == 937.5
    assert abs(f.liquid_from_main("gas", 1.5, 1600.0) - 937.5) < 1e-9
    try:
        f.liquid_from_main("gas", 1.0, 0.0)
        return False
    except ValueError:
        return True
S.check("gas MSm³/d ↔ condensate Sm³/d through the GOR, and a zero GOR is refused", rate_conversions)


def gas_profiled_in_gas():
    lay, ws = gas_field(giip=20e9, rate_msm3_d=1.5)
    fp = pr.field_profile(lay, PS)
    st = fp["streams"][0]
    assert st["phase"] == "gas" and fp["main_phase"] == "gas"
    eur = st["eur_main_sm3"]
    assert abs(eur - 20e9 * st["recovery_factor"]) < 1
    q_res = st["offtake"] * eur / (pr.DAYS_PER_YEAR * PS.uptime)
    q_wells = 4 * 1.5e6
    assert abs(st["profile"]["plateau_sm3_d"] - min(q_res, q_wells)) < 1.0, (st["profile"]["plateau_sm3_d"], q_res)
    assert abs(fp["total_gas_sm3"] / eur - 1.0) < 0.01, "the gas produced is the gas EUR"
    gor = st["gor_sm3_sm3"]
    assert abs(fp["total_oil_sm3"] - fp["total_gas_sm3"] / gor) / fp["total_oil_sm3"] < 1e-9, "condensate at the CGR"
    return True
S.check("a gas reservoir is profiled in gas: plateau from GIIP × RF and the wells' gas rate", gas_profiled_in_gas)


def in_place_and_rates_move_the_value():
    def npv(lay):
        fp = pr.field_profile(lay, PS)
        return ec.cashflow(fp["years"], {2028: 5e8}, ec.EconomicSettings())["npv_usd"], fp
    small, _ = gas_field(giip=10e9)
    big, _ = gas_field(giip=30e9)
    n_s, _ = npv(small)
    n_b, _ = npv(big)
    assert n_b > n_s * 1.5, (n_s, n_b)
    # the wells bind on a big GIIP: faster wells produce earlier and are worth more
    slow, _ = gas_field(giip=200e9, rate_msm3_d=0.5)
    fast, _ = gas_field(giip=200e9, rate_msm3_d=1.5)
    n_sl, fp_sl = npv(slow)
    n_fa, fp_fa = npv(fast)
    assert fp_sl["streams"][0]["profile"]["limited_by"] == "wells"
    assert abs(fp_fa["streams"][0]["profile"]["plateau_sm3_d"] / fp_sl["streams"][0]["profile"]["plateau_sm3_d"] - 3) < 1e-6
    return n_fa > n_sl
S.check("GIIP and the wells' gas rates both change the profile and the NPV", in_place_and_rates_move_the_value)


def gas_cutoff_in_gas():
    lay, _ = gas_field(giip=5e9)
    fp = pr.field_profile(lay, pr.ProfileSettings(max_years=80, economic_cutoff_gas_msm3_d=0.5))
    return all(r["gas_msm3_d"] >= 0.5 - 1e-9 for r in fp["years"][1:])
S.check("a gas reservoir stops at the gas cut-off (MSm³/d), not a condensate one", gas_cutoff_in_gas)


def manual_gas_rows():
    lay, ws = gas_field()
    pr.set_manual_profile(lay, ws[0], [dict(year=1, gas_ksm3_d=1500.0)])
    fp = pr.field_profile(lay, PS)
    man = [s for s in fp["streams"] if s["kind"] == "manual"][0]
    y = man["profile"]["years"][0]
    assert man["phase"] == "gas"
    return abs(y["volume_sm3"] - 1.5e6 / man["gor_sm3_sm3"] * pr.DAYS_PER_YEAR) < 1e-3 \
        and abs(y["gas_sm3"] - 1.5e6 * pr.DAYS_PER_YEAR) < 1e-3
S.check("a manual gas-well row entered by gas alone gets its condensate from the CGR", manual_gas_rows)


def basis_in_gas():
    lay, _ = gas_field()
    rows = {r["item"]: r for r in tb.design_basis(lay)}
    assert "Design gas rate" in rows and "Design oil rate" not in rows
    assert "6.00 MSm³/d" in rows["Design gas rate"]["value"] and "condensate" in rows["Design gas rate"]["value"]
    assert "Condensate-gas ratio (CGR)" in rows and "Condensate density" in rows
    oil = {r["item"]: r for r in tb.design_basis(oil_field())}
    return "Design oil rate" in oil and "associated gas" in oil["Design oil rate"]["value"]
S.check("the design basis states a gas field's design rate in gas, with CGR and condensate", basis_in_gas)


def viability_in_gas():
    lay, _ = gas_field()
    rows = {r["criterion"]: r for r in tv.viability(lay, tb_cost.CostSettings(), tb_schedule.ScheduleSettings(),
                                                    fa.FASettings(), 0.5)}
    assert "MSm³/d gas" in rows["Wells deliver at design rate"]["value"], rows["Wells deliver at design rate"]
    assert "3.00 MSm³/d gas" in rows["Hydrate margin at turndown"]["value"], rows["Hydrate margin at turndown"]
    return rows["Host capacity"]["value"].split(",")[0].endswith("MSm³/d gas") \
        and rows["Host capacity"]["threshold"].split(",")[0].endswith("MSm³/d gas")
S.check("viability quotes a gas field's rates and host capacity in gas first", viability_in_gas)


def optimiser_rate_in_gas():
    lay, ws = gas_field()
    lay2 = copy.deepcopy(lay)
    row = op.evaluate(lay2, per_well_rate_sm3_d=2.0, rate_phase="gas")
    v = fa.well_inputs(lay2)[ws[0]]
    assert abs(f.main_rate(v, "gas") - 2.0) < 1e-9, f.main_rate(v, "gas")
    return row["main_phase"] == "gas" and 0 < row["plateau_sm3_d"] < 100, row["plateau_sm3_d"]
S.check("the optimiser's rate per well and plateau are in gas for a gas field", optimiser_rate_in_gas)


def wells_needed_units():
    r = pr.wells_needed(6.0, 1.5, unit="MSm³/d gas")
    return r["wells"] == 4 and "6.00 MSm³/d gas" in r["note"]
S.check("the well count speaks the main phase's unit", wells_needed_units)

sys.exit(0 if S.report() else 1)
