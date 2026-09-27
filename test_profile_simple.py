"""The simplified profile: in place × RF by drainage strategy, and manual profiles per well."""
import copy, yaml
import tb_network as n, tb_catalog as c, tb_fluids as f, tb_production as pr, tb_flowassurance as fa
from _harness import Suite
S = Suite("test_profile_simple")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))


def demo(stoiip=30e6, rf=0.40, drive="water injection"):
    lay = n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
    r = f.new_reservoir("Res A", "black oil")
    r.in_place_sm3, r.recovery_factor, r.drive = stoiip, rf, drive
    f.set_reservoir(lay, r)
    f.assign_reservoir(lay, [w for w in lay.nodes if lay.kind(w) == "well"], "Res A")
    return lay


def wells(lay):
    return [w for w in lay.nodes if lay.kind(w) == "well"]


def entered_in_place_wins():
    r = f.new_reservoir("A", "black oil")
    r.area_km2, r.thickness_m = 10.0, 25.0
    vol = pr.eur_sm3(r)["in_place_sm3"]
    r.in_place_sm3 = 5e6
    e = pr.eur_sm3(r)
    assert e["in_place_sm3"] == 5e6 and e["in_place_source"] == "entered" and vol != 5e6
    r.in_place_sm3 = 0.0
    return pr.eur_sm3(r)["in_place_source"] == "volumetrics"
S.check("an entered STOIIP/GIIP overrides the volumetrics; 0 falls back to them", entered_in_place_wins)


def plateau_is_the_offtake_and_the_eur_closes():
    s = pr.ProfileSettings(uptime=0.9, ramp_up_years=0.0, economic_cutoff_sm3_d=1.0, max_years=80)
    p = pr.strategy_profile(10e6, 0.10, 0.45, s)
    q = 0.10 * 10e6 / (pr.DAYS_PER_YEAR * 0.9)
    assert abs(p["plateau_sm3_d"] - q) < 1e-6 and p["limited_by"] == "reservoir offtake"
    assert abs(p["years"][0]["rate_sm3_d"] - q) / q < 1e-9
    assert abs(p["recovered_sm3"] - 10e6) / 10e6 < 0.01, p["recovered_sm3"]
    on = [r for r in p["years"] if r["on_plateau"]]
    assert on and on[-1]["cumulative_sm3"] <= 0.45 * 10e6 + 1 and len(on) == 4, len(on)   # 45 % at 10 %/yr
    assert all(b["rate_sm3_d"] <= a["rate_sm3_d"] + 1e-6 for a, b in zip(p["years"], p["years"][1:]))
    return True
S.check("plateau = offtake × EUR, held to the plateau share, and the decline closes on the EUR",
        plateau_is_the_offtake_and_the_eur_closes)


def fewer_wells_longer_plateau_same_volume():
    s = pr.ProfileSettings(economic_cutoff_sm3_d=1.0, max_years=80)
    free = pr.strategy_profile(10e6, 0.12, 0.40, s)
    lim = pr.strategy_profile(10e6, 0.12, 0.40, s, well_potential_sm3_d=free["plateau_sm3_d"] / 2)
    assert lim["limited_by"] == "wells" and lim["plateau_years"] > free["plateau_years"]
    assert abs(lim["recovered_sm3"] - free["recovered_sm3"]) / free["recovered_sm3"] < 0.02
    off = pr.strategy_profile(10e6, 0.12, 0.40, pr.ProfileSettings(limit_by_wells=False, economic_cutoff_sm3_d=1.0,
                                                                  max_years=80),
                              well_potential_sm3_d=free["plateau_sm3_d"] / 2)
    return off["limited_by"] == "reservoir offtake"
S.check("the wells cap the plateau and stretch it, without losing volume (and the cap can be switched off)",
        fewer_wells_longer_plateau_same_volume)


