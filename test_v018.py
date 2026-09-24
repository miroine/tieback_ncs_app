"""v0.18: shutdown/blowdown, contaminants, pressure protection, safety zones, trees and risers."""
import sys, copy, math, yaml
import tb_network as n, tb_catalog as c, tb_flowassurance as fa, tb_shutdown as sd, tb_chemistry as ch
import tb_basis, tb_cost, tb_map as m, tb_multiphase as mp, tb_thermal as th, tb_fluids as tf
from _harness import Suite
S = Suite("test_v018")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
PSI = n.PSI_PER_BAR

# ── hydrate pressure is the inverse of the hydrate curve the app uses ──
def inverse():
    for t in (2.0, 6.0, 12.0):
        p = sd.hydrate_pressure_bara(t, 0.7)
        back = th.f_to_c(th.hydrate_temperature_f(p * PSI, 0.7))
        assert abs(back - t) < 1e-6, (t, back)
    return sd.hydrate_pressure_bara(4, 0.7) < sd.hydrate_pressure_bara(10, 0.7)
S.check("hydrate-free pressure inverts the Towler & Mokhatab curve exactly", inverse)

INV = dict(volume_m3=1000.0, liquid_m3=100.0, gas_volume_m3=900.0, water_m3=20.0, settle_out_bara=80.0,
           riser_volume_m3=20.0, riser_holdup=0.3, riser_depth_m=120.0, rho_liquid=850.0, gas_sg=0.7,
           api=40.0, water_cut=0.2, cooldown_h=10.0, seabed_temp_c=6.0)

def blowdown_mass_balance():
    s = sd.ShutdownSettings(flare_max_kg_s=1e6)
    inv = dict(INV, riser_depth_m=0.0)
    r = sd.blowdown(inv, s)
    # time ≈ mass vented / mean flow — check against a direct re-integration with 4× the steps
    r4 = sd.blowdown(inv, s, steps=800)
    assert abs(r["hours"] - r4["hours"]) / r4["hours"] < 0.01
    assert r["curve"][-1][1] <= r["hydrate_pressure_bara"] + 1e-6 and r["reaches_hydrate_free"]
    return True
S.check("blowdown integrates to the hydrate-free pressure and converges with step size", blowdown_mass_balance)
def bigger_orifice_faster():
    a = sd.blowdown(dict(INV, riser_depth_m=0.0), sd.ShutdownSettings(blowdown_orifice_mm=30, flare_max_kg_s=1e6))
    b = sd.blowdown(dict(INV, riser_depth_m=0.0), sd.ShutdownSettings(blowdown_orifice_mm=60, flare_max_kg_s=1e6))
    return b["hours"] < a["hours"] / 3        # flow scales with area (×4)
S.check("doubling the blowdown restriction cuts the time about four-fold", bigger_orifice_faster)
def flare_limit():
    a = sd.blowdown(dict(INV, riser_depth_m=0.0), sd.ShutdownSettings(blowdown_orifice_mm=200, flare_max_kg_s=2))
    return a["peak_kg_s"] <= 2 + 1e-9
S.check("venting never exceeds the flare capacity", flare_limit)
def choked_flow():
    t_k = 279.15
    z = mp.z_factor(80 * PSI, th.c_to_f(6), 0.7)
    q1 = sd._mass_flow(80, 3, t_k, 0.7, 1e-3, 0.8, 1.27, z)
    q2 = sd._mass_flow(80, 10, t_k, 0.7, 1e-3, 0.8, 1.27, z)
    return abs(q1 - q2) < 1e-9 and sd._mass_flow(3, 3, t_k, 0.7, 1e-3, 0.8, 1.27, 1) == 0.0
S.check("choked flow does not depend on back-pressure; no flow at equal pressure", choked_flow)
def liquid_head_blocks():
    r = sd.blowdown(dict(INV, riser_depth_m=300.0, liquid_m3=500.0))
    assert not r["reaches_hydrate_free"] and "cannot blow the line down" in r["verdict"]
    assert r["floor_worst_bara"] > r["hydrate_pressure_bara"]
    return abs(r["liquid_head_full_bar"] - 850 * 9.80665 * 300 / 1e5) < 1e-9
S.check("a riser full of liquid can stop the host blowing down below the hydrate pressure", liquid_head_blocks)
def already_low():
    r = sd.blowdown(dict(INV, settle_out_bara=5.0, riser_depth_m=0.0))
    return r["reaches_hydrate_free"] and "no blowdown needed" in r["verdict"]
S.check("a line that settles out below the hydrate pressure needs no blowdown", already_low)

def shutdown_sequence():
    r = sd.planned_shutdown(INV, sd.ShutdownSettings(), "MEG", no_touch_h=12)
    acts = [x["action"] for x in r["steps"]]
    assert acts[0].startswith("Inhibit the line with MEG") and any("Close in wells" in a for a in acts)
    inh = r["inhibitor"]
    # mass balance: pure MEG / (water + MEG) = required wt %
    pure = inh["volume_m3"] * 0.9 * ch.INHIBITOR_DENSITY["MEG"]
    water = INV["water_m3"] * sd.RHO_WATER
    assert abs(pure / (pure + water) * 100 - inh["wt_pct"]) < 1e-6
    assert abs(inh["hours"] - inh["volume_m3"] / 2.0) < 1e-9
    return True
