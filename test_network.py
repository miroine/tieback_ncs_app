import sys, copy, yaml
import tb_catalog as c, tb_network as n, tb_geo as g
from _harness import Suite
S = Suite("test_network")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(cat=None): return n.Layout.from_dict(copy.deepcopy(FIX), cat or c.Catalog())
def codes(lay, sev=None): return [f.code for f in lay.validate() if sev is None or f.severity == sev]

S.check("demo has no errors or warnings", lambda: codes(demo()) == [])
def fl_len():
    lay = demo(); e = lay.edges["FL1"]
    v = [(60.5020, 2.6660), (60.55, 2.60), (60.5975, 2.5035)]
    ref = (g.geodesic_distance(*v[0], *v[1]) + g.geodesic_distance(*v[1], *v[2])) * 1.03 + 50
    assert abs(lay.edge_length(e) - ref) < 1e-6
S.check("flowline length = geodesic route ×1.03 + 50 m", fl_len)
S.check("explicit length override used", lambda: demo().edge_length(demo().edges["RISER1"]) == 350)
S.check("jumper length is 0 (unit item)", lambda: demo().edge_length(demo().edges["J_T"]) == 0)
def slots():
    lay = demo()
    lay.add_node(n.Node("W5", "xt_hxt_10k", 60.501, 2.668, sitp_psi=4500))
    lay.add_edge(n.Edge("J_W5", "jumper_rigid", "W5", "TMPL_A"))
    assert "SLOTS_EXCEEDED" in codes(lay, "error")
S.check("5 wells on 4-slot template -> SLOTS_EXCEEDED", slots)
def rating():
    lay = demo(); lay.edges["FL1"].item_id = "fl_flex"; lay.nodes["W1"].sitp_psi = 9000
    errs = [f for f in lay.validate() if f.code == "RATING"]
    assert any(f.element_id == "FL1" for f in errs)
S.check("9000 psi SITP on 7500 psi flexible -> RATING error on FL1", rating)
def hipps():
    lay = demo(); lay.edges["FL1"].item_id = "fl_flex"; lay.nodes["W1"].sitp_psi = 9000
    lay.nodes["PLET_T"].hipps = True
    errs = [f.element_id for f in lay.validate() if f.code == "RATING"]
    assert "FL1" not in errs
    assert "J_T" not in errs  # J_T rated 10k anyway
S.check("HIPPS upstream of flexible clears its RATING error", hipps)
def hipps_node_itself():
    lay = demo(); lay.nodes["W1"].sitp_psi = 12000
    lay.nodes["PLET_T"].hipps = True
    ids = [f.element_id for f in lay.validate() if f.code == "RATING"]
    assert "PLET_T" in ids and "FL1" not in ids and "TMPL_A" in ids and "W1" in ids
S.check("HIPPS node itself and upstream still see full SITP", hipps_node_itself)
def nohost():
    lay = demo(); lay.remove_node("HOST_A")
    cs = codes(lay, "error"); assert "NO_HOST" in cs
S.check("remove host -> NO_HOST", nohost)
def remove_cascades():
    lay = demo(); lay.remove_node("HOST_A")
    assert "RISER1" not in lay.edges and "UMB1" not in lay.edges
S.check("remove_node drops connected edges", remove_cascades)
def nopath():
    lay = demo(); del lay.edges["FL1"]
    assert codes(lay, "error").count("NO_PRODUCTION_PATH") == 4
S.check("cut flowline -> 4 wells without production path", nopath)
def diam():
    lay = demo(); lay.edges["FL1"].diameter_in = 0
    assert "DIAMETER_MISSING" in codes(lay, "error")
    lay.edges["FL1"].diameter_in = 30
    assert "DIAMETER_RANGE" in codes(lay, "error")
S.check("diameter missing/out of range", diam)
def endpoints():
    lay = demo(); lay.add_edge(n.Edge("BAD", "riser_flex", "W1", "HOST_A", diameter_in=8))
    assert "EDGE_ENDPOINTS" in codes(lay, "error")
S.check("riser from well to host -> EDGE_ENDPOINTS", endpoints)
def nocontrol():
    lay = demo(); del lay.edges["UMB1"]
    assert codes(lay, "warning").count("NO_CONTROL") == 4
S.check("no umbilical -> NO_CONTROL warnings", nocontrol)
def nopower():
    lay = demo()
    lay.add_node(n.Node("MPP", "mpp_2x", 60.55, 2.6))
    lay.add_edge(n.Edge("J_MPP", "jumper_rigid", "MPP", "PLET_T"))
    assert "NO_POWER" in codes(lay, "warning")
    lay.add_edge(n.Edge("PC", "pwr_cable", "HOST_A", "MPP"))
    assert "NO_POWER" not in codes(lay, "warning")