def strategy_changes_shape():
    dep = pr.drive_profile(f.Reservoir(name="x", drive="solution gas / depletion"))
    inj = pr.drive_profile(f.Reservoir(name="x", drive="water injection"))
    assert dep["end_frac"] < inj["end_frac"] and dep["offtake_source"] == "strategy"
    own = pr.drive_profile(f.Reservoir(name="x", drive="water injection", plateau_offtake=0.2, plateau_end_frac=0.3))
    return own["offtake"] == 0.2 and own["end_frac"] == 0.3 and own["end_source"] == "entered"
S.check("the drainage strategy sets offtake and plateau length, and entered values override them",
        strategy_changes_shape)


def field_profile_from_in_place():
    lay = demo()
    fp = pr.field_profile(lay, pr.ProfileSettings(limit_by_wells=False, economic_cutoff_sm3_d=1.0, max_years=80))
    st = fp["streams"][0]
    assert st["kind"] == "calculated" and abs(st["eur_calc_sm3"] - 12e6) < 1
    assert abs(fp["total_oil_sm3"] - 12e6) / 12e6 < 0.01, fp["total_oil_sm3"]
    lay2 = demo(rf=0.20)
    fp2 = pr.field_profile(lay2, pr.ProfileSettings(limit_by_wells=False, economic_cutoff_sm3_d=1.0, max_years=80))
    return abs(fp2["total_oil_sm3"] / fp["total_oil_sm3"] - 0.5) < 0.01
S.check("the field profile is in place × recovery factor, and halving the RF halves it", field_profile_from_in_place)


def manual_well_replaces_its_share():
    lay = demo()
    ws = wells(lay)
    base = pr.field_profile(lay)
    pr.set_manual_profile(lay, ws[0], [dict(year=1, oil_sm3_d=800), dict(year=2, oil_sm3_d=600, gas_ksm3_d=50),
                                       dict(year=3, oil_sm3_d=400, water_sm3_d=300)])
    fp = pr.field_profile(lay)
    calc = [s for s in fp["streams"] if s["kind"] == "calculated"][0]
    man = [s for s in fp["streams"] if s["kind"] == "manual"][0]
    assert fp["manual_wells"] == [ws[0]] and ws[0] not in calc["wells"] and ws[0] in calc["manual_wells"]
    assert abs(calc["eur_calc_sm3"] - calc["eur_liquid_sm3"] * (len(ws) - 1) / len(ws)) < 1
    y1 = man["profile"]["years"][0]
    assert y1["year"] == pr.ProfileSettings().first_production_year, "year 1 is first production"
    assert abs(y1["volume_sm3"] - 800 * pr.DAYS_PER_YEAR) < 1e-6
    assert abs(y1["gas_sm3"] - 800 * man["gor_sm3_sm3"] * pr.DAYS_PER_YEAR) < 1e-3, "blank gas follows the GOR"
    assert abs(man["profile"]["years"][1]["gas_sm3"] - 50e3 * pr.DAYS_PER_YEAR) < 1e-3
    assert abs(man["profile"]["years"][2]["water_sm3"] - 300 * pr.DAYS_PER_YEAR) < 1e-3
    assert fp["total_oil_sm3"] != base["total_oil_sm3"]
    # switched off: back to the calculated profile
    pr.set_manual_profile(lay, ws[0], lay.nodes[ws[0]].attrs["manual_profile"], use=False)
    return abs(pr.field_profile(lay)["total_oil_sm3"] - base["total_oil_sm3"]) < 1.0
S.check("a manual well profile replaces that well's share, and switching it off restores it",
        manual_well_replaces_its_share)


def manual_only_no_reservoir():
    lay = n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
    w = wells(lay)[0]
    pr.set_manual_profile(lay, w, [dict(year=2031, oil_sm3_d=1000), dict(year=2032, oil_sm3_d=700)])
    fp = pr.field_profile(lay)
    assert fp["years"] and fp["first_year"] == 2031 and not fp["note"]
    return abs(fp["total_oil_sm3"] - 1700 * pr.DAYS_PER_YEAR) < 1e-3
S.check("manual profiles work without any reservoir, on calendar years", manual_only_no_reservoir)