S.check("the planned-shutdown inhibitor volume closes its mass balance", shutdown_sequence)
S.check("continuous inhibition already strong enough needs no pre-shutdown dose",
        lambda: sd.planned_shutdown(INV, continuous_wt_pct=80)["steps"][0]["action"].startswith("Keep continuous"))
S.check("an oil line is offered displacement as the alternative",
        lambda: any(x["action"].startswith("…or displace") for x in sd.planned_shutdown(INV)["steps"]))
def slow_injection_warns():
    r = sd.planned_shutdown(INV, sd.ShutdownSettings(inhibitor_injection_m3_h=0.1), no_touch_h=2)
    return "longer than" in r["verdict"]
S.check("inhibiting slower than the no-touch time is flagged", slow_injection_warns)
S.raises("a zero restriction is refused", ValueError, lambda: sd.ShutdownSettings(blowdown_orifice_mm=0))
def demo_inventory():
    lay = demo()
    inv = sd.inventory(lay, fa.solve(lay))
    assert inv["volume_m3"] > 0 and inv["riser_depth_m"] > 0 and 0 < inv["settle_out_bara"] < 200
    r = sd.planned_shutdown(inv)
    return r["steps"] and math.isfinite(r["blowdown"]["hydrate_pressure_bara"])
S.check("the demo field gets an inventory, a sequence and a blowdown result", demo_inventory)

# ── contaminants ──
def contaminants():
    rows = {r["item"]: r for r in ch.contaminants(3.0, 50.0, 200.0, 300.0)}
    assert "9.00 bar" in rows["CO₂ content"]["value"] and "severe" in rows["CO₂ content"]["note"]
    assert rows["H₂S content"]["status"] == "action"          # 50 ppm × 300 bar = 1.5 kPa ≥ 0.3
    assert rows["Mercury"]["status"] == "action" and "removal" in rows["Mercury"]["note"]
    low = {r["item"]: r for r in ch.contaminants(0.01, 1.0, 0.1, 100.0)}
    assert all(r["status"] == "ok" for r in low.values()), low
    none = ch.contaminants(None, None, None, 100)
    return all(r["status"] == "missing" for r in none)
S.check("CO₂, H₂S and mercury are judged by partial pressure / content against their limits", contaminants)
def limits_on_every_row():
    rows = ch.screen(mp.Fluid(api=35, gas_sg=0.75, gor_scf_stb=800, water_cut=0.2),
                     ch.ChemistryInputs(wax_appearance_c=30, co2_mol_pct=2, h2s_ppm=50, mercury_ug_nm3=20),
                     min_temp_c=15, arrival_temp_c=20, seabed_temp_c=6, min_pressure_bara=30,
                     hydrate_margin_c=-3, tieback_km=40)
    assert all(r["limit"] for r in rows), [r["issue"] for r in rows if not r["limit"]]
    return any(r["issue"] == "Mercury" and r["risk"] == ch.MEDIUM for r in rows)
S.check("every production-chemistry threat shows the limit it was judged on", limits_on_every_row)
def basis_contaminants():
    lay = demo()
    r = tf.new_reservoir("Brent", "black oil"); r.co2_mol_pct, r.h2s_ppm, r.mercury_ug_nm3 = 4.0, 20.0, 5.0
    tf.set_reservoir(lay, r)
    rows = {x["item"]: x for x in tb_basis.design_basis(lay) if x["category"] == "Contaminants"}
    assert set(rows) == {"CO₂ content", "H₂S content", "Mercury"}, rows
    assert rows["CO₂ content"]["status"] == "action"
    res_rows = {x["item"]: x for x in tb_basis.design_basis(lay)}
    return "280" in res_rows["Reservoir pressure"]["value"] and "95" in res_rows["Reservoir temperature"]["value"]
S.check("the design basis checks CO₂, H₂S and mercury and reports reservoir P and T", basis_contaminants)

# ── pressure protection ──
def protection():
    lay = demo()
    for w in ("W1", "W2", "W3", "W4"):
        lay.nodes[w].sitp_psi = 300 * PSI
    assert all(r["verdict"] == "fully rated" for r in lay.pressure_protection())
    lay.edges["FL1"].item_id = "fl_flex"
    for w in ("W1", "W2", "W3", "W4"):
        lay.nodes[w].sitp_psi = 600 * PSI                 # above the flexible's 517 bar
    rows = lay.pressure_protection()
    assert all(r["verdict"] == "HIPPS or fully rated" and "FL1" in r["upgrade"] for r in rows)
    node = rows[0]["hipps_node"]
    assert node == "TMPL_A", node
    lay.nodes[node].hipps = True
    rows = lay.pressure_protection()
    assert all(r["verdict"] == "HIPPS in place" and r["hipps_at"] == "TMPL_A" for r in rows)
    basis = {x["item"]: x for x in tb_basis.design_basis(lay)}
    return basis["Pressure protection (HIPPS or fully rated)"]["status"] == "ok"
