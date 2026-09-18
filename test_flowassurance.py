import sys, copy, math, yaml
import tb_catalog as c, tb_network as n, tb_flowassurance as fa, tb_multiphase as mp, tb_thermal as th, tb_project as pj
import tb_cost, tb_schedule
from _harness import Suite
S = Suite("test_flowassurance")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
R = fa.solve(demo())

S.check("demo: 4 wells deliverable, no errors", lambda: all(w["deliverable"] for w in R.wells) and len(R.wells) == 4
        and not [f for f in R.findings if f[0] == "error"])
def monotone():
    path = ["W1", "TMPL_A", "PLET_T", "PLET_H", "RB1", "HOST_A"]
    ps = [R.node_pressure_bara[x] for x in path]
    assert all(a > b for a, b in zip(ps[:-1], ps[1:])) and abs(ps[-1] - 30.0) < 1e-9
S.check("pressure decreases monotonically well → host, host = arrival", monotone)
S.check("rate conservation: FL1 carries 4 wells, J_W1 one",
        lambda: abs(R.edges["FL1"].oil_sm3_d - 4000) < 1e-6 and abs(R.edges["J_W1"].oil_sm3_d - 1000) < 1e-6)
S.check("arrival T between seabed and wellhead", lambda: 6.0 < R.host["HOST_A"]["arrival_t_c"] < 70.0)
S.check("temperature continuous along path (edge out = next edge in)",
        lambda: abs(R.edges["FL1"].t_out_c - R.edges["J_RB"].t_in_c) < 1e-9)
S.check("host liquid = oil + water", lambda: abs(R.host["HOST_A"]["liquid_sm3_d"] - 4000 / 0.9) < 1e-6)
S.check("depth inheritance: XT jumpers level with template", lambda: R.edges["J_W1"].theta_deg == 0.0)
S.check("unsized well jumper uses setting, template jumper matches flowline",
        lambda: R.edges["J_W1"].d_in == 6.0 and R.edges["J_T"].d_in == 10.0)
S.check("riser inclined uphill (140 m over 350 m)", lambda: abs(R.edges["RISER1"].theta_deg - math.degrees(math.asin(140 / 350))) < 1e-9)

def independent_march():
    """Single-well straight flowline: solver == hand composition of the same primitives."""
    cat = c.Catalog(); lay = n.Layout(cat)
    lay.add_node(n.Node("H", "host_tiein", 60.6, 2.5)); lay.add_node(n.Node("W", "xt_vxt_10k", 60.55, 2.5, water_depth_m=100))
    lay.add_edge(n.Edge("F", "fl_rigid_cs", "W", "H", diameter_in=8, length_m=5000))
    s = fa.FASettings(arrival_bara=40, seabed_temp_c=5, segment_length_m=500, include_jt=False)
    w = fa.WellFA(oil_sm3_d=800, water_cut=0.2, gor_sm3_sm3=150, wht_c=60, max_whp_bara=200)
    res = fa.solve(lay, s, {"W": w})
    L, theta = 5000.0, math.degrees(math.asin(100 / 5000))
    q_o, q_w = w.rates_stb_d(); flu = w.fluid()
    m = th.stream_mass(q_o, q_w, flu.gor_scf_stb, flu.api, flu.gas_sg)
    lam = th.decay_length_m(m.total, m.cp, fa.DEFAULT_U_W_M2K["fl_rigid_cs"], 8 * 0.0254)
    p, n_seg = 40 * fa.BARA_TO_PSIA, 10
    for i in range(n_seg, 0, -1):
        t = th.temperature_at((i - 0.5) * 500, 60, 5, lam)
        g = mp.segment_gradient(flu, q_o, q_w, p, th.c_to_f(t), 8, theta, 0.0018 / 8)
        p += g["dpdl"] * 500 * fa.M_TO_FT
    assert abs(res.node_pressure_bara["W"] - p / fa.BARA_TO_PSIA) < 1e-9
