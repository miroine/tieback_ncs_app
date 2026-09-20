import sys, copy, datetime as dt, yaml
import tb_catalog as c, tb_network as n, tb_schedule as s
from _harness import Suite
S = Suite("test_schedule")
D = dt.date
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())

def textbook():
    sc = s.Schedule(D(2027, 1, 1))
    sc.add(s.Activity("A", "A", 3)); sc.add(s.Activity("B", "B", 4, [("A", 0)]))
    sc.add(s.Activity("C", "C", 2, [("A", 0)])); sc.add(s.Activity("D", "D", 5, [("B", 0), ("C", 0)]))
    sc.add(s.Activity("E", "E", 1, [("D", 3)]))
    return sc.compute()
T = textbook()
S.check("CPM project duration 16 d (with 3 d lag)", lambda: (T.finish - D(2027, 1, 1)).days == 16)
S.check("CPM critical path A-B-D-E", lambda: T.critical_path() == ["A", "B", "D", "E"])
S.check("CPM float on C = 2 d", lambda: T.activities["C"].total_float_days == 2)
S.check("lag honoured: E starts day 15", lambda: (T.activities["E"].es - D(2027, 1, 1)).days == 15)
S.check("fixed_start respected", lambda: (lambda sc: (sc.add(s.Activity("X", "X", 1, fixed_start=D(2027, 3, 1))),
        sc.compute(), sc.activities["X"].es == D(2027, 3, 1))[-1])(s.Schedule(D(2027, 1, 1))))
W = s.Window()
S.check("window: November start -> 1 Apr next year", lambda: W.fit(D(2027, 11, 1), 10) == D(2028, 4, 1))
S.check("window: would overrun 15 Oct -> next season", lambda: W.fit(D(2027, 10, 10), 10) == D(2028, 4, 1))
S.check("window: in-season start unchanged", lambda: W.fit(D(2027, 6, 1), 10) == D(2027, 6, 1))
S.check("window: January -> 1 Apr same year", lambda: W.fit(D(2027, 1, 15), 30) == D(2027, 4, 1))
S.raises("activity longer than window raises", ValueError, lambda: W.fit(D(2027, 1, 1), 250))
def cyc():
    sc = s.Schedule(D(2027, 1, 1)); sc.add(s.Activity("A", "A", 1, [("B", 0)])); sc.add(s.Activity("B", "B", 1, [("A", 0)])); sc.compute()
S.raises("cycle raises", ValueError, cyc)
def unk():
    sc = s.Schedule(D(2027, 1, 1)); sc.add(s.Activity("A", "A", 1, [("Z", 0)])); sc.compute()
S.raises("unknown predecessor raises", ValueError, unk)
S.raises("negative duration raises", ValueError, lambda: s.Schedule(D(2027, 1, 1)).add(s.Activity("A", "A", -1)))

SCH, EM = s.build_from_layout(demo())
A = SCH.activities
def in_window():
    for a in A.values():
        if a.window and a.duration_days > 0:
            assert D(a.es.year, 4, 1) <= a.es and a.ef <= D(a.es.year, 10, 15), a.act_id
S.check("all marine campaigns inside 1 Apr–15 Oct", in_window)
S.check("award = DG3 + 30 d", lambda: (A["P1_AWARD"].es - A["DG3"].es).days == 30)
S.check("DG3 = DG2 + FEED", lambda: (A["DG3"].es - A["DG2"].es).days == 300)
def fo_logic():
    fo = A["P1_FIRST_OIL"].es
    for k in ("P1_INST_STRUCT", "P1_INST_PIPE", "P1_INST_UMB", "P1_TIEIN", "P1_DRILL_W1", "PDO_OK"):
        assert A[k].ef <= fo, k
S.check("first oil after all infra, first well and PDO approval", fo_logic)
def drill_logic():
    d = A["P1_DRILL_W1"]
    assert d.es >= A["P1_INST_STRUCT"].ef and d.es >= A["PDO_OK"].ef and d.es >= A["P1_PROC_xt_hxt_10k"].ef
S.check("drilling after template install, XT delivery and PDO approval", drill_logic)
S.check("wells drilled sequentially on 1 rig", lambda: A["P1_DRILL_W2"].es == A["P1_DRILL_W1"].ef)
def two_rigs():
    s2, _ = s.build_from_layout(demo(), s.ScheduleSettings(rigs=2))
    assert s2.activities["P1_DRILL_END"].es < A["P1_DRILL_END"].es
    assert s2.activities["P1_DRILL_W2"].es == s2.activities["P1_DRILL_W1"].es
S.check("2 rigs drill in parallel and finish earlier", two_rigs)
def jumper_after_metrology():
    assert A["P1_JUMPER_FAB"].es >= A["P1_METROLOGY"].ef and A["P1_TIEIN"].es >= A["P1_JUMPER_FAB"].ef
S.check("jumper fab follows metrology; tie-in follows fab", jumper_after_metrology)
def offset():
    s2, _ = s.build_from_layout(demo(), s.ScheduleSettings(phase_offset_days={1: 365}))
    delay = (s2.activities["P1_FIRST_OIL"].es - A["P1_FIRST_OIL"].es).days
    assert 360 <= delay <= 372, delay
