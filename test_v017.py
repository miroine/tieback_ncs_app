"""v0.17: regions, layers round a point, host-aware templates, concept tie-ins, new equipment."""
import sys, copy, math, glob, yaml
import tb_mapextras as mx, tb_network as n, tb_catalog as c, tb_tiein as ti, tb_cases, tb_cost
import tb_schedule, tb_flowassurance as fa, tb_geo as g, tb_map as m
from _harness import Suite
S = Suite("test_v017")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())

# ── regions ──
S.check("the three seas and the whole shelf are defined",
        lambda: set(mx.REGIONS) == {"Whole NCS", "North Sea", "Norwegian Sea", "Barents Sea"})
S.check("regions are (west, south, east, north) boxes",
        lambda: all(w < e and s_ < n_ for w, s_, e, n_ in mx.REGIONS.values()))
S.check("known fields fall in the right sea",
        lambda: mx.region_of(60.5, 2.8) == "North Sea"          # Oseberg area
        and mx.region_of(65.3, 7.3) == "Norwegian Sea"          # Heidrun / Åsgard area
        and mx.region_of(71.3, 21.4) == "Barents Sea"           # Snøhvit / Goliat area
        and mx.region_of(40.0, 0.0) == "")
S.raises("an unknown region is refused", KeyError, lambda: mx.region_view("Atlantis"))

# ── where to load layers ──
S.check("the picked point wins over the selection",
        lambda: mx.layer_centre("point", (71.5, 22.0), (60, 2)) == (71.5, 22.0))
S.check("no picked point falls back to the selected item",
        lambda: mx.layer_centre("point", None, (60, 2)) == (60, 2))
S.check("nothing picked or selected gives no centre — never the map middle by accident",
        lambda: mx.layer_centre("point", None, None, [(60, 2)], [0, 50, 10, 70]) is None)
S.check("layout centre is the mean position",
        lambda: mx.layer_centre("layout", layout_points=[(60, 2), (62, 4)]) == (61.0, 3.0))
S.check("view centre is the middle of the view",
        lambda: mx.layer_centre("view", view=[2, 60, 4, 62]) == (61.0, 3.0))
def radius_box():
    w, s_, e, n_ = mx.bbox_around_point(71.5, 22.0, 40)
    assert abs(g.geodesic_distance(71.5, 22.0, n_, 22.0) - 40000) < 300
    assert abs(g.geodesic_distance(71.5, 22.0, 71.5, e) - 40000) < 400, "east-west must widen at 71°N"
    return True
S.check("the box round a point is the radius on the ground, at high latitude too", radius_box)
S.raises("a zero radius is refused", ValueError, lambda: mx.bbox_around_point(60, 2, 0))

# ── templates placed with a real host ──
def split_every_template():
    for f in sorted(glob.glob("templates/*.yaml")):
        lay = n.Layout.from_dict(yaml.safe_load(open(f)), c.Catalog())
        r = lay.place_split(61.0, 3.0, 61.3, 2.6, host_label="ALPHA", host_depth_m=120)
        h = lay.nodes[r["host"]]
        assert (round(h.lat, 9), round(h.lon, 9)) == (61.3, 2.6) and h.label == "ALPHA", f
        a = lay.anchor(prefer=("template", "manifold", "well"))
        assert abs(a[0] - 61.0) < 1e-9 and abs(a[1] - 3.0) < 1e-9, f
        assert r["stretched"], f"{f}: some line must join field and host"
        for eid in r["stretched"]:
            assert lay.edges[eid].route == [], f
        assert not [x for x in lay.validate() if x.severity == "error"], f
        rb = [x for x in lay.host_side(r["host"]) if lay.kind(x) == "riser_base"]
        assert all(lay.nodes[x].water_depth_m == 120 for x in rb), f
    return True
S.check("every template splits: field at the point, host at the chosen host", split_every_template)
def split_keeps_field_shape():
    lay = n.Layout.from_dict(yaml.safe_load(open("templates/satellite_4well.yaml")), c.Catalog())
    wells = [x for x in lay.nodes if lay.kind(x) == "well"]
    before = [g.geodesic_distance(lay.nodes[wells[0]].lat, lay.nodes[wells[0]].lon,
                                  lay.nodes[w].lat, lay.nodes[w].lon) for w in wells]
    lay.place_split(64.0, 7.0, 64.4, 6.0)
    after = [g.geodesic_distance(lay.nodes[wells[0]].lat, lay.nodes[wells[0]].lon,
                                 lay.nodes[w].lat, lay.nodes[w].lon) for w in wells]
    return all(abs(a - b) < 0.5 for a, b in zip(before, after))
