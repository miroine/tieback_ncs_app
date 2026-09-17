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
sys.exit(0 if S.report() else 1)