S.check("phase award offset 365 d delays first oil ~1 yr", offset)
def em():
    q = [r["element_id"] for r in demo().quantities()]
    assert all(e in EM and EM[e] for e in q)
    assert all(v in A for m in EM.values() for v in m.values())
S.check("every element mapped to existing activities", em)
S.check("critical path ends at first oil", lambda: SCH.critical_path()[-1] == "P1_FIRST_OIL")
def weather():
    s2, _ = s.build_from_layout(demo(), s.ScheduleSettings(weather_factor=1.0))
    assert abs(s2.activities["P1_INST_STRUCT"].duration_days * 1.25 - A["P1_INST_STRUCT"].duration_days) < 1e-9
S.check("weather factor scales campaign duration", weather)

# ── user edits to the generated plan ───────────────────────────────────────
def override_changes_the_duration():
    lay = demo()
    base = s.build_from_layout(lay, s.ScheduleSettings())[0]
    st2 = s.ScheduleSettings(duration_overrides={"FEED": 500.0})
    got = s.build_from_layout(lay, st2)[0]
    assert base.activities["FEED"].duration_days != 500.0
    assert got.activities["FEED"].duration_days == 500.0
    assert got.activities["DG3"].es > base.activities["DG3"].es, "DG3 must move with FEED"
    return True
S.check("a duration override moves the activities after it", override_changes_the_duration)

S.check("an override for an unknown activity is ignored",
        lambda: s.build_from_layout(demo(), s.ScheduleSettings(
            duration_overrides={"NOT_AN_ACTIVITY": 10.0}))[0] is not None)
S.check("a negative override is ignored rather than breaking the network",
        lambda: s.build_from_layout(demo(), s.ScheduleSettings(
            duration_overrides={"FEED": -50.0}))[0].activities["FEED"].duration_days > 0)

def not_before_holds_an_activity():
    lay = demo()
    base = s.build_from_layout(lay, s.ScheduleSettings())[0]
    late = base.activities["FEED"].es + dt.timedelta(days=400)
    got = s.build_from_layout(lay, s.ScheduleSettings(not_before={"FEED": late.isoformat()}))[0]
    assert got.activities["FEED"].es == late, (got.activities["FEED"].es, late)
    return True
S.check("'start no earlier than' holds an activity back", not_before_holds_an_activity)

S.check("a not-before earlier than the network allows changes nothing",
        lambda: s.build_from_layout(demo(), s.ScheduleSettings(
            not_before={"FEED": "2000-01-01"}))[0].activities["FEED"].es
        == s.build_from_layout(demo(), s.ScheduleSettings())[0].activities["FEED"].es)
S.check("an unparseable date is ignored",
        lambda: s.build_from_layout(demo(), s.ScheduleSettings(
            not_before={"FEED": "not a date"}))[0] is not None)

def extra_milestone_appears():
    got = s.build_from_layout(demo(), s.ScheduleSettings(
        extra_milestones=[{"name": "Rig contract signed", "date": "2028-03-01"},
                          {"name": "", "date": "2028-03-01"},          # no name — skipped
                          {"name": "No date", "date": ""}]))[0]        # no date — skipped
    added = [a for a in got.activities.values() if a.name == "Rig contract signed"]
    assert len(added) == 1, [a.name for a in got.activities.values() if a.group == "Milestones"]
    assert added[0].milestone and added[0].es == dt.date(2028, 3, 1), added[0]
    assert not any(a.name == "No date" for a in got.activities.values())
    return True
S.check("a user milestone is added on its date, and incomplete rows are skipped", extra_milestone_appears)

S.check("a user milestone can hang off an existing activity",
        lambda: s.build_from_layout(demo(), s.ScheduleSettings(
            extra_milestones=[{"name": "After FEED", "date": "2027-01-01", "after": "FEED"}]
        ))[0].activities["USER_MS_1"].predecessors == [("FEED", 0)])
S.check("an unknown predecessor is dropped, not an error",
        lambda: s.build_from_layout(demo(), s.ScheduleSettings(
            extra_milestones=[{"name": "Loose", "date": "2029-01-01", "after": "NOPE"}]
        ))[0].activities["USER_MS_1"].predecessors == [])

def edits_round_trip_through_a_project_file():
    import tb_project, tb_cost
    st2 = s.ScheduleSettings(duration_overrides={"FEED": 500.0}, not_before={"DG3": "2029-01-01"},
                             extra_milestones=[{"name": "Rig contract", "date": "2028-03-01", "after": ""}])
    txt = tb_project.project_to_yaml("P", demo(), tb_cost.CostSettings(), st2)
    back = tb_project.project_from_yaml_full(txt)[3]
    assert back.duration_overrides == {"FEED": 500.0}, back.duration_overrides
    assert back.not_before == {"DG3": "2029-01-01"}, back.not_before
    assert back.extra_milestones[0]["name"] == "Rig contract", back.extra_milestones
    return True
S.check("plan edits are saved and reloaded with the project", edits_round_trip_through_a_project_file)

sys.exit(0 if S.report() else 1)