def manual_over_eur_warned():
    lay = demo(stoiip=2e6, rf=0.3)             # 0.6 MSm³
    for w in wells(lay):
        pr.set_manual_profile(lay, w, [dict(year=y, oil_sm3_d=1000) for y in range(1, 4)])
    fp = pr.field_profile(lay)
    return fp["over_eur"] and "more than their share" in fp["over_eur"][0]
S.check("manual profiles that take more than the reservoir's EUR are flagged", manual_over_eur_warned)


def csv_and_validation():
    rows = pr.read_profile_csv("Year,Oil (Sm3/d),Gas (kSm3/d),Water\n1,1000,,50\n2,800,120,\n")
    assert rows[0] == dict(year=1, oil_sm3_d=1000.0, gas_ksm3_d=None, water_sm3_d=50.0), rows[0]
    assert rows[1]["gas_ksm3_d"] == 120.0 and rows[1]["water_sm3_d"] is None
    tsv = pr.read_profile_csv("year\toil\n2030\t500\n2031\t400")
    assert [r["year"] for r in tsv] == [2030, 2031]
    semi = pr.read_profile_csv("year;oil\n1;1,5\n")
    assert semi[0]["oil_sm3_d"] == 1.5
    for bad in ([dict(year=1, oil_sm3_d=1), dict(year=1, oil_sm3_d=2)], [dict(year=1, oil_sm3_d=-5)]):
        try:
            pr.clean_manual_profile(bad)
            return False
        except ValueError:
            pass
    return pr.clean_manual_profile([dict(year=None, oil_sm3_d=1), dict(year=3)]) == []
S.check("CSV/TSV/semicolon profiles are read; duplicate years and negative rates are refused", csv_and_validation)


def optimiser_still_sees_wells():
    lay = demo(stoiip=200e6)                      # a big reservoir: the wells, not the reservoir, bind
    w_in = fa.well_inputs(lay)
    all_ = pr.field_profile(lay)
    half = pr.field_profile(lay, well_rates={w: v.oil_sm3_d * 0.5 for w, v in w_in.items()})
    return half["streams"][0]["plateau_sm3_d"] < all_["streams"][0]["plateau_sm3_d"] * 0.6
S.check("the well rates still set the plateau when they bind, so the optimiser's well count matters",
        optimiser_still_sees_wells)


def reservoir_round_trip():
    lay = demo()
    r = f.reservoirs(lay)["Res A"]
    r.plateau_offtake, r.plateau_end_frac = 0.11, 0.33
    f.set_reservoir(lay, r)
    d = yaml.safe_load(yaml.safe_dump(lay.to_dict()))
    lay2 = n.Layout.from_dict(d, c.Catalog())
    r2 = f.reservoirs(lay2)["Res A"]
    w = wells(lay)[0]
    pr.set_manual_profile(lay, w, [dict(year=1, oil_sm3_d=500)])
    lay3 = n.Layout.from_dict(yaml.safe_load(yaml.safe_dump(lay.to_dict())), c.Catalog())
    return (r2.in_place_sm3 == 30e6 and r2.plateau_offtake == 0.11 and pr.manual_profile(lay3, w)
            and pr.manual_profile(lay3, w)[0]["oil_sm3_d"] == 500)
S.check("in place, strategy settings and manual profiles survive the project file", reservoir_round_trip)


