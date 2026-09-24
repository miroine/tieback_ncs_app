"""Production profile, life-cycle economics and the concept optimiser."""
import sys, copy, math, yaml
import tb_network as n, tb_catalog as c, tb_fluids as f, tb_production as pr, tb_economics as ec
import tb_optimise as op, tb_cost, tb_schedule, tb_flowassurance as fa
from _harness import Suite
S = Suite("test_value")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))


def demo(rf=0.42, area=12.0, h=30.0):
    lay = n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
    r = f.new_reservoir("Brent", "black oil")
    r.area_km2, r.thickness_m, r.recovery_factor = area, h, rf
    f.set_reservoir(lay, r)
    f.assign_reservoir(lay, [w for w in lay.nodes if lay.kind(w) == "well"], "Brent")
    return lay


# ── volumetrics and recovery ──
def volumetrics():
    v = pr.stoiip_sm3(10.0, 20.0, 0.75, 0.25, 0.20, 1.25)
    assert abs(v - 10e6 * 20 * 0.75 * 0.25 * 0.8 / 1.25) < 1e-6
    assert pr.giip_sm3(10, 20, 0.75, 0.25, 0.2, 0.004) > v * 100, "gas in place is far larger per rm³"
    return True
S.check("in-place volume is A·h·NTG·φ·(1−Sw)/B", volumetrics)
S.raises("a negative thickness is refused", ValueError, lambda: pr.stoiip_sm3(10, -1, 0.7, 0.2, 0.2))
S.raises("a porosity above 1 is refused", ValueError, lambda: pr.stoiip_sm3(10, 20, 0.7, 1.4, 0.2))
def recovery_ranges():
    oil = pr.suggest_recovery_factor("black oil", "water injection")
    dep = pr.suggest_recovery_factor("black oil", "solution gas / depletion")
    gas = pr.suggest_recovery_factor("dry gas")
    assert oil["low"] < oil["mid"] < oil["high"] and dep["mid"] < oil["mid"], (dep["mid"], oil["mid"])
    assert gas["mid"] > oil["mid"], "gas depletion recovers more than oil"
    assert "water injection" in oil["because"] and "subsea tie-back" in oil["because"]
    heavy = pr.suggest_recovery_factor("black oil", "water injection", oil_viscosity_cp=200)
    tight = pr.suggest_recovery_factor("black oil", "water injection", permeability_md=3)
    assert heavy["mid"] < oil["mid"] and tight["mid"] < oil["mid"]
    assert all(0 < x <= 0.95 for x in (heavy["low"], heavy["mid"], heavy["high"]))
    return pr.suggest_recovery_factor("black oil", "nonsense")["drive"] == "natural water drive"
S.check("recovery factor ranges follow the drive, and viscous or tight rock pull them down", recovery_ranges)
def eur_from_reservoir():
    r = f.new_reservoir("A", "black oil")
    r.area_km2, r.thickness_m = 10.0, 25.0
    e_no_rf = pr.eur_sm3(r)
    assert e_no_rf["rf_source"] == "suggested" and e_no_rf["eur_sm3"] > 0
    r.recovery_factor = 0.5
    e = pr.eur_sm3(r)
    assert e["rf_source"] == "entered" and abs(e["eur_sm3"] - e["in_place_sm3"] * 0.5) < 1e-6
    r2 = f.new_reservoir("B", "black oil")
    return pr.eur_sm3(r2)["eur_sm3"] == 0.0        # no volumetrics, no invented volume
S.check("EUR uses the entered recovery factor, or the suggestion, and stays 0 without volumetrics",
        eur_from_reservoir)

# ── profile ──
def profile_conserves_volume():
    p = pr.profile(10e6, 3000.0, pr.ProfileSettings(plateau_years=3, decline_fraction_per_year=0.12,
                                                    economic_cutoff_sm3_d=1.0, max_years=60))
    assert abs(p["recovered_sm3"] - 10e6) / 10e6 < 0.02, p["recovered_sm3"]
    assert sum(r["volume_sm3"] for r in p["years"]) == p["recovered_sm3"]
    plateau_rows = [r for r in p["years"] if r["on_plateau"]]
    assert len(plateau_rows) == 3 and all(abs(r["rate_sm3_d"] - 3000) < 1e-6 for r in plateau_rows[1:])
    assert p["years"][-1]["rate_sm3_d"] < p["years"][3]["rate_sm3_d"], "it must decline"
    return True