S.check("solver equals independent march of the same correlations", independent_march)
def sweep():
    rows = fa.diameter_sweep(demo(), "FL1", [8, 10, 12, 14])
    req = [r["max_required_whp_bara"] for r in rows]; tout = [r["edge_t_out_c"] for r in rows]
    assert all(a > b for a, b in zip(req, req[1:])) and all(a > b for a, b in zip(tout, tout[1:]))
S.check("bigger flowline: lower required WHP but colder arrival", sweep)
def not_deliverable():
    lay = demo()
    for w in ("W1", "W2", "W3", "W4"):
        fa.set_well_inputs(lay, w, fa.WellFA(max_whp_bara=50))
    r = fa.solve(lay)
    assert sum(1 for f in r.findings if f[0] == "error" and "not deliverable" in f[2]) == 4
S.check("insufficient wellhead pressure flagged per well", not_deliverable)
def capacity():
    r = fa.solve(demo(), fa.FASettings(host_liquid_capacity_sm3_d=3000, host_gas_capacity_msm3_d=0.5))
    msgs = [f[2] for f in r.findings if f[1] == "HOST_A"]
    assert any("Liquid" in m for m in msgs) and any("Gas" in m for m in msgs)
S.check("host liquid and gas capacity checks", capacity)
def insulation():
    lay = demo(); lay.edges["FL1"].item_id = "fl_pip"
    assert fa.solve(lay).edges["FL1"].t_out_c > R.edges["FL1"].t_out_c + 20
S.check("pipe-in-pipe arrives much warmer than bare CS", insulation)
def u_override():
    lay = demo(); lay.edges["FL1"].attrs["u_w_m2k"] = 0.0
    no_jt = fa.solve(lay, fa.FASettings(include_jt=False))
    assert abs(no_jt.edges["FL1"].t_out_c - no_jt.edges["FL1"].t_in_c) < 1e-9        # no heat loss
    with_jt = fa.solve(lay, fa.FASettings(include_jt=True))
    assert with_jt.edges["FL1"].t_out_c < with_jt.edges["FL1"].t_in_c                 # still cools by expansion
S.check("per-edge U override: U=0 is isothermal without JT, and cools by expansion with it", u_override)
def inhibitor():
    r2 = fa.solve(demo(), fa.FASettings(inhibitor="MEG", inhibitor_wt_pct=30))
    assert abs((r2.edges["FL1"].min_hydrate_margin_c - R.edges["FL1"].min_hydrate_margin_c)
               - th.hammerschmidt_depression_f("MEG", 30) / 1.8) < 1e-9
S.check("MEG raises hydrate margin by the Hammerschmidt depression", inhibitor)
def cold():
    lay = demo()
    for w in ("W1", "W2", "W3", "W4"):
        fa.set_well_inputs(lay, w, fa.WellFA(wht_c=12, gas_sg=0.8))
    r = fa.solve(lay, fa.FASettings(seabed_temp_c=4))
    assert any(f[0] == "error" and "hydrate region" in f[2] for f in r.findings)
S.check("cold wellstream enters hydrate region → error", cold)
def deh():
    lay = demo(); lay.edges["FL1"].item_id = "fl_deh"
    r = fa.solve(lay)
    assert r.edges["FL1"].heated and not any(f[1] == "FL1" and "Cool-down" in f[2] for f in r.findings)
S.check("DEH line exempt from cool-down/hydrate warnings", deh)
def no_path():
    lay = demo(); lay.add_node(n.Node("W9", "xt_vxt_10k", 60.4, 2.4))
    r = fa.solve(lay)
    assert any(f[1] == "W9" and "No production path" in f[2] for f in r.findings) and all(w["well"] != "W9" for w in r.wells)
