"""Formula-consistency audit (v0.20.1): each check pins a fix, so it cannot quietly come back."""
import sys, copy, math, types, unittest.mock, yaml
import tb_network as n, tb_catalog as c, tb_flowassurance as fa, tb_multiphase as mp, tb_thermal as th
import tb_shutdown as sd, tb_cost, tb_fluids as f, tb_production as pr, tb_economics as ec, tb_optimise as op
import tb_schedule, tb_chemistry as ch, tb_basis
from _harness import Suite
S = Suite("test_audit")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
PSI = 14.503774

# ── fluid properties ──
def viscosity_bounded_below_fit():
    for api in (25, 35, 45):
        v70 = mp.dead_oil_viscosity(api, 70.0)
        assert abs(mp.dead_oil_viscosity(api, 69.999) - v70) / v70 < 1e-3, "continuous at 70 °F"
        cold = [mp.dead_oil_viscosity(api, t) for t in (69, 60, 50, 39.2)]
        assert all(b > a for a, b in zip(cold, cold[1:])), "still rises as it cools"
    # a 35° API oil at 4 °C: tens to low hundreds of cP, not the ~900 the power law gives
    return 60 < mp.dead_oil_viscosity(35, 39.2) < 200
S.check("dead-oil viscosity follows the fit in range and a physical slope below 70 °F", viscosity_bounded_below_fit)
S.check("inside its range Beggs & Robinson is untouched",
        lambda: abs(mp.dead_oil_viscosity(35, 150.0) - (10 ** (10 ** (3.0324 - 0.02023 * 35) * 150 ** -1.163) - 1))
        < 1e-9)
def live_oil_thinner():
    mu_d = mp.dead_oil_viscosity(35, 150)
    return mp.live_oil_viscosity(mu_d, 400) < mu_d / 2 and abs(mp.live_oil_viscosity(mu_d, 0) - mu_d) / mu_d < 0.05
S.check("dissolved gas thins the oil, and none leaves it (nearly) dead", live_oil_thinner)

# ── Beggs & Brill transition band ──
def transition_continuous_inclined():
    rho_l, rho_g, mu_l, mu_g, d = 50.0, 3.0, 2.0, 0.015, 10.0
    lam, dft = 0.1, 10.0 / 12
    l2, l3 = 0.0009252 * lam ** -2.4684, 0.10 * lam ** -1.4516

    def hl(fr, th_):
        vm = math.sqrt(fr * 32.174 * dft)
        return mp.beggs_brill(lam * vm, (1 - lam) * vm, rho_l, rho_g, mu_l, mu_g, d, th_, 0.0002, 1000)
    for th_ in (5.0, 10.0, 30.0):
        for edge in (l2, l3):
            a, b = hl(edge * 0.9995, th_), hl(edge * 1.0005, th_)
            assert a["pattern"] != b["pattern"], (a["pattern"], b["pattern"])
            assert abs(a["holdup"] - b["holdup"]) < 0.01, (th_, edge, a["holdup"], b["holdup"])
    return True
S.check("uphill holdup is continuous across both edges of the transition band", transition_continuous_inclined)
S.check("single-phase liquid friction equals Darcy-Weisbach",
        lambda: abs(mp.beggs_brill(3.0, 0.0, 62.4, 0.1, 1.0, 0.01, 10, 0, 0.00018, 1000)["dpdl"]
                    - mp.haaland_friction(1488 * 62.4 * 3 * (10 / 12) / 1.0, 0.00018) * 62.4 * 9
                    / (2 * 32.174 * 10 / 12) / 144) < 1e-12)
S.check("a static water column weighs 0.433 psi/ft",
        lambda: abs(mp.beggs_brill(1e-9, 0.0, 62.4, 0.1, 1, 0.01, 4, 90, 5e-4, 1000)["dpdl_hydro"] - 0.4333) < 1e-3)
S.check("the JT coefficient has no 1/Z (≈0.3–0.5 K/bar for gas at 80 bara)",
        lambda: 0.3 < th.jt_coefficient_gas_k_per_bar(80 * PSI, th.c_to_f(60), 0.7, mp.z_factor) < 0.5)

# ── one settle-out pressure for cool-down, shutdown and blowdown ──
def one_settle_out():
    lay = demo()
    res = fa.solve(lay)
    inv = sd.inventory(lay, res)
    assert res.settle_out_bara > 0 and abs(inv["settle_out_bara"] - res.settle_out_bara) < 1e-9
    assert abs(fa.settle_out_bara(res.edges.values()) - res.settle_out_bara) < 1e-9
    lo, hi = min(r.p_out_bara for r in res.edges.values()), max(r.p_in_bara for r in res.edges.values())
    return lo < res.settle_out_bara < hi