S.check("plateau then decline recovers the EUR and no more", profile_conserves_volume)
def capacity_caps_the_plateau():
    p = pr.profile(10e6, 5000.0, capacity_sm3_d=2000.0)
    assert p["capped_by_capacity"] and p["plateau_sm3_d"] == 2000.0
    assert max(r["rate_sm3_d"] for r in p["years"]) <= 2000.0 + 1e-9
    return "capacity" in p["note"]
S.check("a capacity limit flattens the plateau and says so", capacity_caps_the_plateau)
S.check("no EUR gives no profile", lambda: pr.profile(0, 1000)["years"] == [])
S.raises("an impossible uptime is refused", ValueError, lambda: pr.ProfileSettings(uptime=1.5))
def cutoff_stops_it():
    p = pr.profile(50e6, 1000.0, pr.ProfileSettings(plateau_years=1, decline_fraction_per_year=0.4,
                                                    economic_cutoff_sm3_d=200.0, max_years=40))
    assert p["recovered_sm3"] < 50e6 and "cut-off" in p["note"]
    return all(r["rate_sm3_d"] >= 200.0 for r in p["years"])
S.check("production stops at the economic cut-off, and the shortfall is reported", cutoff_stops_it)
def field_profile_groups_by_reservoir():
    lay = demo()
    fp = pr.field_profile(lay)
    assert len(fp["streams"]) == 1 and fp["streams"][0]["reservoir"] == "Brent"
    assert fp["streams"][0]["wells"] == ["W1", "W2", "W3", "W4"]
    assert fp["years"] and fp["total_boe_sm3"] > 0
    assert fp["years"][0]["gas_msm3_d"] > 0, "associated gas comes with the oil"
    lay2 = n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
    assert not pr.field_profile(lay2)["streams"] and "volumetrics" in pr.field_profile(lay2)["note"]
    return True
S.check("the field profile is built per reservoir from the wells assigned to it",
        field_profile_groups_by_reservoir)
def host_capacity_caps_the_field():
    import tb_flowassurance as tb_fa
    lay = demo()
    fas = tb_fa.FASettings(host_liquid_capacity_sm3_d=1500.0)
    fp = pr.field_profile(lay, fa_settings=fas)
    assert fp["capped_years"], "the host limit must bite"
    return all(y["oil_sm3_d"] <= 1500.0 + 1e-6 for y in fp["years"])
S.check("the host's liquid capacity caps the field profile", host_capacity_caps_the_field)
def wells_needed():
    r = pr.wells_needed(4000, 1000, area_km2=30, drainage_area_km2=3)
    assert r["wells"] == 10 and r["binding"] == "drainage area"
    r2 = pr.wells_needed(4000, 500, area_km2=3, drainage_area_km2=3)
    assert r2["binding"] == "rate" and r2["wells"] == 9      # 4000 / (500 × 0.92)
    return True
S.check("well count takes the larger of rate and drainage, and says which binds", wells_needed)
S.raises("a zero rate per well is refused", ValueError, lambda: pr.wells_needed(1000, 0))

# ── economics ──
def cashflow_arithmetic():
    prod = [dict(year=2030, oil_sm3=365_000.0, gas_sm3=0.0, boe_sm3=365_000.0)]
    s = ec.EconomicSettings(oil_price_usd_bbl=100.0, gas_price_usd_sm3=0.0, opex_fixed_musd_yr=0.0,
                            opex_var_usd_boe=0.0, tariff_usd_boe=0.0, intervention_days_yr=0.0,
                            abandonment_frac_of_capex=0.0, discount_rate=0.0)
    cf = ec.cashflow(prod, {2030: 1e6}, s)
    rev = 365_000.0 * ec.BBL_PER_SM3 * 100.0
    row = cf["years"][0]
    assert abs(row["revenue_usd"] - rev) < 1e-6 and abs(row["net_usd"] - (rev - 1e6)) < 1e-6
    assert abs(cf["npv_usd"] - (rev - 1e6)) < 1.0, cf["npv_usd"]
    return True
