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
    s = fa.FASettings(arrival_bara=40, seabed_temp_c=5, segment_length_m=500)
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
    assert abs(fa.solve(lay).edges["FL1"].t_out_c - R.edges["FL1"].t_in_c) < 1e-9
S.check("per-edge U override (U=0 → isothermal)", u_override)
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