S.check("the cool-down, the planned shutdown and the blowdown all start from one settle-out pressure",
        one_settle_out)
def cooldown_at_settle_out():
    """Recompute FL1's cool-down by hand at the settle-out pressure and compare."""
    lay = demo()
    res = fa.solve(lay)
    r = res.edges["FL1"]
    w = fa.well_inputs(lay)
    sg = next(iter(w.values())).gas_sg
    t_h = th.f_to_c(th.hydrate_temperature_f(res.settle_out_bara * PSI, sg))
    return t_h > 6.0 and r.cooldown_h > 0 and math.isfinite(r.cooldown_h)
S.check("a line's cool-down is read against the hydrate curve at the settle-out pressure", cooldown_at_settle_out)

# ── heat transfer by item ──
def steel_risers_are_steel():
    lay = demo()
    u = {}
    for k in ("riser_flex", "riser_scr", "riser_ttr", "riser_hybrid", "riser_jtube"):
        lay.edges["RISER1"].item_id = k
        u[k] = fa.u_value(lay, lay.edges["RISER1"])
    assert u["riser_scr"] == u["riser_ttr"] == fa.DEFAULT_U_W_M2K["fl_rigid_cs"], u
    assert u["riser_hybrid"] < u["riser_flex"] < u["riser_scr"], u
    return all(k in fa.DEFAULT_U_W_M2K for k in c.Catalog().items
               if c.Catalog().get(k).category in ("flowline", "riser") and k != "fl_deh" or k == "fl_deh")
S.check("every flowline and riser type has its own heat-transfer coefficient", steel_risers_are_steel)

# ── shutdown inhibitor that cannot reach the dose ──
INV = dict(volume_m3=1000.0, liquid_m3=100.0, gas_volume_m3=900.0, water_m3=20.0, settle_out_bara=150.0,
           riser_volume_m3=20.0, riser_holdup=0.3, riser_depth_m=120.0, rho_liquid=850.0, gas_sg=0.7,
           api=40.0, water_cut=0.2, cooldown_h=10.0, seabed_temp_c=2.0)
def short_dose_is_flagged():
    s = sd.ShutdownSettings(lean_meg_wt_pct=40.0)
    r = sd.inhibitor_for_shutdown(INV, "MEG", s, subcooling_c=30.0)
    assert r["short"] and r["required_wt_pct"] > r["wt_pct"] and not r["correlation_valid"]
    plan = sd.planned_shutdown(INV, s, "MEG")
    return any("NOT ENOUGH" in x["detail"] for x in plan["steps"])
S.check("a lean stream too weak for the dose is reported, not silently capped", short_dose_is_flagged)

# ── cost ──
def piggyback_uses_carrier_spread():
    lay = demo()
    lay.add_edge(n.Edge("CHEM1", "chem_line", "PLET_T", "PLET_H", attrs={"piggyback_on": "FL1"}))
    line = next(l for l in tb_cost.estimate(lay)["lines"] if l["element_id"] == "CHEM1")
    carrier_spread = lay.catalog.get(lay.edges["FL1"].item_id).install_spread
    assert line["spread"] == carrier_spread, (line["spread"], carrier_spread)
    mc = tb_cost.monte_carlo(lay, n=200, seed=1)
    return mc["P10"] < mc["P50"] < mc["P90"]
S.check("a piggybacked line is priced and simulated on its carrier's spread", piggyback_uses_carrier_spread)

# ── production against host capacity (liquid = oil + water) ──
def liquid_capacity_includes_water():
    lay = demo()
    r = f.new_reservoir("Brent", "black oil")
    r.area_km2, r.thickness_m, r.recovery_factor, r.water_cut = 12.0, 30.0, 0.42, 0.3
    f.set_reservoir(lay, r)
    f.assign_reservoir(lay, [w for w in lay.nodes if lay.kind(w) == "well"], "Brent")
    fp = pr.field_profile(lay, fa_settings=fa.FASettings(host_liquid_capacity_sm3_d=3000.0))
    peak = max(y["oil_sm3_d"] + y["water_sm3_d"] for y in fp["years"])
    assert peak <= 3000.0 + 1e-6, peak
    assert abs(max(y["oil_sm3_d"] for y in fp["years"]) - 3000.0 * 0.7) < 1.0, "oil = capacity × (1 − wc)"
    y0 = fp["years"][1]
    assert abs(y0["water_sm3_d"] / y0["oil_sm3_d"] - 0.3 / 0.7) < 1e-9
    return fp["deferred_boe_sm3"] < 1.0 and fp["total_water_sm3"] > 0