S.check("boosting needs power path", nopower)
def isolated():
    lay = demo(); lay.add_node(n.Node("LONE", "ssiv_std", 60.52, 2.6))
    assert "ISOLATED" in codes(lay, "warning")
S.check("isolated node warning", isolated)
def direct_fl():
    lay = demo(); lay.add_node(n.Node("SAT", "xt_vxt_10k", 60.55, 2.55, sitp_psi=3000))
    lay.add_edge(n.Edge("FL_SAT", "fl_flex", "SAT", "PLET_H", diameter_in=6))
    assert "DIRECT_WELL_FLOWLINE" in codes(lay, "info")
S.check("direct XT flowline -> info", direct_fl)
S.raises("duplicate id raises", ValueError, lambda: demo().add_node(n.Node("W1", "xt_hxt_10k", 60, 2)))
S.raises("edge to unknown node raises", ValueError, lambda: demo().add_edge(n.Edge("E", "jumper_rigid", "W1", "ZZ")))
S.raises("self-loop raises", ValueError, lambda: demo().add_edge(n.Edge("E", "jumper_rigid", "W1", "W1")))
S.raises("bad coordinates raise", ValueError, lambda: demo().add_node(n.Node("Q", "plet_std", 95, 2)))
def path():
    nodes, edges = demo().path_to_host("W1")
    assert nodes == ["W1", "TMPL_A", "PLET_T", "PLET_H", "RB1", "HOST_A"]
    assert edges == ["J_W1", "J_T", "FL1", "J_RB", "RISER1"]
S.check("production path from W1 correct", path)
def move():
    lay = demo(); L0 = lay.edge_length(lay.edges["FL1"])
    lay.move_node("PLET_T", 60.45, 2.70); assert lay.edge_length(lay.edges["FL1"]) > L0
S.check("moving node updates route length", move)
def yrt():
    lay = demo(); lay2 = n.Layout.from_dict(yaml.safe_load(lay.to_yaml()), lay.catalog)
    assert lay.quantities() == lay2.quantities()
S.check("layout YAML round-trip preserves quantities", yrt)
S.check("quantity rows = nodes + edges", lambda: len(demo().quantities()) == 9 + 9)
def smoothing():
    lay = demo()
    L0 = lay.edge_length(lay.edges["FL1"])
    lay.edges["FL1"].attrs["smooth"] = True
    assert len(lay.edge_shape(lay.edges["FL1"])) == 2 * lay.settings.smooth_samples + 1
    assert lay.edge_length(lay.edges["FL1"]) > L0        # curve is slightly longer than the corner
    assert lay.edge_length(lay.edges["FL1"]) / L0 < 1.05
S.check("smoothed route resamples the corridor and lengthens the line slightly", smoothing)
S.check("smoothing a 2-point line is a no-op",
        lambda: (lambda l: (l.edges["J_T"].attrs.__setitem__("smooth", True),
                            len(l.edge_shape(l.edges["J_T"])) == 2)[-1])(demo()))
def bend_radius():
    lay = demo()
    lay.edges["FL1"].route = [(60.55, 2.60), (60.5505, 2.5985)]    # near-hairpin over ~150 m
    assert "BEND_RADIUS" in codes(lay, "warning")
    lay.settings.min_bend_radius_m = 50.0
    assert "BEND_RADIUS" not in codes(lay, "warning")
S.check("tight route bend warns against the minimum lay radius", bend_radius)
S.check("gentle route bend passes", lambda: "BEND_RADIUS" not in codes(demo(), "warning"))
def lift():
    lay = demo()
    lay.catalog.override("tmpl_4slot", install_spread="csv")      # 400 te structure on a 250 te CSV
    fs = [f for f in lay.validate() if f.code == "LIFT_CAPACITY"]
    assert fs and fs[0].element_id == "TMPL_A" and "250" in fs[0].message
S.check("structure heavier than its vessel's hook is flagged", lift)
S.check("heavy-lift spread carries the template fine", lambda: "LIFT_CAPACITY" not in codes(demo(), "warning"))
S.check("spread with no stated capacity is not checked",
        lambda: (lambda l: (l.catalog.spreads["hlv"].__setattr__("lift_capacity_te", 0),
                            l.catalog.override("tmpl_4slot", weight_te=99999),
                            "LIFT_CAPACITY" not in codes(l, "warning"))[-1])(demo()))
def templates():
    import pathlib
    files = sorted(pathlib.Path("templates").glob("*.yaml"))
    assert len(files) >= 5
    for f in files:
        lay = n.Layout.from_dict(yaml.safe_load(f.read_text()), c.Catalog())
        errs = [x for x in lay.validate() if x.severity == "error"]
        assert not errs, (f.name, [x.message for x in errs])
        assert lay.nodes and lay.edges and any(lay.kind(x) == "host" for x in lay.nodes)