S.check("pressure protection: fully rated, HIPPS needed, then HIPPS in place", protection)
def rating_message_in_bar():
    lay = demo(); lay.edges["FL1"].item_id = "fl_flex"; lay.nodes["W1"].sitp_psi = 600 * PSI
    msg = next(f.message for f in lay.validate() if f.code == "RATING" and f.element_id == "FL1")
    return "600 bar" in msg and "517 bar" in msg and "fully rated" in msg
S.check("rating findings speak bar and name the two options", rating_message_in_bar)
def hipps_costed():
    lay = demo()
    base = tb_cost.estimate(lay)["total_usd"]
    lay.nodes["TMPL_A"].hipps = True
    est = tb_cost.estimate(lay)
    assert any(l["element_id"] == "TMPL_A_HIPPS" for l in est["lines"])
    return est["total_usd"] > base
S.check("HIPPS ticked on a structure is costed", hipps_costed)

# ── safety zone ──
def zones():
    lay = demo()
    z = m.safety_zones(lay)
    assert len(z) == 1 and z[0]["radius_m"] == 500.0 and "RB1" in z[0]["inside"] and "TMPL_A" not in z[0]["inside"]
    infos = [f for f in lay.validate() if f.code == "SAFETY_ZONE"]
    assert infos and all(f.severity == "info" for f in infos)
    p = m.build_payload(lay)
    assert p["safety_zones"] and not m.build_payload(lay, display=m.DisplaySettings(show_safety_zones=False))["safety_zones"]
    return True
S.check("each host gets a 500 m safety zone, with the items inside it flagged", zones)

# ── trees and risers ──
def catalogue():
    cat = c.Catalog()
    xts = {i.item_id: i.rating_psi for i in cat.items.values() if i.category == "well"}
    assert {5000, 10000, 15000, 20000} <= set(xts.values()), xts
    risers = [i.item_id for i in cat.items.values() if i.category == "riser"]
    for k in ("riser_flex", "riser_flex_lw", "riser_scr", "riser_slwr", "riser_ttr", "riser_hybrid",
              "riser_rigid_fixed", "riser_jtube"):
        assert k in risers, k
    return all(c.edge_allowed("riser", "riser_base", "host") for _ in risers)
S.check("trees 5k–20k and eight riser types are in the catalogue", catalogue)
def riser_swap():
    lay = demo()
    for k in ("riser_scr", "riser_slwr", "riser_ttr", "riser_hybrid", "riser_rigid_fixed", "riser_jtube"):
        lay.edges["RISER1"].item_id = k
        assert not [f for f in lay.validate() if f.severity == "error"], k
        assert tb_cost.estimate(lay)["total_usd"] > 0
        fa.solve(lay)
    return True
S.check("every riser type validates, costs and solves in the demo", riser_swap)

# ── numerical robustness: extreme conditions must not crash a page ──
def z_factor_extremes():
    """Reported from the deployment: a size sweep died with ZeroDivisionError deep in z_factor."""
    for p_, t_, sg_ in ((2.438e5, 656.4, 0.7), (1e7, 900.0, 0.6), (1e-6, -200.0, 1.2),
                        (0.0, 60.0, 0.55), (5e5, 2000.0, 1.0)):
        z = mp.z_factor(p_, t_, sg_)
        assert 0.3 <= z <= 1.4 and z == z, (p_, t_, sg_, z)
    return abs(mp.z_factor(3000, 150, 0.7) - 0.827) < 0.01      # unchanged in the valid range
S.check("the Z-factor stays finite and in range at any pressure or temperature", z_factor_extremes)


def runaway_pressure_is_capped():
    lay = demo()
    w = fa.well_inputs(lay)
    for k, v in w.items():
        v.oil_sm3_d *= 6
        fa.set_well_inputs(lay, k, v)
    rows = {r["diameter_in"]: r for r in fa.diameter_sweep(lay, "FL1", [4.0, 12.0])}
    assert rows[4.0]["max_required_whp_bara"] <= fa.MAX_MARCH_BARA + 1e-6
    assert "too small at this rate" in rows[4.0]["note"] and not rows[12.0]["note"]
    assert rows[12.0]["max_required_whp_bara"] < rows[4.0]["max_required_whp_bara"]
    lay.edges["FL1"].diameter_in = 3.0                       # the same in a plain solve
    res = fa.solve(lay, None, w)
    assert any("too small for" in f[2] for f in res.findings)
    assert all(r.p_in_bara <= fa.MAX_MARCH_BARA + 1e-6 for r in res.edges.values())
    return True
S.check("a line too small for the rate is capped and flagged, not marched into nonsense",
        runaway_pressure_is_capped)

sys.exit(0 if S.report() else 1)