S.check("the field keeps its shape when moved to another latitude", split_keeps_field_shape)
def split_without_host():
    lay = n.Layout.from_dict(yaml.safe_load(open("templates/satellite_4well.yaml")), c.Catalog())
    h0 = [x for x in lay.nodes if lay.kind(x) == "host"][0]
    a0 = lay.anchor(prefer=("template",))
    d0 = g.geodesic_distance(*a0, lay.nodes[h0].lat, lay.nodes[h0].lon)
    lay.place_split(62.0, 4.0)
    a1 = lay.anchor(prefer=("template",))
    d1 = g.geodesic_distance(*a1, lay.nodes[h0].lat, lay.nodes[h0].lon)
    return abs(a1[0] - 62.0) < 1e-9 and abs(d1 - d0) < 5
S.check("without a host the template keeps its own tie-back distance", split_without_host)
S.check("an empty layout splits to nothing", lambda: n.Layout(c.Catalog()).place_split(60, 2)["moved"] == 0)

# ── saved concepts as tie-in points ──
CASE = tb_cases.snapshot("Field A", demo(), tb_cost.CostSettings(), tb_schedule.ScheduleSettings())
def concept_candidates():
    cs = ti.candidates_from_cases([CASE])
    kinds = {x.name: x.subsea for x in cs}
    assert kinds.get("Host A (Field A)") is False and kinds.get("Template A (Field A)") is True, kinds
    assert all(x.source == "concept: Field A" for x in cs)
    assert not ti.candidates_from_cases([CASE], exclude="Field A"), "a concept is not offered to itself"
    assert all(not x.subsea for x in ti.candidates_from_cases([CASE], subsea=False))
    assert not ti.candidates_from_cases([{"name": "broken", "project": {"x": 1}}]), "bad case offers nothing"
    return True
S.check("a saved concept offers its host and its templates as tie-in points", concept_candidates)
def satellite():
    lay = n.Layout(c.Catalog())
    lay.add_node(n.Node("T2", "tmpl_4slot", 60.57, 2.72, water_depth_m=130))
    lay.add_node(n.Node("W9", "xt_hxt_10k", 60.5701, 2.7201, water_depth_m=130))
    lay.add_edge(n.Edge("J9", "jumper_rigid", "W9", "T2"))
    return lay
def subsea_screen():
    lay = satellite()
    cs = ti.candidates_from_cases([CASE])
    rows = {r["host"]: r for r in ti.screen(lay, "T2", cs, ti.TieInSettings(), fa.FASettings(),
                                            tb_cost.CostSettings(), fa.well_inputs(lay))}
    sub, host = rows["Template A (Field A)"], rows["Host A (Field A)"]
    assert "shares Field A" in sub["note"]
    # a subsea tie-in pays for its new line only, not the other concept's template, riser and host
    assert sub["tieback_capex_musd"] < host["tieback_capex_musd"] + 1e-9 or sub["line_length_km"] > host["line_length_km"]
    assert math.isfinite(sub["required_whp_bara"]), "the combined system must solve"
    return True
S.check("a subsea tie-in into a saved concept is screened with the shared line", subsea_screen)
def subsea_capex_is_new_work_only():
    lay = satellite()
    cand = [x for x in ti.candidates_from_cases([CASE]) if x.subsea][0]
    trial, _ = ti.trial_layout(lay, "T2", cand, ti.TieInSettings())
    new = ti._new_cost_layout(trial, "X_" + cand.target_node)
    assert not [x for x in new.nodes if x.startswith("X_") and x != "X_" + cand.target_node]
    assert new.nodes["X_" + cand.target_node].item_id == "plet_std"
    assert "TIEIN_FL" in new.edges and not [e for e in new.edges if e.startswith("X_")]
    return True
