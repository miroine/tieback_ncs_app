import sys, math
from scipy.optimize import brentq
import tb_multiphase as M
from _harness import Suite
S = Suite("test_multiphase")

def s_ref(y):  # Beggs & Brill (1973) as published
    if 1 < y < 1.2:
        return math.log(2.2 * y - 1.2)
    x = math.log(y)
    return x / (-0.0523 + 3.182 * x - 0.8725 * x ** 2 + 0.01853 * x ** 4)

# C1 — friction multiplier
S.check("C1 S(1.1) uses ln(2.2y−1.2) branch", lambda: abs(M.s_factor(1.1) - math.log(1.22)) < 1e-12)
S.check("C1 S(4) uses the general expression (≈0.50, not ln 7.6 = 2.03)",
        lambda: abs(M.s_factor(4.0) - s_ref(4.0)) < 1e-12 and 0.45 < M.s_factor(4.0) < 0.55)
S.check("C1 S(y<1) finite and positive (friction never vanishes)",
        lambda: all(0 < M.s_factor(y) < 1.0 for y in (0.01, 0.1, 0.3, 0.625, 0.95)))
S.check("C1 S continuous at y = 1 and y = 1.2",
        lambda: abs(M.s_factor(1.0 + 1e-9) - M.s_factor(1.0 - 1e-9)) < 1e-6
        and abs(M.s_factor(1.2 - 1e-9) - M.s_factor(1.2 + 1e-9)) < 2e-3)
S.check("C1 S ≥ 0 and exp(S) ≤ 3.5 for y ∈ [0.01, 100] (published expression peaks ≈3.2 near y≈56)",
        lambda: all(1.0 <= math.exp(M.s_factor(10 ** (k / 20))) <= 3.5 for k in range(-40, 41)))
# C2 — dry-gas pattern
S.check("C2 λ=0.005 at high Froude → distributed", lambda: M.flow_pattern(0.005, 1000.0) == "distributed")
S.check("C2 λ=0.005 at low Froude → segregated", lambda: M.flow_pattern(0.005, 1.0) == "segregated")
S.check("pattern map λ=0.5: Fr=30 intermittent (L3<Fr≤L4), Fr=100 distributed (>L4≈53)",
        lambda: M.flow_pattern(0.5, 30.0) == "intermittent" and M.flow_pattern(0.5, 100.0) == "distributed")
# C3
S.check("C3 N_Lv = 1.938·v·(ρ/σ)^¼", lambda: abs(M.liquid_velocity_number(2.0, 55.0, 30.0) - 1.938 * 2 * (55 / 30) ** 0.25) < 1e-12)
# C4
S.check("C4 Standing Rs linear in γg", lambda: abs(M.standing_rs(2000, 180, 35, 0.9) / M.standing_rs(2000, 180, 35, 0.6) - 1.5) < 1e-12)
S.check("C4 Standing Rs hand calc (2000 psia, 180 °F, 35 API, 0.75)",
        lambda: abs(M.standing_rs(2000, 180, 35, 0.75) - 0.75 * ((2000 / 18.2 + 1.4) * 10 ** (0.4375 - 0.1638)) ** 1.2048) < 1e-9)
S.check("C4 oil SG 35 API = 0.8498", lambda: abs(M.oil_sg(35) - 0.84985) < 1e-4)
S.check("C4 Standing Bo hand calc", lambda: abs(M.standing_bo(500, 180, 35, 0.75)
        - (0.9759 + 0.00012 * (500 * math.sqrt(0.75 / (141.5 / 166.5)) + 225) ** 1.2)) < 1e-12)
S.check("Bo in plausible range 1.1–1.5 for Rs 300–900", lambda: all(1.1 < M.standing_bo(rs, 180, 35, 0.75) < 1.6 for rs in (300, 600, 900)))

# Z vs Dranchuk–Abou-Kassem (independent Standing–Katz fit)
def dak(tpr, ppr):
    A = [0.3265, -1.0700, -0.5339, 0.01569, -0.05165, 0.5475, -0.7361, 0.1844, 0.1056, 0.6134, 0.7210]
    def f(r):
        z = (1 + (A[0] + A[1] / tpr + A[2] / tpr ** 3 + A[3] / tpr ** 4 + A[4] / tpr ** 5) * r
             + (A[5] + A[6] / tpr + A[7] / tpr ** 2) * r ** 2 - A[8] * (A[6] / tpr + A[7] / tpr ** 2) * r ** 5
             + A[9] * (1 + A[10] * r ** 2) * r ** 2 / tpr ** 3 * math.exp(-A[10] * r ** 2))
        return z - 0.27 * ppr / (r * tpr)
    r = brentq(f, 1e-6, 3.0)
    return 0.27 * ppr / (r * tpr)
def z_grid():
    tpc, ppc = M.sutton_pseudocritical(0.7)
    worst = 0
    for tpr in (1.3, 1.5, 1.7, 2.0):
        for ppr in (0.5, 1.0, 2.0, 4.0, 6.0):
            z = M.z_factor(ppr * ppc, tpr * tpc - 459.67, 0.7)
            worst = max(worst, abs(z - dak(tpr, ppr)) / dak(tpr, ppr))
    assert worst < 0.03, worst