S.check("well without path excluded with error", no_path)
def loop():
    lay = demo()
    real = lay.path_to_host
    def fake(w, kinds=None):
        nodes, edges = real(w)
        if w == "W2":  # pretend W2 routes TMPL_A → W1 (conflicting direction)
            return ["W2", "TMPL_A", "W1", "HOST_A"], ["J_W2", "J_W1", "RISER1"]
        return nodes, edges
    lay.path_to_host = fake
    assert any("looped network" in f[2] for f in fa._flow_tree(lay)[2])
S.check("conflicting flow directions detected as looped network", loop)
def persist():
    lay = demo(); fa.set_well_inputs(lay, "W1", fa.WellFA(oil_sm3_d=1234, max_whp_bara=99))
    lay.edges["FL1"].attrs["u_w_m2k"] = 2.5
    txt = pj.project_to_yaml("x", lay, tb_cost.CostSettings(), tb_schedule.ScheduleSettings(), fa.FASettings(arrival_bara=25))
    _, lay2, _, _, fas = pj.project_from_yaml_full(txt)
    assert fa.well_inputs(lay2)["W1"].oil_sm3_d == 1234 and lay2.edges["FL1"].attrs["u_w_m2k"] == 2.5 and fas.arrival_bara == 25
S.check("well inputs, U overrides and FA settings persist in project file", persist)
def section():
    sec = fa.path_section(demo(), R, "W1")
    assert sec[0]["pipe_elev_m"] == -150.0 and sec[0]["kind"] == "jumper"
    assert sec[-1]["kind"] == "riser" and sec[-1]["pipe_elev_m"] > sec[-1]["seabed_elev_m"]
    assert all(a["distance_m"] <= b["distance_m"] for a, b in zip(sec, sec[1:]))
    assert all(r["seabed_elev_m"] <= r["pipe_elev_m"] + 1e-9 for r in sec)
    flow = [r for r in sec if r["kind"] == "flowline"]
    assert all(abs(r["seabed_elev_m"] - r["pipe_elev_m"]) < 1e-9 for r in flow)   # lies on the seabed
S.check("route section: distances increase, flowline on seabed, riser climbs above it", section)
def section_marks():
    ms = fa.section_nodes(demo(), R, "W1")
    assert [m["node"] for m in ms] == ["W1", "TMPL_A", "PLET_T", "PLET_H", "RB1", "HOST_A"]
    assert ms[0]["distance_m"] == 0 and ms[-1]["elev_m"] == 0 and ms[-1]["p_bara"] == 30.0
S.check("section node markers follow the production path to the host", section_marks)
S.check("section of a well with no path is empty",
        lambda: fa.path_section(n.Layout(c.Catalog()), R, "W1") == [])
def xsec():
    cat = c.Catalog()
    pip = fa.pipe_cross_section(cat.get("fl_pip"), 10)
    names = [l["name"] for l in pip]
    assert names[0] == "Bore (fluid)" and "Insulation" in names and names[-1] == "Outer carrier pipe"
    assert pip[0]["outer_mm"] == 254.0 and abs(pip[1]["outer_mm"] - (254 + 2 * 0.63 * 25.4)) < 1e-9
    assert all(a["outer_mm"] <= b["outer_mm"] for a, b in zip(pip, pip[1:]))
    flex = fa.pipe_cross_section(cat.get("fl_flex"), 8)
    assert "Flexible armour layers" in [l["name"] for l in flex]
    assert "DEH piggyback cable" in [l["name"] for l in fa.pipe_cross_section(cat.get("fl_deh"), 10)]
S.check("pipe cross-section build-up: bore, wall/armour, insulation, coating, carrier", xsec)
def smooth_length():
    lay = demo(); lay.edges["FL1"].attrs["smooth"] = True
    r2 = fa.solve(lay)
    assert r2.edges["FL1"].length_m > R.edges["FL1"].length_m
    assert r2.wells[0]["required_whp_bara"] > R.wells[0]["required_whp_bara"]   # longer line, more ΔP
