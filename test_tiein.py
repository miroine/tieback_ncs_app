import sys, copy, math, yaml
import tb_catalog as c, tb_network as n, tb_tiein as ti, tb_flowassurance as fa, tb_geo as g
from _harness import Suite
S = Suite("test_tiein")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
FC = {"features": [
    {"geometry": {"type": "Point", "coordinates": [2.62, 60.58]},
     "properties": {"fclName": "ALPHA", "fclKind": "PLATFORM", "fclWaterDepth": 125, "fclSurface": "Y",
                    "fclCurrentOperatorName": "Operator A"}},
    {"geometry": {"type": "Point", "coordinates": [2.30, 60.40]},
     "properties": {"fclName": "BRAVO", "fclKind": "FPSO", "fclWaterDepth": 300, "fclSurface": "Y"}},
    {"geometry": {"type": "Point", "coordinates": [2.66, 60.52]},
     "properties": {"fclName": "SUBSEA X", "fclKind": "SUBSEA STRUCTURE", "fclSurface": "N"}},
    {"geometry": {"type": "LineString", "coordinates": [[2, 60], [3, 61]]}, "properties": {"fclName": "PIPE"}}]}

def props(**kw):
    base = dict(fclName="X", fclKind="PLATFORM", fclSurface="Y", fclPhase="IN SERVICE",
                fclStartupDate="1999-01-01")
    base.update(kw)
    return base
def fcof(*plist, lat=60.5):
    return {"features": [{"geometry": {"type": "Point", "coordinates": [2.6 + i * 0.02, lat]},
                          "properties": p} for i, p in enumerate(plist)]}

def activity_classification():
    assert ti.facility_activity(props())[0] == "in operation"
    assert ti.facility_activity(props(fclDateRemoved="2020-01-01"))[:2] == ("not in operation", "removed")
    assert ti.facility_activity(props(fclDateShutdown="2018-06-01"))[:2] == ("not in operation", "shut down")
    assert ti.facility_activity(props(fclPhase="SHUT DOWN", fclStartupDate=None))[0] == "not in operation"
    assert ti.facility_activity(props(fclPhase="ABANDONED IN PLACE", fclStartupDate=None))[0] == "not in operation"
    assert ti.facility_activity(props(fclPhase="FUTURE", fclStartupDate=None))[0] == "planned"
    assert ti.facility_activity({})[0] == "unknown"
S.check("facility activity read from removal/shutdown dates and phase wording", activity_classification)
def removed_and_planned_dropped():
    fc = fcof(props(fclName="LIVE", fclNpdidFacility="1"),
              props(fclName="SHUTDOWN", fclNpdidFacility="2", fclDateShutdown="2018-06-01"),
              props(fclName="REMOVED", fclNpdidFacility="3", fclDateRemoved="2020-01-01"),
              props(fclName="PLANNED", fclNpdidFacility="4", fclPhase="FUTURE", fclStartupDate=None))
    assert [c.name for c in ti.candidates_from_overlay(fc)] == ["LIVE"]
    assert len(ti.candidates_from_overlay(fc, active_only=False)) == 4
S.check("decommissioned and planned facilities are not offered as hosts", removed_and_planned_dropped)
def mobile_units_dropped():
    fc = fcof(props(fclName="RIG", fclKind="JACK-UP", fclFixedOrMoveable="MOVEABLE", fclNpdidFacility="9"),
              props(fclName="FIXED", fclNpdidFacility="10"))
    assert [c.name for c in ti.candidates_from_overlay(fc)] == ["FIXED"]
    assert len(ti.candidates_from_overlay(fc, fixed_only=False)) == 2
S.check("mobile units are not tie-in hosts", mobile_units_dropped)
def dedupe_by_npdid():
    fc = fcof(props(fclName="ALPHA", fclNpdidFacility="1", fclWaterDepth=0),
              props(fclName="ALPHA", fclNpdidFacility="1", fclWaterDepth=120))
    got = ti.candidates_from_overlay(fc)
    assert len(got) == 1 and got[0].npdid == "1"
S.check("the same NPDID appears once", dedupe_by_npdid)
def dedupe_across_layers():
    a = ti.candidates_from_overlay(fcof(props(fclName="ALPHA", fclNpdidFacility="1", fclWaterDepth=120)))
    b = ti.candidates_from_overlay(fcof(props(fclName="ALPHA", fclNpdidFacility="1")))
    merged = ti.dedupe(a + b)
    assert len(merged) == 1 and merged[0].water_depth_m == 120     # richer record wins
S.check("a facility loaded from two layers is merged, keeping the fuller record", dedupe_across_layers)
def dedupe_without_npdid():
    c1 = ti.Candidate("BRAVO", 60.5, 2.6)
    c2 = ti.Candidate("bravo ", 60.5001, 2.6001)                   # same place, sloppy name
    c3 = ti.Candidate("BRAVO", 60.9, 2.6)                          # different place, same name
    got = ti.dedupe([c1, c2, c3])
    assert len(got) == 2
S.check("records without an NPDID merge on name and position, not name alone", dedupe_without_npdid)
def screening_dedupes_and_reports_status():
    cands = [ti.Candidate("ALPHA", 60.58, 2.62, npdid="1", activity="in operation"),
             ti.Candidate("ALPHA", 60.58, 2.62, npdid="1", activity="in operation")]
    rows = ti.screen(demo(), "TMPL_A", cands)
    assert len(rows) == 1 and rows[0]["status"] == "in operation"
