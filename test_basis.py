import sys, copy, yaml, datetime as dt
import tb_catalog as c, tb_network as n, tb_basis as b, tb_cost, tb_schedule, tb_flowassurance as fa
from _harness import Suite
S = Suite("test_basis")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
ROWS = b.design_basis(demo())
def by(item): return next(r for r in ROWS if r["item"] == item)

S.check("every row has category, item, value and a known status",
        lambda: all(set(r) >= {"category", "item", "value", "status"} and
                    r["status"] in ("ok", "default", "missing", "action") for r in ROWS))
S.check("summary counts add up to the row count", lambda: sum(b.summary(ROWS).values()) == len(ROWS))
S.check("outstanding items are the missing and to-resolve ones",
        lambda: {r["item"] for r in b.outstanding(ROWS)} ==
        {r["item"] for r in ROWS if r["status"] in ("missing", "action")})
S.check("blank water depths are flagged", lambda: by("Water depth on subsea equipment")["status"] == "missing"
        and "4 of 8" in by("Water depth on subsea equipment")["value"])
S.check("missing seabed profile flagged", lambda: by("Seabed profile along routes")["status"] == "missing")
S.check("untouched defaults are marked as defaults, not as entered values",
        lambda: by("Contingency")["status"] == "default" and by("Seabed temperature")["status"] == "default")
S.check("built-in rates are flagged for action", lambda: by("Cost library")["status"] == "action")
S.check("loaded cost library clears that item",
        lambda: next(r for r in b.design_basis(demo(), catalog_source="equinor_rates.xlsx")
                     if r["item"] == "Cost library")["status"] == "ok")
# SI units throughout
S.check("pressures in bara", lambda: "bara" in by("Host arrival pressure")["value"]
        and "bara" in by("Shut-in tubing pressure")["value"])
S.check("shut-in pressure converted from psi", lambda: "310" in by("Shut-in tubing pressure")["value"])
S.check("rates in Sm³/d", lambda: "Sm³/d" in by("Design oil/condensate rate")["value"])
S.check("line sizes in mm", lambda: "mm" in by("Line sizes")["value"] and "254" in by("Line sizes")["value"])
S.check("roughness in mm", lambda: "mm" in by("Pipe roughness")["value"])
S.check("oil density in kg/Sm³", lambda: "kg/Sm³" in by("Oil density")["value"])
S.check("temperatures in °C", lambda: "°C" in by("Seabed temperature")["value"])
S.check("cost in MNOK and MUSD", lambda: "MNOK" in by("CAPEX incl. contingency")["value"]
        and "MUSD" in by("CAPEX incl. contingency")["value"])
def nok_rate():
    r12 = next(r for r in b.design_basis(demo(), nok_per_usd=12.0) if r["item"] == "CAPEX incl. contingency")
    r10 = by("CAPEX incl. contingency")
    assert r12["value"] != r10["value"] and "12.00 NOK/USD" in r12["note"]
S.check("exchange rate applied and stated", nok_rate)
S.check("units note is in Norwegian SI terms",
        lambda: "SI-enheter" in b.UNITS_NOTE and "MNOK" in b.UNITS_NOTE)
def entered_values_show_as_ok():
    lay = demo()
    for w in ("W1", "W2", "W3", "W4"):
        fa.set_well_inputs(lay, w, fa.WellFA(oil_sm3_d=1500, water_cut=0.25, gor_sm3_sm3=180, api=34,
                                             gas_sg=0.78, wht_c=62, max_whp_bara=180))
    for nd in lay.nodes.values():
        nd.water_depth_m = nd.water_depth_m or 150
    rows = b.design_basis(lay, fa_settings=fa.FASettings(inhibitor="MEG", inhibitor_wt_pct=30,
                                                         arrival_bara=22, seabed_temp_c=4.5))
    g = {r["item"]: r["status"] for r in rows}
    assert g["Water cut"] == "ok" and g["Hydrate inhibition"] == "ok" and g["Host arrival pressure"] == "ok"
    assert g["Water depth on subsea equipment"] == "ok"
S.check("entered project values change status from default to ok", entered_values_show_as_ok)
def ipr_and_errors():
    lay = demo()
    lay.nodes["W1"].attrs["ipr"] = {"use": True, "kind": "pi", "reservoir_bara": 320}
    assert next(r for r in b.design_basis(lay) if r["item"] == "Inflow performance (IPR)")["status"] == "ok"
    lay.edges["FL1"].diameter_in = 0
    rows = b.design_basis(lay)
    err = next(r for r in rows if r["item"] == "Layout errors")
    assert err["status"] == "action" and err["value"] == "1" and "FL1" in err["note"]
S.check("IPR presence and layout errors are picked up", ipr_and_errors)
def empty_layout():
    rows = b.design_basis(n.Layout(c.Catalog()))
    assert next(r for r in rows if r["item"] == "Wells")["status"] == "missing" and len(rows) > 10
S.check("an empty layout still produces a checklist", empty_layout)
sys.exit(0 if S.report() else 1)