S.check("Brill-Beggs Z within 3 % of DAK over Tpr 1.3–2, Ppr 0.5–6", z_grid)
S.check("Sutton pseudo-criticals γ=0.7", lambda: M.sutton_pseudocritical(0.7) == (169.2 + 349.5 * 0.7 - 74 * 0.49, 756.8 - 131 * 0.7 - 3.6 * 0.49))
S.check("Lee viscosity plausible 0.012–0.025 cP (0.72, 200 °F, 2000 psia)",
        lambda: 0.012 < M.gas_viscosity_lee(2000, 200, 0.72, M.z_factor(2000, 200, 0.72)) < 0.025)
S.check("gas density ideal limit", lambda: abs(M.gas_density(14.696, 60, 1.0, 1.0) - 14.696 * 28.9647 / (10.7316 * 519.67)) < 1e-12)

def colebrook(re, eps):
    f = 0.02
    for _ in range(100):
        f = (1 / (-2 * math.log10(eps / 3.7 + 2.51 / (re * math.sqrt(f))))) ** 2
    return f
S.check("Haaland within 2 % of Colebrook", lambda: all(abs(M.haaland_friction(re, e) / colebrook(re, e) - 1) < 0.02
        for re in (1e4, 1e5, 1e6, 1e7) for e in (1e-5, 1e-4, 1e-3)))
S.check("laminar 64/Re", lambda: M.haaland_friction(1000, 1e-3) == 0.064)

def liquid_limit():
    rho, mu, d, th, eps, v = 55.0, 2.0, 6.0, 30.0, 0.0003, 4.0
    r = M.beggs_brill(v, 0.0, rho, 3.0, mu, 0.015, d, th, eps, 1000.0)
    re = 1488 * rho * v * 0.5 / mu
    ref = (M.haaland_friction(re, eps) * rho * v ** 2 / (2 * 32.174 * 0.5) + rho * math.sin(math.radians(th))) / 144
    assert r["holdup"] == 1.0 and abs(r["dpdl"] - ref) < 1e-12
S.check("single-phase liquid → Darcy + hydrostatic exactly", liquid_limit)
def gas_limit():
    rho_g, mu_g, d, v, p = 5.0, 0.015, 10.0, 30.0, 1500.0
    r = M.beggs_brill(0.0, v, 55.0, rho_g, 1.0, mu_g, d, 0.0, 0.0002, p)
    re = 1488 * rho_g * v * (10 / 12) / mu_g
    fric = M.haaland_friction(re, 0.0002) * rho_g * v ** 2 / (2 * 32.174 * 10 / 12) / 144
    ek = rho_g * v * v / (32.174 * p * 144)
    assert r["holdup"] == 0.0 and abs(r["dpdl"] - fric / (1 - ek)) < 1e-12
S.check("single-phase gas → Darcy with acceleration term", gas_limit)
def bounds():
    for vsl in (0.05, 0.5, 3.0):
        for vsg in (0.2, 5.0, 40.0):
            for th in (-45, -5, 0, 5, 45, 90):
                r = M.beggs_brill(vsl, vsg, 50, 4, 1, 0.015, 8, th, 0.0005, 800)
                assert r["lam"] - 1e-12 <= r["holdup"] <= 1.0 and r["dpdl"] == r["dpdl"]
S.check("holdup always in [λ, 1] and gradient finite over a regime grid", bounds)
def incl_order():
    g = {th: M.beggs_brill(1.0, 5.0, 50, 4, 1, 0.015, 8, th, 0.0005, 800)["dpdl"] for th in (-20, 0, 20)}
    assert g[20] > g[0] > g[-20]
S.check("gradient uphill > horizontal > downhill", incl_order)
def payne():
    a = M.beggs_brill(1.0, 5.0, 50, 4, 1, 0.015, 8, 10, 0.0005, 800, payne=True)["holdup"]
    b = M.beggs_brill(1.0, 5.0, 50, 4, 1, 0.015, 8, 10, 0.0005, 800, payne=False)["holdup"]
    assert abs(a - max(b * 0.924, 1 / 6)) < 1e-12
S.check("A1 Payne uphill factor 0.924", payne)
def transition_cont():
    lam = 0.2
    l2 = 0.0009252 * lam ** -2.4684
    l3 = 0.10 * lam ** -1.4516
    h = lambda fr: M._horizontal_holdup(M.flow_pattern(lam, fr), lam, fr)
    assert abs(h(l2 * (1 - 1e-9)) - h(l2 * (1 + 1e-9))) < 1e-6
    assert abs(h(l3 * (1 - 1e-9)) - h(l3 * (1 + 1e-9))) < 1e-6
S.check("horizontal holdup continuous across transition boundaries", transition_cont)
S.check("no-flow returns hydrostatic liquid column", lambda: M.beggs_brill(0, 0, 60, 4, 1, 0.01, 6, 90, 0.001, 500)["dpdl"] == 60 / 144)
S.check("erosional velocity C/√ρ", lambda: M.erosional_velocity_ft_s(25.0) == 20.0)
S.raises("Fluid validates API", ValueError, lambda: M.Fluid(api=100))
S.raises("Fluid validates water cut", ValueError, lambda: M.Fluid(water_cut=1.0))
def props_gas_free():
    f = M.Fluid(gor_scf_stb=100.0)
    pr = M.local_properties(f, 1000, 0, 3000, 150)
    assert pr["rs"] == 100.0 and pr["q_gas_ft3_s"] == 0.0   # all gas in solution
S.check("undersaturated: Rs capped at GOR, no free gas", props_gas_free)
sys.exit(0 if S.report() else 1)
