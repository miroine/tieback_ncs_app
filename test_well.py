import sys, math
import tb_well as w, tb_multiphase as mp
from _harness import Suite
S = Suite("test_well")
F = mp.Fluid(api=38, gas_sg=0.7, gor_scf_stb=800, water_cut=0.1)

pi = w.IPR("pi", 5000, 6.0)
S.check("PI inflow is linear", lambda: pi.rate(4000) == 6000 and pi.rate(5000) == 0)
S.check("PI AOF", lambda: pi.rate(0) == 30000)
S.check("inflow clipped at reservoir pressure", lambda: pi.rate(6000) == 0)
S.check("bhp inverts rate", lambda: abs(pi.bhp(6000) - 4000) < 1e-3)
S.check("bhp at zero rate = reservoir pressure", lambda: pi.bhp(0) == 5000)
vog = w.IPR("vogel", 4000, 5.0, bubble_point_psia=4000)
S.check("Vogel qmax = PI·pb/1.8", lambda: abs(vog.rate(0) - 5.0 * 4000 / 1.8) < 1e-9)
S.check("Vogel at half bubble point (1 − 0.2x − 0.8x²)",
        lambda: abs(vog.rate(2000) - 5.0 * 4000 / 1.8 * (1 - 0.2 * 0.5 - 0.8 * 0.25)) < 1e-9)
def vogel_above_pb():
    v = w.IPR("vogel", 5000, 5.0, bubble_point_psia=3000)
    assert abs(v.rate(4000) - 5000) < 1e-9                       # straight line above Pb
    assert v.rate(3000) == 10000 and v.rate(0) > v.rate(1000)
S.check("Vogel is linear above the bubble point and curved below", vogel_above_pb)
gas = w.IPR("gas_backpressure", 5000, gas_c=0.002, gas_n=0.85)
S.check("gas back-pressure AOF", lambda: abs(gas.rate(0) - 0.002 * (5000 ** 2) ** 0.85) < 1e-6)
S.check("gas rate falls with bottom-hole pressure", lambda: gas.rate(1000) > gas.rate(4000) > 0)
S.raises("unknown IPR kind raises", ValueError, lambda: w.IPR("magic"))
S.raises("non-positive PI raises", ValueError, lambda: w.IPR("pi", 5000, 0))
S.check("monotonic: every IPR rate decreases with pressure",
        lambda: all(k.rate(p) >= k.rate(p + 250) for k in (pi, vog, gas) for p in range(0, 4500, 250)))

tub = w.Tubing(9000, 9000, 4.892, geothermal_f=180)
S.check("vertical tubing is 90°", lambda: abs(tub.inclination_deg() - 90) < 1e-9)
S.check("deviated tubing inclination", lambda: abs(w.Tubing(10000, 8660).inclination_deg() - 60) < 0.1)
def vlp_falls():
    q = [1000, 4000, 8000, 14000]
    whp = [w.wellhead_pressure(F, x, x * 0.111, pi, tub)["whp_psia"] for x in q]
    assert all(a > b for a, b in zip(whp, whp[1:])), whp
S.check("wellhead pressure falls as rate rises (VLP + IPR)", vlp_falls)
def vlp_hydrostatic():
    r = w.wellhead_pressure(F, 500, 0, pi, tub, segments=40)
    assert 0 < r["whp_psia"] < pi.bhp(500)                        # lifts against the column
    assert len(r["profile"]) == 40 and r["profile"][-1]["md_ft"] == 9000
S.check("tubing profile marches the full depth and loses pressure upward", vlp_hydrostatic)
S.check("bigger tubing delivers more pressure at the same rate",
        lambda: w.wellhead_pressure(F, 9000, 1000, pi, w.Tubing(9000, 9000, 6.184))["whp_psia"]
        > w.wellhead_pressure(F, 9000, 1000, pi, tub)["whp_psia"])
def op_point():
    op = w.operating_point(F, pi, tub, lambda q: 900 + 0.02 * q, water_cut=0.1)
    assert op["converged"]
    avail = w.wellhead_pressure(F, op["rate_stb_d"], op["rate_stb_d"] * 0.1 / 0.9, pi, tub)["whp_psia"]
    assert abs(avail - (900 + 0.02 * op["rate_stb_d"])) < 2.0     # curves actually intersect there
S.check("operating point is where deliverable meets required pressure", op_point)
S.check("no flow when the network needs more than the well can ever deliver",
        lambda: w.operating_point(F, pi, tub, lambda q: 9e4)["rate_stb_d"] == 0.0)
def op_limit():
    op = w.operating_point(F, pi, tub, lambda q: 1.0, q_max=5000)
    assert op["rate_stb_d"] == 5000 and "constrained" in op["note"]
S.check("rate above the search limit is reported, not silently clipped", op_limit)
S.check("higher back-pressure gives a lower rate",
        lambda: w.operating_point(F, pi, tub, lambda q: 1400 + 0.02 * q)["rate_stb_d"]
        < w.operating_point(F, pi, tub, lambda q: 700 + 0.02 * q)["rate_stb_d"])
sys.exit(0 if S.report() else 1)