S.check("smoothed routing feeds the longer as-laid length into hydraulics", smooth_length)
def jt_cools():
    hot = fa.solve(demo(), fa.FASettings(include_jt=False))
    cold = fa.solve(demo(), fa.FASettings(include_jt=True))
    assert cold.edges["FL1"].t_out_c < hot.edges["FL1"].t_out_c - 0.5      # cools over a 35 bar drop
    assert cold.edges["FL1"].min_hydrate_margin_c < hot.edges["FL1"].min_hydrate_margin_c
S.check("Joule-Thomson cooling lowers arrival temperature and hydrate margin", jt_cools)
S.check("JT off makes the extra P/T passes irrelevant",
        lambda: abs(fa.solve(demo(), fa.FASettings(include_jt=False, pt_iterations=1)).edges["FL1"].t_out_c
                    - fa.solve(demo(), fa.FASettings(include_jt=False, pt_iterations=5)).edges["FL1"].t_out_c) < 1e-12)
def jt_converges():
    a = fa.solve(demo(), fa.FASettings(include_jt=True, pt_iterations=3)).edges["FL1"].t_out_c
    b = fa.solve(demo(), fa.FASettings(include_jt=True, pt_iterations=6)).edges["FL1"].t_out_c
    assert abs(a - b) < 0.05
S.check("P/T coupling has converged by three passes", jt_converges)
def inventory():
    r = fa.solve(demo())
    e = r.edges["FL1"]
    import math as _m
    vol = _m.pi * (e.d_in * 0.0254) ** 2 / 4 * e.length_m
    assert 0 < e.liquid_inventory_m3 < vol and abs(e.liquid_inventory_m3 / vol - e.max_holdup) < 0.5
S.check("liquid inventory is a sensible fraction of the pipe volume", inventory)
def turndown():
    rows = fa.rate_sensitivity(demo(), None, None, (1.0, 0.5, 0.3))
    assert [r["fraction"] for r in rows] == [1.0, 0.5, 0.3]
    assert rows[0]["surge_vs_design_m3"] == 0.0
    assert all(a["arrival_t_c"] > b["arrival_t_c"] for a, b in zip(rows, rows[1:]))   # colder at lower rate
    assert all(a["max_required_whp_bara"] > b["max_required_whp_bara"] for a, b in zip(rows, rows[1:]))
    assert rows[-1]["min_hydrate_margin_c"] < rows[0]["min_hydrate_margin_c"]
S.check("turndown: lower rates arrive colder with less back-pressure and thinner hydrate margin", turndown)
S.check("scale_rates leaves the original inputs untouched",
        lambda: (lambda ws: (fa.scale_rates(ws, 0.5), ws["W1"].oil_sm3_d == 1000.0)[-1])(fa.well_inputs(demo())))
def slug_flag():
    lay = demo()
    for w_ in ("W1", "W2", "W3", "W4"):
        fa.set_well_inputs(lay, w_, fa.WellFA(oil_sm3_d=120, gor_sm3_sm3=60))
    r = fa.solve(lay)
    assert r.edges["RISER1"].min_gas_velocity_m_s < 3.0 and r.edges["RISER1"].slug_risk
    assert any("Severe slugging" in f[2] for f in r.findings)
S.check("low gas velocity in the riser raises a severe slugging flag", slug_flag)
def coupled():
    import tb_well as tw
    lay = demo()
    iprs = {f"W{i}": tw.IPR("pi", 4500, 4.0) for i in range(1, 5)}
    tubs = {f"W{i}": tw.Tubing(8500, 8000, 4.892, geothermal_f=175) for i in range(1, 5)}
    res, rates, info = fa.solve_coupled(lay, None, None, iprs, tubs)
    assert info["converged"] and all(q > 0 for q in rates.values())
    # the solved rate must sit on both curves: network requirement == tubing deliverability
    req = {w["well"]: w["required_whp_bara"] for w in res.wells}
    for wid in rates:
        q_stb = rates[wid] * fa.SM3_TO_STB
        avail = tw.wellhead_pressure(fa.WellFA(oil_sm3_d=rates[wid]).fluid(), q_stb, q_stb * 0.1 / 0.9,
                                     iprs[wid], tubs[wid])["whp_psia"] / fa.BARA_TO_PSIA
        assert abs(avail - req[wid]) / req[wid] < 0.10
