import sys, copy, yaml, numpy as np
import tb_catalog as c, tb_network as n, tb_schedule as s, tb_cost as k
from _harness import Suite
S = Suite("test_cost")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(cat=None): return n.Layout.from_dict(copy.deepcopy(FIX), cat or c.Catalog())
LAY = demo(); EST = k.estimate(LAY); L = {l["element_id"]: l for l in EST["lines"]}

def fl1():
    Lm = LAY.edge_length(LAY.edges["FL1"]); q = Lm * 10
    l = L["FL1"]
    assert abs(l["procurement"] - 95 * q) < 1e-6 and abs(l["fabrication"] - 45 * q) < 1e-6
    assert abs(l["engineering"] - 0.08 * 140 * q) < 1e-6
    assert abs(l["installation"] - 0.35 * Lm / 1000 * 1.25 * 450_000) < 1e-6
S.check("FL1 hand calc (per inch-metre, weather factor, PLV rate)", fl1)
def umb():
    Lm = LAY.edge_length(LAY.edges["UMB1"]); assert abs(L["UMB1"]["procurement"] - 900 * Lm) < 1e-6
S.check("umbilical per-metre basis", umb)
S.check("XT unit basis", lambda: L["W1"]["procurement"] == 7.0e6)
S.check("host spread not weather-factored",
        lambda: abs(L["HOST_A"]["installation"] - 60 * 120_000) < 1e-6)
def sums():
    bc = EST["by_category"]
    assert abs(sum(bc.values()) - EST["total_usd"]) < 1e-3
    pre = sum(bc[x] for x in k.CATEGORIES[:6])
    assert abs(bc["Owner's costs"] - 0.05 * pre) < 1e-3
    assert abs(bc["Contingency"] - 0.15 * EST["base_usd"]) < 1e-3
    assert abs(bc["Survey & pre-commissioning"] - 0.08 * bc["Installation"]) < 1e-3
S.check("category roll-up, owner's cost, contingency, survey fractions", sums)
def mob():
    cat = LAY.catalog
    used = {sp for sp, _ in EST["spreads_used"]}
    assert used == {"rig", "hlv", "csv", "plv", "ulv", "host"}
    assert abs(EST["by_category"]["Mob/demob"] - sum(cat.spreads[x].mob_demob_usd for x in used)) < 1e-6
S.check("mob/demob once per spread per phase", mob)
def mob_phase2():
    lay = demo(); lay.edges["J_W4"].phase = 2; lay.nodes["W4"].phase = 2
    e = k.estimate(lay)
    assert e["mob_by_phase"][2] == lay.catalog.spreads["csv"].mob_demob_usd + lay.catalog.spreads["rig"].mob_demob_usd
S.check("second phase campaign pays its own mob", mob_phase2)
def factor():
    e = k.estimate(LAY, k.CostSettings(element_factor={"FL1": 2.0}))
    assert abs(e["lines"][[l["element_id"] for l in e["lines"]].index("FL1")]["direct"] - 2 * L["FL1"]["direct"]) < 1e-6
S.check("element factor scales element", factor)
def override():
    e = k.estimate(LAY, k.CostSettings(element_override_usd={"TMPL_A": 30e6}))
    t = [l for l in e["lines"] if l["element_id"] == "TMPL_A"][0]
    assert abs(t["direct"] - 30e6) < 1e-6 and t["overridden"]
S.check("element override sets direct cost exactly", override)
def mc_det():
    cat = c.Catalog()
    for iid in list(cat.items): cat.override(iid, uncertainty=(1.0, 1.0, 1.0))
    for sp in cat.spreads.values(): sp.uncertainty = (1.0, 1.0, 1.0)
    lay = demo(cat); mc = k.monte_carlo(lay, n=200)
    assert np.allclose(mc["samples"], k.estimate(lay)["base_usd"], rtol=1e-12)
S.check("zero-uncertainty Monte Carlo == deterministic base", mc_det)
MC = k.monte_carlo(LAY, n=4000, seed=7)
S.check("P10 < P50 < P90", lambda: MC["P10"] < MC["P50"] < MC["P90"])
S.check("MC reproducible with seed", lambda: k.monte_carlo(LAY, n=4000, seed=7)["P50"] == MC["P50"])
S.check("P50 within ±15 % of deterministic base (right-skewed ranges)",
        lambda: 1.0 < MC["P50"] / EST["base_usd"] < 1.15)
SCH, EM = s.build_from_layout(LAY)
PH = k.phase_costs(EST, SCH, EM)
S.check("phased total == estimate total", lambda: abs(PH["total_usd"] - EST["total_usd"]) < 1e-3)
S.check("annual sums == total", lambda: abs(sum(PH["annual"].values()) - EST["total_usd"]) < 1e-3)
def no_early():
    award = SCH.activities["P1_AWARD"].es
    m_award = (award.year - PH["origin"].year) * 12 + award.month - 1
    assert PH["monthly"][:m_award].sum() == 0
S.check("no CAPEX before contract award month", no_early)
S.check("no CAPEX after first oil year", lambda: max(y for y, v in PH["annual"].items() if v > 0)
        <= SCH.activities["P1_FIRST_OIL"].es.year)
def spread_fn():
    p = np.zeros(6); k._spread(p, 0.5, 2.5, 100.0)
    assert np.allclose(p, [25, 50, 25, 0, 0, 0])
S.check("fractional-month spreading exact", spread_fn)
def templates_cost_and_schedule():
    import pathlib, tb_schedule as sch
    for f in sorted(pathlib.Path("templates").glob("*.yaml")):
        lay = n.Layout.from_dict(yaml.safe_load(f.read_text()), c.Catalog())
        est = k.estimate(lay)
        s_, em = sch.build_from_layout(lay)
        ph = k.phase_costs(est, s_, em)
        assert est["total_usd"] > 0 and abs(ph["total_usd"] - est["total_usd"]) < 1e-3, f.name
S.check("every template costs and schedules end to end", templates_cost_and_schedule)
def cost_library_template():
    txt = open("library/cost_library_template.yaml").read()
    lib = c.Catalog.from_yaml(txt)
    assert lib.to_dict() == c.Catalog().to_dict() and "Replace the numbers below" in txt
S.check("shipped cost library template reloads into an identical catalog", cost_library_template)
def piggyback_cost():
    lay = demo(); lay.add_edge(n.Edge("CHEM1", "chem_line", "PLET_T", "PLET_H"))
    alone = k.estimate(lay)
    lay.edges["CHEM1"].attrs["piggyback_on"] = "FL1"
    strapped = k.estimate(lay)
    a = [l for l in alone["lines"] if l["element_id"] == "CHEM1"][0]
    b = [l for l in strapped["lines"] if l["element_id"] == "CHEM1"][0]
    assert b["piggyback"] and not a["piggyback"]
    assert abs(b["offshore_days"] / a["offshore_days"] * (a["length_m"] / b["length_m"])
               - k.CostSettings().piggyback_install_frac) < 1e-9
    assert strapped["by_category"]["Installation"] < alone["by_category"]["Installation"]
S.check("strapped line pays a share of lay time and no separate campaign", piggyback_cost)
def piggyback_no_extra_mob():
    lay = demo()
    lay.add_edge(n.Edge("FIB1", "fibre_cable", "PLET_T", "PLET_H", phase=2, attrs={"piggyback_on": "FL1"}))
    est = k.estimate(lay)
    assert 2 not in est["mob_by_phase"]      # rides the carrier's campaign, no phase-2 mobilisation
S.check("strapped line does not mobilise its own spread", piggyback_no_extra_mob)
sys.exit(0 if S.report() else 1)