S.check("every shipped concept template loads and passes the design checks", templates)
def piggyback_follows_carrier():
    lay = demo()
    lay.add_edge(n.Edge("CHEM1", "chem_line", "PLET_T", "PLET_H"))
    straight = lay.edge_length(lay.edges["CHEM1"])
    lay.edges["CHEM1"].attrs["piggyback_on"] = "FL1"
    assert lay.edge_shape(lay.edges["CHEM1"]) == lay.edge_shape(lay.edges["FL1"])
    assert lay.edge_length(lay.edges["CHEM1"]) > straight and codes(lay, "error") == []
S.check("strapped line follows the carrier corridor", piggyback_follows_carrier)
def piggyback_reversed():
    lay = demo()
    lay.add_edge(n.Edge("CHEM1", "chem_line", "PLET_H", "PLET_T", attrs={"piggyback_on": "FL1"}))
    assert lay.edge_shape(lay.edges["CHEM1"]) == lay.edge_shape(lay.edges["FL1"])[::-1]
S.check("strapped line run the other way follows the corridor reversed", piggyback_reversed)
def piggyback_own_route_wins():
    lay = demo()
    lay.add_edge(n.Edge("CHEM1", "chem_line", "PLET_T", "PLET_H", route=[(60.52, 2.63)],
                        attrs={"piggyback_on": "FL1"}))
    assert lay.edge_shape(lay.edges["CHEM1"]) != lay.edge_shape(lay.edges["FL1"])
S.check("an explicit route overrides the carrier corridor", piggyback_own_route_wins)
def piggyback_checks():
    lay = demo()
    lay.add_edge(n.Edge("CHEM1", "chem_line", "PLET_T", "PLET_H", attrs={"piggyback_on": "NOPE"}))
    assert "PIGGYBACK_MISSING" in codes(lay, "error")
    lay.edges["CHEM1"].attrs["piggyback_on"] = "CHEM1"
    assert "PIGGYBACK_SELF" in codes(lay, "error")
    lay.edges["CHEM1"].attrs["piggyback_on"] = "UMB1"
    assert "PIGGYBACK_CARRIER" in codes(lay, "warning")
    lay.edges["CHEM1"].attrs["piggyback_on"] = "FL1"
    lay.add_edge(n.Edge("FIB1", "fibre_cable", "PLET_T", "PLET_H", attrs={"piggyback_on": "CHEM1"}))
    assert "PIGGYBACK_CHAIN" in codes(lay, "error")
S.check("piggyback checks: missing, self, wrong carrier, chained", piggyback_checks)
def anchor_and_place():
    lay = demo()
    assert lay.anchor() == (60.6, 2.5)                       # the host
    L0 = lay.edge_length(lay.edges["FL1"])
    lay.place_at(71.5, 22.0)
    assert abs(lay.nodes["HOST_A"].lat - 71.5) < 1e-9 and abs(lay.nodes["HOST_A"].lon - 22.0) < 1e-9
    assert abs(lay.edge_length(lay.edges["FL1"]) / L0 - 1) < 0.005      # distances preserved
    assert lay.edges["FL1"].route and abs(lay.edges["FL1"].route[0][0] - 71.5) < 0.1
    assert not [f for f in lay.validate() if f.severity == "error"]
S.check("place_at moves a concept to a new location keeping its dimensions", anchor_and_place)
def plain_translate():
    lay = demo(); lay.translate(0.5, -0.25)
    assert abs(lay.nodes["W1"].lat - (60.5012 + 0.5)) < 1e-9 and abs(lay.edges["FL1"].route[0][1] - (2.60 - 0.25)) < 1e-9
S.check("translate shifts nodes and route vertices together", plain_translate)
S.check("anchor falls back to the centroid without a host/structure",
        lambda: (lambda l: (l.add_node(n.Node("P", "plet_std", 60.0, 3.0)),
                            l.add_node(n.Node("Q", "plet_std", 61.0, 4.0)),
                            l.anchor() == (60.5, 3.5))[-1])(n.Layout(c.Catalog())))
S.check("place_at on an empty layout is a no-op", lambda: n.Layout(c.Catalog()).place_at(60, 3) is None)
S.raises("place_at out of range raises", ValueError, lambda: demo().place_at(95.0, 3.0))
def jumper_group():
    lay = demo()
    assert lay.jumper_group("TMPL_A") == ["PLET_T", "W1", "W2", "W3", "W4"]
    assert lay.jumper_group("W1") == ["TMPL_A"] and lay.jumper_group("HOST_A") == []
S.check("jumper group lists what sits on a structure", jumper_group)
sys.exit(0 if S.report() else 1)