S.check("coupled nodal solve lands on both the well and network curves", coupled)
def coupled_weaker_reservoir():
    import tb_well as tw
    strong = fa.solve_coupled(demo(), None, None, {"W1": tw.IPR("pi", 5000, 4.0)}, {})[1]["W1"]
    weak = fa.solve_coupled(demo(), None, None, {"W1": tw.IPR("pi", 3000, 4.0)}, {})[1]["W1"]
    assert weak < strong
S.check("lower reservoir pressure gives a lower solved rate", coupled_weaker_reservoir)
S.check("no IPR given → rates unchanged",
        lambda: fa.solve_coupled(demo())[2]["note"].startswith("No IPR"))
def seabed_profile_routing():
    import math as _m
    lay = demo()
    flat = fa.solve(lay).edges["FL1"]
    prof = [150 - 18 * _m.sin(_m.pi * i / 39) + (6 if 20 < i < 26 else 0) for i in range(40)]
    lay.edges["FL1"].attrs["seabed_profile"] = [round(x, 1) for x in prof]
    rough = fa.solve(lay).edges["FL1"]
    assert rough.uses_seabed_profile and not flat.uses_seabed_profile
    assert rough.p_in_bara > flat.p_in_bara            # undulations cost extra back-pressure
    assert len(rough.elevations) == len(rough.profile) + 1
    assert min(r["elev_m"] for r in rough.profile) < -140 and max(r["elev_m"] for r in rough.profile) > -140
    off = fa.solve(lay, fa.FASettings(use_seabed_profile=False)).edges["FL1"]
    assert abs(off.p_in_bara - flat.p_in_bara) < 1e-9  # setting turns it back off
S.check("stored seabed profile drives per-station inclination", seabed_profile_routing)
def free_span_finding():
    lay = demo()
    prof = [150.0] * 40
    for i in (18, 19, 20, 21):
        prof[i] = 156.0                                # a 4-sample depression
    lay.edges["FL1"].attrs["seabed_profile"] = prof
    r = fa.solve(lay)
    assert r.edges["FL1"].free_spans and any("free span" in f[2] for f in r.findings)
S.check("seabed depression raises a free-span finding", free_span_finding)
def section_reaches_host():
    sec = fa.path_section(demo(), R, "W1")
    assert abs(sec[-1]["pipe_elev_m"]) < 1e-9 and abs(sec[-1]["p_bara"] - 30.0) < 1e-9
S.check("route section closes at the host at sea level", section_reaches_host)
S.raises("unknown inhibitor raises", ValueError, lambda: fa.FASettings(inhibitor="Glycerol"))
S.raises("bad water cut raises", ValueError, lambda: fa.WellFA(water_cut=1.2))
def blend_check():
    f1, f2 = mp.Fluid(api=30, gas_sg=0.65, gor_scf_stb=500, water_cut=0.0), mp.Fluid(api=50, gas_sg=0.85, gor_scf_stb=1500, water_cut=0.5)
    f, qo, qw = fa.blend([(f1, 1000, 0), (f2, 1000, 1000)])
    assert qo == 2000 and qw == 1000 and f.api == 40 and f.gor_scf_stb == 1000
    assert abs(f.gas_sg - (0.65 * 5e5 + 0.85 * 1.5e6) / 2e6) < 1e-12 and abs(f.water_cut - 1 / 3) < 1e-12
S.check("stream blending conserves oil, water and gas; volume-weighted API/SG", blend_check)
S.check("empty layout solves to empty result", lambda: fa.solve(n.Layout(c.Catalog())).wells == [])
sys.exit(0 if S.report() else 1)