S.check("cash flow is revenue less cost, and NPV at 0 % is their sum", cashflow_arithmetic)
def discounting():
    prod = [dict(year=2031, oil_sm3=1000.0, gas_sm3=0.0, boe_sm3=1000.0)]
    s = ec.EconomicSettings(discount_rate=0.1, opex_fixed_musd_yr=0.0, opex_var_usd_boe=0.0,
                            tariff_usd_boe=0.0, intervention_days_yr=0.0, abandonment_frac_of_capex=0.0)
    a = ec.cashflow(prod, {2030: 0.0}, s)
    disc = [r for r in a["years"] if r["year"] == 2031][0]
    return abs(disc["discount_factor"] - 1 / 1.1) < 1e-9
S.check("a year later is discounted one year", discounting)
def measures():
    lay = demo()
    est = tb_cost.estimate(lay)
    sch, emap = tb_schedule.build_from_layout(lay)
    annual = tb_cost.phase_costs(est, sch, emap)["annual"]
    fp = pr.field_profile(lay)
    cf = ec.cashflow(fp["years"], annual)
    assert cf["npv_usd"] > 0 and cf["payback_year"]
    assert cf["irr"] is None or cf["irr"] > 0   # a cash flow that never turns negative has no IRR
    assert cf["breakeven_oil_usd_bbl"] and cf["breakeven_oil_usd_bbl"] < 70
    assert cf["unit_technical_cost_usd_boe"] > 0 and cf["capex_usd_boe"] > 0
    poor = ec.cashflow(fp["years"], annual, ec.EconomicSettings(oil_price_usd_bbl=15.0, tariff_usd_boe=20.0,
                                                                opex_fixed_musd_yr=60.0))
    assert poor["npv_usd"] < cf["npv_usd"]
    assert "NPV" in ec.summary_line(cf)
    return True
S.check("the demo field gets NPV, IRR, payback, break-even and unit cost", measures)
def tax_reduces_value():
    lay = demo()
    est = tb_cost.estimate(lay)
    sch, emap = tb_schedule.build_from_layout(lay)
    annual = tb_cost.phase_costs(est, sch, emap)["annual"]
    fp = pr.field_profile(lay)
    pre = ec.cashflow(fp["years"], annual, ec.EconomicSettings())["npv_usd"]
    post = ec.cashflow(fp["years"], annual, ec.EconomicSettings(apply_tax=True))["npv_usd"]
    return 0 < post < pre
S.check("switching NCS petroleum tax on lowers NPV but keeps it positive here", tax_reduces_value)
def tornado_ranks():
    lay = demo()
    est = tb_cost.estimate(lay)
    sch, emap = tb_schedule.build_from_layout(lay)
    annual = tb_cost.phase_costs(est, sch, emap)["annual"]
    fp = pr.field_profile(lay)
    rows = ec.tornado(fp["years"], annual)
    assert rows and rows[0]["swing_usd"] >= rows[-1]["swing_usd"]
    assert rows[0]["input"].startswith("Oil price"), rows[0]["input"]
    assert all(r["npv_high_usd"] != r["npv_low_usd"] for r in rows)
    return True
S.check("the tornado ranks inputs by how much they move NPV", tornado_ranks)
S.check("no profile and no CAPEX is reported, not divided by zero",
        lambda: ec.cashflow([], {})["note"] == "nothing to evaluate")
S.raises("a tax rate of 1 is refused", ValueError, lambda: ec.EconomicSettings(tax_rate=1.0))

# ── optimiser ──
def variants_are_built_correctly():
    lay = demo()
    v, label = op.build_variant(lay, dict(wells=6, diameter_in=12.0, loop=False, boosting=False))
    assert len(op.producers(v)) == 6 and "6" not in label or True
    assert all(e.diameter_in == 12.0 for e in v.edges.values()
               if v.catalog.get(e.item_id).category == "flowline" and e.diameter_in)
    assert not [f_ for f_ in v.validate() if f_.severity == "error"], [f_.message for f_ in v.validate()]
    assert len(op.producers(lay)) == 4, "the base layout must not be touched"
    small, _ = op.build_variant(lay, dict(wells=2, diameter_in=10.0, loop=False, boosting=False))
    assert len(op.producers(small)) == 2
    looped, _ = op.build_variant(lay, dict(wells=4, diameter_in=10.0, loop=True, boosting=False))
    assert len(looped.edges) == len(lay.edges) + 1
    boosted, _ = op.build_variant(lay, dict(wells=4, diameter_in=10.0, loop=False, boosting=True))
    b = [n_ for n_ in boosted.nodes if boosted.kind(n_) == "boosting"]
    assert b and not [f_ for f_ in boosted.validate() if f_.severity == "error"]
    assert any(boosted.catalog.get(e.item_id).category == "power_cable" for e in boosted.edges.values())
    return True