S.check("the subsea tie-in capex counts only the new equipment", subsea_capex_is_new_work_only)
def attach_subsea():
    lay = satellite()
    cand = [x for x in ti.candidates_from_cases([CASE]) if x.subsea][0]
    added = ti.attach_to_layout(lay, "T2", cand)
    assert added and not [f for f in lay.validate() if f.severity == "error"], lay.validate()
    assert lay.path_to_host("W9"), "the satellite well must reach the copied host"
    shared = ti.shared_ids(lay)
    assert shared and all(lay.nodes.get(i, lay.edges.get(i)).attrs["from_concept"] == "Field A" for i in shared)
    n0 = len(lay.nodes)
    ti.attach_to_layout(lay, "T2", cand)
    assert len(lay.nodes) == n0 + 2, "tying in again reuses the copied path (only new PLETs)"
    return True
S.check("attaching a subsea tie-in copies the concept's path to its host once", attach_subsea)
def near_hosts():
    cs = ti.candidates_from_cases([CASE])
    rows = ti.near(cs, 60.45, 2.35, 100)
    assert rows and all(not r[0].subsea for r in rows) and rows == sorted(rows, key=lambda r: r[1])
    return not ti.near(cs, 71.0, 22.0, 100)
S.check("hosts near a point are listed nearest first, subsea points left out", near_hosts)

# ── new equipment ──
NEW = ("plet_valved", "plet_pig", "wye_pig", "hot_tap", "hipps_mod", "pump_1ph", "pump_mp1", "winj_pump",
       "wgc_mod", "sep_gl", "sep_inline", "sdu", "uta", "sub_power", "chem_store", "fl_tcp", "jumper_flex")
S.check("the new items are in the default catalogue", lambda: all(k in c.Catalog().items for k in NEW))
S.check("a control unit takes an umbilical and power but not a flowline",
        lambda: c.edge_allowed("umbilical", "control", "host") and c.edge_allowed("power_cable", "control", "boosting")
        and c.edge_allowed("utility_line", "control", "template") and not c.edge_allowed("flowline", "control", "template")
        and not c.edge_allowed("jumper", "control", "well"))
def top_up():
    cat = c.Catalog()
    cat.items["plet_std"].procurement_usd = 1.0
    for k in NEW:
        cat.items.pop(k)
    assert set(cat.missing_defaults()) == set(NEW)
    assert set(cat.add_missing_defaults()) == set(NEW) and not cat.missing_defaults()
    return cat.items["plet_std"].procurement_usd == 1.0
S.check("an older catalogue is topped up without touching its own rates", top_up)
def new_items_cost_and_schedule():
    lay = demo()
    lay.add_node(n.Node("SDU1", "sdu", 60.51, 2.66))
    lay.add_node(n.Node("SPD1", "sub_power", 60.52, 2.66))
    lay.add_node(n.Node("BP1", "pump_1ph", 60.53, 2.66))
    lay.add_edge(n.Edge("UMB9", "umb_static", "HOST_A", "SDU1"))
    lay.add_edge(n.Edge("PC9", "pwr_cable", "HOST_A", "SPD1"))
    lay.add_edge(n.Edge("PC10", "pwr_cable", "SPD1", "BP1"))
    est = tb_cost.estimate(lay)
    ids = {ln["element_id"] for ln in est["lines"]}
    assert {"SDU1", "SPD1", "BP1"} <= ids
    s_, em = tb_schedule.build_from_layout(lay)
    assert s_.activities
    nopower = [f.element_id for f in lay.validate() if f.code == "NO_POWER"]
    assert "BP1" not in nopower and "SPD1" not in nopower, nopower
    return True
S.check("new equipment costs, schedules and is powered through a distribution unit", new_items_cost_and_schedule)
def hipps_flag():
    lay = demo()
    st = {}
    m.apply_event(lay, {"nonce": "a", "seq": 1, "type": "add_node",
                        "payload": {"item_id": "hipps_mod", "lat": 60.5, "lon": 2.6}}, st)
    h = [x for x in lay.nodes.values() if x.item_id == "hipps_mod"][0]
    return h.hipps and h.node_id.startswith("SSIV")
S.check("a HIPPS module placed on the map has its HIPPS flag set", hipps_flag)
S.check("control units get their own map id prefix", lambda: m.ID_PREFIX["control"] == "SCU")

sys.exit(0 if S.report() else 1)
