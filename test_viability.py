import sys, copy, yaml
import tb_catalog as c, tb_network as n, tb_viability as v, tb_flowassurance as fa, tb_cost, tb_schedule
from _harness import Suite
S = Suite("test_viability")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
ROWS = v.viability(demo())
def crit(rows, name): return next(r for r in rows if r["criterion"] == name)

S.check("every row carries status, value, target and an action field",
        lambda: all(set(r) == {"group", "criterion", "status", "value", "threshold", "action"} for r in ROWS)
        and all(r["status"] in (v.PASS, v.ATTENTION, v.FAIL, v.NA) for r in ROWS))
S.check("summary counts add up", lambda: sum(v.summary(ROWS).values()) == len(ROWS))
S.check("demo passes layout integrity",
        lambda: crit(ROWS, "No blocking design errors")["status"] == v.PASS
        and crit(ROWS, "Every well routed to a host")["status"] == v.PASS)
S.check("demo flags the turndown hydrate margin",
        lambda: crit(ROWS, "Hydrate margin at turndown")["status"] == v.FAIL)
S.check("verdict reflects the blocking item", lambda: "Not viable" in v.verdict(ROWS))
S.check("failures sort to the top", lambda: v.sort_rows(ROWS)[0]["status"] == v.FAIL)
def broken_layout():
    lay = demo(); del lay.edges["FL1"]
    rows = v.viability(lay)
    assert crit(rows, "Every well routed to a host")["status"] == v.FAIL
S.check("cut flowline fails the routing criterion", broken_layout)
def no_host():
    lay = demo(); lay.remove_node("HOST_A")
    rows = v.viability(lay)
    assert crit(rows, "Host in the layout")["status"] == v.FAIL
    assert crit(rows, "Wells deliver at design rate")["status"] == v.NA
S.check("without a host, flow assurance criteria are not assessed", no_host)
def insulated_passes_turndown():
    lay = demo(); lay.edges["FL1"].item_id = "fl_pip"
    rows = v.viability(lay)
    assert crit(rows, "Hydrate margin at turndown")["status"] in (v.PASS, v.ATTENTION)
    assert crit(rows, "Cool-down beats the no-touch time")["status"] == v.PASS
S.check("insulating the flowline fixes turndown and cool-down", insulated_passes_turndown)
def undeliverable():
    lay = demo()
    for w in ("W1", "W2", "W3", "W4"):
        fa.set_well_inputs(lay, w, fa.WellFA(max_whp_bara=40))
    assert crit(v.viability(lay), "Wells deliver at design rate")["status"] == v.FAIL
S.check("wells that cannot deliver fail the deliverability criterion", undeliverable)
S.check("host capacity exceeded is blocking",
        lambda: crit(v.viability(demo(), fa_settings=fa.FASettings(host_liquid_capacity_sm3_d=1000)),
                     "Host capacity")["status"] == v.FAIL)
def slot_and_rating():
    lay = demo()
    lay.add_node(n.Node("W5", "xt_hxt_10k", 60.501, 2.668, sitp_psi=12000))
    lay.add_edge(n.Edge("J_W5", "jumper_rigid", "W5", "TMPL_A"))
    rows = v.viability(lay)
    assert crit(rows, "Slot capacity not exceeded")["status"] == v.FAIL
    assert crit(rows, "Pressure ratings cover shut-in pressure")["status"] == v.FAIL
S.check("slot overflow and under-rated components are blocking", slot_and_rating)
S.check("a campaign that cannot fit the weather window fails the schedule",
        lambda: crit(v.viability(demo(), sched_settings=tb_schedule.ScheduleSettings(
            marine_window=tb_schedule.Window((7, 1), (7, 5)))), "Schedule builds end to end")["status"] == v.FAIL)
S.check("empty layout is assessed, not crashed",
        lambda: v.summary(v.viability(n.Layout(c.Catalog())))[v.FAIL] >= 1)
sys.exit(0 if S.report() else 1)
