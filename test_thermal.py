import sys, math
from scipy.integrate import solve_ivp
import tb_thermal as T
from _harness import Suite
S = Suite("test_thermal")

def ode_profile():
    m, cp, U, D, Ta, T0, L = 12.0, 2600.0, 8.0, 0.254, 5.0, 70.0, 20000.0
    lam = T.decay_length_m(m, cp, U, D)
    sol = solve_ivp(lambda x, y: -U * math.pi * D / (m * cp) * (y - Ta), (0, L), [T0], rtol=1e-10, atol=1e-10)
    assert abs(sol.y[0][-1] - T.temperature_at(L, T0, Ta, lam)) < 1e-5
S.check("steady-state profile == numerical ODE solution", ode_profile)
S.check("U = 0 → isothermal", lambda: T.temperature_at(1e5, 60, 5, T.decay_length_m(10, 2000, 0, 0.2)) == 60)
S.check("very long line → ambient", lambda: abs(T.temperature_at(1e7, 60, 5, T.decay_length_m(10, 2000, 20, 0.2)) - 5) < 1e-9)
S.check("zero flow → ambient immediately", lambda: T.temperature_at(10, 60, 5, T.decay_length_m(0, 2000, 20, 0.2)) == 5)
S.check("adiabatic mixing energy balance", lambda: abs(T.mix_temperature([(3000.0, 60.0), (1000.0, 20.0)]) - 50.0) < 1e-12)
S.check("stream mass: 1000 STB/d of 10 °API oil = 1.838 kg/s",
        lambda: abs(T.stream_mass(1000, 0, 0, 10.0, 0.7).oil_kg_s - 1000 * 0.158987 * 999 / 86400) < 1e-9)
S.check("stream mass gas: 1000 STB × 1000 scf/STB × 0.0764 lb × 0.7 SG",
        lambda: abs(T.stream_mass(1000, 0, 1000, 35, 0.7).gas_kg_s - 1e6 * 0.0764 * 0.45359 * 0.7 / 86400) < 1e-9)
def cooldown_ode():
    t0, th_, ta, U, d, w, rho, cp = 40.0, 18.0, 5.0, 4.0, 0.254, 0.015, 800.0, 2500.0
    cap = math.pi * d ** 2 / 4 * rho * cp + math.pi * ((d + 2 * w) ** 2 - d ** 2) / 4 * T.RHO_STEEL * T.CP_STEEL
    k = U * math.pi * d / cap
    ev = lambda t, y: y[0] - th_
    ev.terminal = True
    sol = solve_ivp(lambda t, y: -k * (y - ta), (0, 1e7), [t0], events=ev, rtol=1e-10, atol=1e-10)
    assert abs(sol.t_events[0][0] / 3600 - T.cooldown_hours(t0, th_, ta, U, d, w, rho, cp)) < 1e-4
S.check("cool-down time == lumped-capacitance ODE event time", cooldown_ode)
S.check("cool-down: already below hydrate T → 0 h", lambda: T.cooldown_hours(10, 15, 5, 5, 0.2, 0.01, 800, 2500) == 0)
S.check("cool-down: hydrate T below ambient → never", lambda: math.isinf(T.cooldown_hours(30, 4, 5, 5, 0.2, 0.01, 800, 2500)))
S.check("insulation (lower U) lengthens cool-down",
        lambda: T.cooldown_hours(40, 18, 5, 1, 0.254, 0.015, 800, 2500) > T.cooldown_hours(40, 18, 5, 10, 0.254, 0.015, 800, 2500))
S.check("Towler-Mokhatab 0.6 SG at 1000 psia ≈ 61.1 °F",
        lambda: abs(T.hydrate_temperature_f(1000, 0.6) - (13.47 * math.log(1000) + 34.27 * math.log(0.6)
                                                          - 1.675 * math.log(1000) * math.log(0.6) - 20.35)) < 1e-12
        and 60.5 < T.hydrate_temperature_f(1000, 0.6) < 61.5)
S.check("hydrate T rises with pressure", lambda: T.hydrate_temperature_f(3000, 0.7) > T.hydrate_temperature_f(1000, 0.7) > T.hydrate_temperature_f(300, 0.7))
S.check("heavier gas forms hydrates warmer", lambda: T.hydrate_temperature_f(1000, 0.8) > T.hydrate_temperature_f(1000, 0.6))
S.check("Hammerschmidt 20 wt % MeOH = 18.22 °F", lambda: abs(T.hammerschmidt_depression_f("Methanol", 20) - 2335 * 20 / (32.04 * 80)) < 1e-12)
S.check("Hammerschmidt MEG 30 wt %", lambda: abs(T.hammerschmidt_depression_f("MEG", 30) - 2700 * 30 / (62.07 * 70)) < 1e-12)
S.check("no inhibitor → 0", lambda: T.hammerschmidt_depression_f("None", 30) == 0)
S.raises("inhibitor 100 wt % raises", ValueError, lambda: T.hammerschmidt_depression_f("MEG", 100))
S.check("margin: inhibitor adds its depression in °C",
        lambda: abs(T.hydrate_margin_c(15, 1500, 0.7, "Methanol", 20) - T.hydrate_margin_c(15, 1500, 0.7) - 18.2179 / 1.8) < 1e-3)
S.check("°C/°F round trip", lambda: abs(T.f_to_c(T.c_to_f(12.3)) - 12.3) < 1e-12)
sys.exit(0 if S.report() else 1)