def reshuffle_keeps_every_barrel():
    rows = [dict(year=2030 + i, oil_sm3=v * pr.DAYS_PER_YEAR * 0.9, gas_sm3=v * 100 * pr.DAYS_PER_YEAR * 0.9,
                 water_sm3=v * 0.25 * pr.DAYS_PER_YEAR * 0.9) for i, v in enumerate([5000, 5000, 4000, 2500, 1500, 800])]
    fit = pr.fit_to_capacity(rows, liquid_cap_sm3_d=3000.0, uptime=0.9)
    tot = lambda rs, k: sum(r[k] for r in rs)
    for k in ("oil_sm3", "gas_sm3", "water_sm3"):
        assert abs(tot(fit["years"], k) - tot(rows, k)) / tot(rows, k) < 1e-9, k
    assert all(r["oil_sm3_d"] + r["water_sm3_d"] <= 3000.0 * (1 + 1e-9) for r in fit["years"])
    assert fit["extended_years"] > 0 and fit["years"][-1]["year"] > 2035 and fit["lost_boe_sm3"] == 0.0
    assert all(abs(r["water_sm3"] / r["oil_sm3"] - 0.25) < 1e-9 for r in fit["years"] if r["oil_sm3"] > 0), \
        "the mix of what is produced is the mix of what was offered"
    g = pr.fit_to_capacity(rows, gas_cap_msm3_d=0.3, uptime=0.9)
    assert all(r["gas_msm3_d"] <= 0.3 * (1 + 1e-9) for r in g["years"])
    short = pr.fit_to_capacity(rows, liquid_cap_sm3_d=500.0, uptime=0.9, last_year=2040)
    assert short["years"][-1]["year"] == 2040 and short["lost_boe_sm3"] > 0, "beyond the horizon it is lost, and said"
    return True
S.check("reshuffling to the host capacity moves volume later, keeps the mix and loses nothing inside the horizon",
        reshuffle_keeps_every_barrel)


def manual_plus_calculated_fits_the_host():
    lay = demo(stoiip=60e6)
    ws = wells(lay)
    pr.set_manual_profile(lay, ws[0], [dict(year=y, oil_sm3_d=2500) for y in range(1, 6)])
    ps = pr.ProfileSettings(max_years=60)
    free = pr.field_profile(lay, ps)
    cap = fa.FASettings(host_liquid_capacity_sm3_d=3500.0)
    fp = pr.field_profile(lay, ps, fa_settings=cap)
    assert all(y["oil_sm3_d"] + y["water_sm3_d"] <= 3500.0 * (1 + 1e-9) for y in fp["years"])
    assert fp["deferred_boe_sm3"] < 1.0 and fp["reshuffled_boe_sm3"] > 0
    assert abs(fp["total_boe_sm3"] - free["total_boe_sm3"]) / free["total_boe_sm3"] < 1e-9
    full = lambda rs: sum(1 for y in rs if y["oil_sm3_d"] + y["water_sm3_d"] >= 3500.0 * 0.999)
    assert full(fp["years"]) > full(free["years"]), "the host stays full for longer"
    short = pr.field_profile(lay, pr.ProfileSettings(max_years=8), fa_settings=cap)
    return short["deferred_boe_sm3"] > 0 and short["last_year"] <= short["first_year"] + 7
S.check("manual and calculated wells together are reshuffled under the host's capacity, volume kept",
        manual_plus_calculated_fits_the_host)


def two_reservoirs_share_spare_capacity():
    """A fixed split would leave the small reservoir's share idle once it declines."""
    lay = demo(stoiip=60e6)
    ws = wells(lay)
    r = f.new_reservoir("Res B", "black oil")
    r.in_place_sm3, r.recovery_factor, r.drive = 5e6, 0.3, "solution gas / depletion"
    f.set_reservoir(lay, r)
    f.assign_reservoir(lay, ws[:1], "Res B")
    fp = pr.field_profile(lay, fa_settings=fa.FASettings(host_liquid_capacity_sm3_d=3000.0))
    liq = [y["oil_sm3_d"] + y["water_sm3_d"] for y in fp["years"]]
    first_below = next(i for i, v in enumerate(liq[1:], 1) if v < 3000.0 * 0.999)
    assert all(v >= 3000.0 * 0.999 for v in liq[1:first_below])
    return all(v < 3000.0 * 0.999 for v in liq[first_below:]), "once off capacity, it stays off"
S.check("two reservoirs fill the host together: no idle capacity while volume is held back",
        two_reservoirs_share_spare_capacity)

import sys
sys.exit(0 if S.report() else 1)