S.check("screening output is one row per host, with its status", screening_dedupes_and_reports_status)
S.check("layout hosts are always live and unique",
        lambda: [(c.name, c.activity) for c in ti.candidates_from_layout(demo())] == [("Host A", "in operation")])

def read_candidates():
    cands = ti.candidates_from_overlay(FC, active_only=False)
    assert [x.name for x in cands] == ["ALPHA", "BRAVO"]          # subsurface and non-points dropped
    assert cands[0].water_depth_m == 125 and cands[0].operator == "Operator A"
    assert ti.candidates_from_overlay(FC, surface_only=False, active_only=False)[2].name == "SUBSEA X"
S.check("Sodir facility features become tie-in candidates", read_candidates)
S.check("candidate list can be filtered by kind",
        lambda: [x.name for x in ti.candidates_from_overlay(FC, kinds=["FPSO"], active_only=False)] == ["BRAVO"])
S.check("hosts already in the layout are candidates too",
        lambda: [x.name for x in ti.candidates_from_layout(demo())] == ["Host A"])
def distance_check():
    lay = demo()
    d, az = ti.distance_and_bearing(lay, "TMPL_A", ti.Candidate("X", 60.58, 2.62))
    ref = g.geodesic_distance(lay.nodes["TMPL_A"].lat, lay.nodes["TMPL_A"].lon, 60.58, 2.62)
    assert abs(d - ref) < 1e-6 and 340 < az < 350                  # roughly north-north-west
S.check("distance and bearing match a direct geodesic calculation", distance_check)
def ed50_distance():
    lay = demo(); lay.settings.datum = "ED50"
    d_wgs = ti.distance_and_bearing(demo(), "TMPL_A", ti.Candidate("X", 60.58, 2.62))[0]
    d_ed = ti.distance_and_bearing(lay, "TMPL_A", ti.Candidate("X", 60.58, 2.62))[0]
    # the ED50 node converts ~100 m, mostly along the line, so the distance moves by tens of metres
    assert 5 < abs(d_ed - d_wgs) < 150
S.check("candidate distance accounts for the layout datum", ed50_distance)
ROWS = ti.screen(demo(), "TMPL_A", ti.candidates_from_overlay(FC, active_only=False))
def screening():
    assert [r["host"] for r in ROWS] == ["ALPHA", "BRAVO"]         # sorted by distance
    a, b = ROWS
    assert a["distance_km"] < b["distance_km"] and a["line_length_km"] > a["distance_km"]
    assert b["tieback_capex_musd"] > a["tieback_capex_musd"]        # further is dearer
    assert b["required_whp_bara"] > a["required_whp_bara"]          # and needs more pressure
    assert b["arrival_t_c"] < a["arrival_t_c"]                      # and arrives colder
S.check("screening ranks by distance with cost, pressure and temperature", screening)
S.check("range limit drops distant hosts",
        lambda: [r["host"] for r in ti.screen(demo(), "TMPL_A", ti.candidates_from_overlay(FC, active_only=False),
                                              ti.TieInSettings(max_distance_km=15))] == ["ALPHA"])
def bigger_line():
    cands = ti.candidates_from_overlay(FC, active_only=False)
    small = ti.screen(demo(), "TMPL_A", cands, ti.TieInSettings(diameter_in=6))
    big = ti.screen(demo(), "TMPL_A", cands, ti.TieInSettings(diameter_in=14))
    assert big[0]["required_whp_bara"] < small[0]["required_whp_bara"]
    assert big[0]["tieback_capex_musd"] > small[0]["tieback_capex_musd"]
S.check("a bigger line lowers the pressure needed and raises the cost", bigger_line)
def trial_carries_wells():
    lay, d = ti.trial_layout(demo(), "TMPL_A", ti.Candidate("X", 60.58, 2.62, water_depth_m=125),
                             ti.TieInSettings())
    assert sum(1 for x in lay.nodes if lay.kind(x) == "well") == 4
    assert not [f for f in lay.validate() if f.severity == "error"]
    assert abs(lay.edge_length(lay.edges["TIEIN_FL"]) - (d * 1.05 + 50)) < 1.0
S.check("trial tie-back carries the wells and is a valid layout", trial_carries_wells)
def attach():
    lay = demo()
    before = len(lay.nodes) + len(lay.edges)
    added = ti.attach_to_layout(lay, "TMPL_A", ti.Candidate("GAMMA", 60.58, 2.62, water_depth_m=125))
    assert len(added) == 9 and len(lay.nodes) + len(lay.edges) == before + 9
    assert not [f for f in lay.validate() if f.severity == "error"]
    path = lay.path_to_host("W1")
    assert path and lay.kind(path[0][-1]) == "host"
S.check("adding the tie-back to the layout gives every well a path to the new host", attach)
def attach_twice():
    lay = demo()
    ti.attach_to_layout(lay, "TMPL_A", ti.Candidate("G1", 60.58, 2.62))
    ti.attach_to_layout(lay, "TMPL_A", ti.Candidate("G2", 60.40, 2.30))
    assert len([x for x in lay.nodes if lay.kind(x) == "host"]) == 3     # demo host plus two
S.check("a second tie-back does not clash with the first", attach_twice)
def bad_candidate_noted():
    rows = ti.screen(demo(), "TMPL_A", [ti.Candidate("BAD", 60.58, 2.62)],
                     ti.TieInSettings(flowline_item="nope"))
    assert rows and "note" in rows[0] and "distance_km" in rows[0]
S.check("a candidate that fails to build is reported, not raised", bad_candidate_noted)
S.check("empty candidate list gives no rows", lambda: ti.screen(demo(), "TMPL_A", []) == [])
sys.exit(0 if S.report() else 1)