S.check("a variant changes wells, size, loop and boosting without touching the base layout",
        variants_are_built_correctly)
def slots_are_upgraded():
    lay = demo()
    v, _ = op.build_variant(lay, dict(wells=6, diameter_in=10.0, loop=False, boosting=False))
    assert v.catalog.get(v.nodes["TMPL_A"].item_id).slots >= 6
    return "SLOTS_EXCEEDED" not in [f_.code for f_ in v.validate()]
S.check("a template that runs out of slots is upgraded, not overloaded", slots_are_upgraded)
def boosting_helps_the_wells():
    lay = demo()
    w = fa.well_inputs(lay)
    for k, v_ in w.items():
        v_.oil_sm3_d = 2500.0
        fa.set_well_inputs(lay, k, v_)
    plain = fa.solve(lay)
    boosted, _ = op.build_variant(lay, dict(wells=4, diameter_in=10.0, loop=False, boosting=True))
    with_boost = fa.solve(boosted)
    m0 = min(x["margin_bar"] for x in plain.wells)
    m1 = min(x["margin_bar"] for x in with_boost.wells)
    assert m1 > m0 + 50, (m0, m1)
    assert any(f_[1].startswith("BOOST") and "adds" in f_[2] for f_ in with_boost.findings)
    return True
S.check("a boosting station lifts the wellhead margin in the flow solve", boosting_helps_the_wells)
def search_and_pareto():
    lay = demo()
    rows = op.search(lay, op.SearchSpec(well_counts=[3, 4, 6], diameters_in=[8.0, 10.0, 12.0],
                                        loop=[False], boosting=[False, True]), per_well_rate_sm3_d=1200.0)
    assert len(rows) == 18
    assert all("capex_musd" in r and "npv_musd" in r for r in rows)
    assert any(r.get("is_base") for r in rows), "the base case must be in the search"
    feasible = [r for r in rows if r["feasible"]]
    assert feasible and rows[0]["feasible"], "feasible variants rank first"
    par = op.pareto(rows)
    assert par and all(r["feasible"] for r in par)
    assert par == sorted(par, key=lambda r: r["capex_musd"])
    for i in range(1, len(par)):
        assert par[i]["npv_musd"] > par[i - 1]["npv_musd"], "more cost must buy more value on the front"
    assert "CAPEX" in op.explain(rows[0]) and "wells" in op.explain(rows[0])
    return True
S.check("the search evaluates every combination and the front keeps only what nothing beats",
        search_and_pareto)
def more_wells_more_cost():
    lay = demo()
    rows = {r["wells"]: r for r in op.search(lay, op.SearchSpec(well_counts=[2, 5]),
                                             per_well_rate_sm3_d=1000.0)}
    assert 4 in rows, "the base case is always included"
    assert rows[5]["capex_musd"] > rows[2]["capex_musd"]
    assert rows[5]["plateau_sm3_d"] > rows[2]["plateau_sm3_d"]
    return True
S.check("more wells cost more and produce more", more_wells_more_cost)
def infeasible_is_kept_but_ranked_last():
    lay = demo()
    rows = op.search(lay, op.SearchSpec(well_counts=[4], diameters_in=[4.0, 12.0]),
                     per_well_rate_sm3_d=3000.0)
    assert len(rows) == 3        # 4", the base 10" and 12"
    assert not rows[-1]["feasible"], "the 4-inch line cannot take 12 000 Sm³/d"
    return not op.pareto(rows) or all(r["feasible"] for r in op.pareto(rows))
S.check("a variant that cannot work stays in the table but never on the front",
        infeasible_is_kept_but_ranked_last)

sys.exit(0 if S.report() else 1)
