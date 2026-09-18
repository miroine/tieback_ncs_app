import sys, copy, yaml, datetime as dt, pathlib
import tb_catalog as c, tb_network as n, tb_cases as cs, tb_cost, tb_schedule, tb_flowassurance as fa
from _harness import Suite
S = Suite("test_cases")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
def case(name="base", lay=None, **kw):
    return cs.snapshot(name, lay or demo(), kw.get("cost", tb_cost.CostSettings()),
                       kw.get("sched", tb_schedule.ScheduleSettings()), kw.get("fa", fa.FASettings()),
                       kw.get("note", ""))
C0 = case()

def restore_identical():
    name, lay, cost, sched, fas, disp = cs.restore(C0)
    assert name == "base" and lay.quantities() == demo().quantities()
    assert cost == tb_cost.CostSettings() and fas.arrival_bara == 30.0 and sched.rigs == 1
S.check("case restores the project exactly", restore_identical)
def snapshot_is_frozen():
    c0 = case("frozen")
    lay = demo(); lay.remove_node("W1")            # edit after snapshotting
    _, restored, _, _, _, _ = cs.restore(c0)
    assert "W1" in restored.nodes
S.check("editing the layout afterwards does not change a saved case", snapshot_is_frozen)
R = cs.summarise(C0)
S.check("summary covers cost, schedule and flow assurance",
        lambda: R["wells"] == 4 and R["capex_total_musd"] > R["capex_base_musd"] > 0
        and isinstance(R["first_production"], dt.date) and R["max_required_whp_bara"] > 30
        and R["errors"] == 0)
S.check("flow assurance can be skipped", lambda: "max_required_whp_bara" not in cs.summarise(C0, False))
def catalog_travels():
    lay = demo(); lay.catalog.override("fl_rigid_cs", procurement_usd=400.0)
    dear = cs.summarise(case("dear", lay))
    assert dear["capex_total_musd"] > R["capex_total_musd"] * 1.1
S.check("a case carries its own catalog rates", catalog_travels)
def compare_and_deltas():
    lay = demo(); lay.edges["FL1"].diameter_in = 14
    rows = cs.compare([C0, case("big line", lay)])
    assert [r["case"] for r in rows] == ["base", "big line"]
    assert rows[1]["max_required_whp_bara"] < rows[0]["max_required_whp_bara"]      # bigger line, less ΔP
    assert rows[1]["capex_total_musd"] > rows[0]["capex_total_musd"]                # but costs more
    d = cs.deltas(rows)
    assert d[0]["capex_total_musd"] == 0.0
    assert abs(d[1]["capex_total_musd"] - (rows[1]["capex_total_musd"] - rows[0]["capex_total_musd"])) < 1e-9
    assert d[1]["first_production_days"] == (rows[1]["first_production"] - rows[0]["first_production"]).days
S.check("comparison and deltas show the size trade-off", compare_and_deltas)
def broken_case_survives():
    lay = demo(); lay.remove_node("HOST_A")
    row = cs.summarise(case("no host", lay))
    assert row["errors"] > 0 and row["case"] == "no host"
S.check("a case with design errors still summarises", broken_case_survives)
def caseset_roundtrip():
    txt = cs.caseset_to_yaml([C0, case("second")])
    back = cs.caseset_from_yaml(txt)
    assert [x["name"] for x in back] == ["base", "second"]
    assert cs.summarise(back[0])["capex_total_musd"] == R["capex_total_musd"]
S.check("case set round-trips through YAML", caseset_roundtrip)
S.raises("wrong schema raises", ValueError, lambda: cs.caseset_from_yaml("schema: other\n"))
S.raises("case without project data raises", ValueError,
         lambda: cs.caseset_from_yaml("schema: tieback_caseset/1\ncases:\n  - name: x\n"))
def templates_compare():
    cases = []
    for f in sorted(pathlib.Path("templates").glob("*.yaml"))[:3]:
        lay = n.Layout.from_dict(yaml.safe_load(f.read_text()), c.Catalog())
        cases.append(cs.snapshot(f.stem, lay, tb_cost.CostSettings(), tb_schedule.ScheduleSettings(), fa.FASettings()))
    rows = cs.compare(cases)
    assert len(rows) == 3 and all(r["capex_total_musd"] > 0 for r in rows)
S.check("shipped templates compare against each other", templates_compare)
sys.exit(0 if S.report() else 1)