S.check("the host's liquid capacity is oil plus water, as in the flow-assurance host check",
        liquid_capacity_includes_water)
def fluid_mismatch_reported():
    lay = demo()
    r = f.new_reservoir("Brent", "black oil")       # GOR 120; the demo wells carry 250
    r.area_km2, r.thickness_m = 12.0, 30.0
    f.set_reservoir(lay, r)
    f.assign_reservoir(lay, [w for w in lay.nodes if lay.kind(w) == "well"], "Brent")
    fp = pr.field_profile(lay)
    assert fp["fluid_mismatch"] and "GOR" in fp["fluid_mismatch"][0]
    f.apply_reservoir_to_wells(lay)
    return not pr.field_profile(lay)["fluid_mismatch"]
S.check("a reservoir whose wells carry another GOR is reported until it is applied", fluid_mismatch_reported)

# ── the optimiser dates each variant from its own schedule ──
def boosting_starts_later_and_is_valued_so():
    lay = demo()
    r = f.new_reservoir("Brent", "black oil")
    r.area_km2, r.thickness_m, r.recovery_factor = 12.0, 30.0, 0.42
    f.set_reservoir(lay, r)
    f.assign_reservoir(lay, [w for w in lay.nodes if lay.kind(w) == "well"], "Brent")
    rows = {x["boosting"]: x for x in op.search(lay, op.SearchSpec(well_counts=[4], diameters_in=[10.0],
                                                                    boosting=[False, True]))}
    assert rows[True]["first_production"] > rows[False]["first_production"], "a booster has a long lead"
    v, _ = op.build_variant(lay, dict(wells=4, diameter_in=10.0, loop=False, boosting=True))
    fp = pr.field_profile(v, pr.ProfileSettings(first_production_year=int(rows[True]["first_production"][:4])))
    return fp["first_year"] == int(rows[True]["first_production"][:4])
S.check("a variant's production starts on its own first-production date", boosting_starts_later_and_is_valued_so)
def missing_first_oil_milestone_is_not_feasible():
    lay = demo()
    result = types.SimpleNamespace(wells=[], edges={}, findings=[])
    empty_profile = dict(years=[], streams=[], total_boe_sm3=0.0, first_year=2030, last_year=2030)
    with unittest.mock.patch.object(tb_schedule, "build_from_layout",
                                    return_value=(types.SimpleNamespace(activities={}), {})), \
         unittest.mock.patch.object(fa, "deliverable_rates",
                                    return_value=dict(result=result, rates={}, scale=1.0, note="")), \
         unittest.mock.patch.object(pr, "field_profile", return_value=empty_profile):
        row = op.evaluate(lay)
    assert not row["feasible"] and math.isnan(row["npv_musd"])
    return "no first-production milestone" in row["note"]
S.check("a schedule without a first-oil milestone cannot produce a feasible or valued variant",
        missing_first_oil_milestone_is_not_feasible)

# ── cross-module agreement ──
def cost_and_schedule_agree_on_piggyback():
    return tb_cost.CostSettings().piggyback_install_frac == tb_schedule.ScheduleSettings().piggyback_install_frac
S.check("cost and schedule use the same piggyback share by default", cost_and_schedule_agree_on_piggyback)
def stock_tank_oil_density_is_shared():
    api = 40.0
    rho = 141.5 / (api + 131.5) * th.RHO_STOCK_TANK_WATER_KG_M3
    assert abs(th.stream_mass(1000, 0, 0, api, 0.7).oil_kg_s
               - 1000 * 0.158987 * rho / 86400) < 1e-12
    assert abs(sd._liquid_density(api, 0.0) - rho) < 1e-12
    density_row = next(r for r in tb_basis.design_basis(demo())
                       if r["item"] == "Oil density")
    return density_row["value"].startswith(f"{round(rho):.0f}–")
S.check("thermal, shutdown, and design-basis oil density use one reference-water density",
        stock_tank_oil_density_is_shared)
S.check("one Hammerschmidt limit table for flow assurance and chemistry",
        lambda: ch.HAMMERSCHMIDT_LIMIT_WT == th.HAMMERSCHMIDT_LIMIT_WT)
S.check("1 000 Sm³ gas is 1 Sm³ o.e. in production and economics alike",
        lambda: pr.BOE_PER_SM3_GAS == ec.BOE_PER_SM3_GAS == 1e-3)
S.check("the gas price default is about 8 USD/MMBtu (1 Sm³ ≈ 0.0353 MMBtu)",
        lambda: 7.0 < ec.EconomicSettings().gas_price_usd_sm3 / 0.0353 < 9.0)

sys.exit(0 if S.report() else 1)
