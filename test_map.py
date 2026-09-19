import sys, copy, yaml, json
import tb_catalog as c, tb_network as n, tb_map as m, tb_project as p, tb_cost as k, tb_schedule as s
from _harness import Suite
S = Suite("test_map")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
def ev(seq, typ, payload, nonce="N1", view=None): return {"nonce": nonce, "seq": seq, "type": typ, "payload": payload, "view": view}

def payload():
    lay = demo(); lay.edges["FL1"].diameter_in = 0
    pl = m.build_payload(lay)
    fl = [e for e in pl["edges"] if e["id"] == "FL1"][0]
    assert fl["severity"] == "error" and fl["route"] == [[60.55, 2.6]] and fl["length_m"] > 14000
    assert fl["source"] == "PLET_T" and fl["target"] == "PLET_H"
    json.dumps(pl)
S.check("payload JSON-serialisable with severity, route, length", payload)
def palette():
    pal = m.build_palette(c.Catalog())
    kinds_e = {i["kind"] for i in pal["edge_items"]}
    assert kinds_e == set(c.EDGE_KINDS) == {"flowline", "umbilical", "jumper", "riser", "power_cable", "utility_line"}
    assert all(i["kind"] not in kinds_e for i in pal["node_items"]) and len(pal["node_items"]) >= 15
S.check("palette splits node / edge items (all catalog categories covered)", palette)
S.check("payload carries symbol, footprint, heading and line style",
        lambda: (lambda pl: [x for x in pl["nodes"] if x["id"] == "TMPL_A"][0]["symbol"] == "template"
                 and [x for x in pl["nodes"] if x["id"] == "TMPL_A"][0]["footprint"] == [32, 22]
                 and [e for e in pl["edges"] if e["id"] == "UMB1"][0]["dash"] == "8 6")(m.build_payload(demo())))
S.check("rev deterministic and content-sensitive",
        lambda: m.content_rev({"a": 1}) == m.content_rev({"a": 1}) != m.content_rev({"a": 2}))
S.check("severity_map keeps worst", lambda: m.severity_map([n.Finding("info", "x", "", "A"), n.Finding("error", "y", "", "A"),
                                                            n.Finding("warning", "z", "", "A")]) == {"A": "error"})
S.check("next_id fills gaps", lambda: m.next_id("W", ["W1", "W2", "W4"]) == "W3")

def workflow():
    lay, st = demo(), {}
    r = m.apply_event(lay, ev(1, "add_node", {"item_id": "xt_vxt_10k", "lat": 60.55, "lon": 2.55}), st)
    assert r["changed"] and r["selected"] == "W5" and lay.nodes["W5"].lat == 60.55
    r = m.apply_event(lay, ev(2, "add_node", {"item_id": "plet_std", "lat": 60.56, "lon": 2.55}), st)
    assert r["selected"] == "PLET1"
    r = m.apply_event(lay, ev(3, "add_edge", {"item_id": "jumper_rigid", "source": "W5", "target": "PLET1"}), st)
    assert r["changed"] and r["selected"] == "J1" and lay.edges["J1"].diameter_in == 0
    r = m.apply_event(lay, ev(4, "add_edge", {"item_id": "fl_flex", "source": "PLET1", "target": "PLET_H"}), st)
    assert lay.edges[r["selected"]].diameter_in == 10.0
    fid = r["selected"]
    L0 = lay.edge_length(lay.edges[fid])
    r = m.apply_event(lay, ev(5, "set_route", {"id": fid, "route": [[60.65, 2.4]]}), st)
    assert lay.edge_length(lay.edges[fid]) > L0
    r = m.apply_event(lay, ev(6, "move_node", {"id": "W5", "lat": 60.555, "lon": 2.551}), st)
    assert lay.nodes["W5"].lon == 2.551
    r = m.apply_event(lay, ev(7, "delete", {"id": "W5"}, view=[2, 60, 3, 61]), st)
    assert "W5" not in lay.nodes and "J1" not in lay.edges and r["selected"] is None and st["view"] == [2, 60, 3, 61]
S.check("add → connect → route → move → delete workflow", workflow)
def dedupe():
    lay, st = demo(), {}
    e = ev(1, "add_node", {"item_id": "plet_std", "lat": 60.5, "lon": 2.5})
    assert m.apply_event(lay, e, st)["changed"]
    again = m.apply_event(lay, e, st)
    assert not again["applied"] and sum(1 for x in lay.nodes if x.startswith("PLET")) == 3
S.check("re-delivered event (Streamlit rerun) applied once", dedupe)
def new_nonce():
    lay, st = demo(), {}
    m.apply_event(lay, ev(5, "select", {"id": "W1"}), st)
    r = m.apply_event(lay, ev(1, "select", {"id": "W2"}, nonce="N2"), st)
    assert r["applied"] and r["selected"] == "W2"
S.check("new iframe nonce restarts sequence", new_nonce)
def older():
    lay, st = demo(), {}
    m.apply_event(lay, ev(5, "select", {"id": "W1"}), st)
    assert not m.apply_event(lay, ev(4, "select", {"id": "W2"}), st)["applied"]
S.check("out-of-order older seq ignored", older)
def rules():
    lay, st = demo(), {}
    r = m.apply_event(lay, ev(1, "add_edge", {"item_id": "riser_flex", "source": "W1", "target": "HOST_A", "diameter_in": 8}), st)
    assert r["error"] and not r["changed"] and len(lay.edges) == 9
S.check("disallowed connection returns error, no change", rules)
def bad_ids():
    lay, st = demo(), {}
    assert m.apply_event(lay, ev(1, "move_node", {"id": "ZZ", "lat": 1, "lon": 1}), st)["error"]
    assert m.apply_event(lay, ev(2, "delete", {"id": "ZZ"}), st)["error"]
    assert m.apply_event(lay, ev(3, "set_route", {"id": "FL1", "route": [[95, 2]]}), st)["error"]
    assert m.apply_event(lay, ev(4, "nonsense", {}), st)["error"]
    assert m.apply_event(lay, ev(5, "add_node", {"item_id": "nope", "lat": 1, "lon": 1}), st)["error"]
    assert m.apply_event(lay, ev(6, "select", {"id": "ZZ"}), st)["selected"] is None
S.check("unknown ids / bad input produce errors, never exceptions", bad_ids)
S.check("None event is a no-op", lambda: not m.apply_event(demo(), None, {})["applied"])
def route_resets_override():
    lay, st = demo(), {}
    m.apply_event(lay, ev(1, "set_route", {"id": "RISER1", "route": [[60.599, 2.501]]}), st)
    assert lay.edges["RISER1"].length_m is None
S.check("editing route clears explicit length override", route_resets_override)
S.check("component files present", lambda: (m.COMPONENT_DIR / "index.html").exists() and (m.COMPONENT_DIR / "core.js").exists())

def ed50_display():
    import tb_geo
    lay, st = demo(), {}
    lay.settings.datum = "ED50"
    pl = m.build_payload(lay)
    w1 = [x for x in pl["nodes"] if x["id"] == "W1"][0]
    la, lo = tb_geo.transform_datum(lay.nodes["W1"].lat, lay.nodes["W1"].lon, "ED50", "WGS84")
    assert abs(w1["lat"] - la) < 1e-12 and abs(w1["lon"] - lo) < 1e-12
    assert tb_geo.geodesic_distance(w1["lat"], w1["lon"], lay.nodes["W1"].lat, lay.nodes["W1"].lon) > 50
    # a drag that lands exactly where it was displayed must not move the stored ED50 point
    m.apply_event(lay, ev(1, "move_node", {"id": "W1", "lat": w1["lat"], "lon": w1["lon"]}), st)
    assert abs(lay.nodes["W1"].lat - 60.5012) < 1e-8 and abs(lay.nodes["W1"].lon - 2.6676) < 1e-8
    fl = [x for x in pl["edges"] if x["id"] == "FL1"][0]
    m.apply_event(lay, ev(2, "set_route", {"id": "FL1", "route": fl["route"]}), st)
    assert abs(lay.edges["FL1"].route[0][0] - 60.55) < 1e-8
S.check("ED50 layout shown in WGS84 on map; map edits converted back (no drift)", ed50_display)
def wgs_identity():
    pl = m.build_payload(demo()); w1 = [x for x in pl["nodes"] if x["id"] == "W1"][0]
    assert (w1["lat"], w1["lon"]) == (60.5012, 2.6676)
S.check("WGS84 layout displayed unchanged", wgs_identity)

def payload_shape():
    lay = demo(); lay.edges["FL1"].attrs["smooth"] = True
    e = [x for x in m.build_payload(lay)["edges"] if x["id"] == "FL1"][0]
    assert e["smooth"] and len(e["shape"]) == 21 and len(e["route"]) == 1
    assert abs(e["shape"][0][0] - 60.5020) < 1e-9 and abs(e["shape"][0][1] - 2.6660) < 1e-9   # starts at the node
S.check("payload carries the as-laid smoothed shape alongside the editable bends", payload_shape)

def move_group():
    lay, st = demo(), {}
    w1 = (lay.nodes["W1"].lat, lay.nodes["W1"].lon)
    t0 = (lay.nodes["TMPL_A"].lat, lay.nodes["TMPL_A"].lon)
    r = m.apply_event(lay, ev(1, "move_group", {"id": "TMPL_A", "lat": t0[0] + 0.01, "lon": t0[1] - 0.02}), st)
    assert r["changed"] and "5 attached" in r["message"]
    assert abs(lay.nodes["W1"].lat - (w1[0] + 0.01)) < 1e-9 and abs(lay.nodes["W1"].lon - (w1[1] - 0.02)) < 1e-9
    assert abs(lay.nodes["HOST_A"].lat - 60.6) < 1e-12          # not jumpered to the template
S.check("move_group carries the jumpered wells with the structure", move_group)
S.check("move_group on an unknown node errors", lambda: m.apply_event(demo(), ev(1, "move_group",
        {"id": "ZZ", "lat": 60, "lon": 2}), {})["error"])
def pick_event():
    lay, st = demo(), {}
    r = m.apply_event(lay, ev(1, "pick", {"lat": 61.25, "lon": 3.4}), st)
    assert st["picked"] == [61.25, 3.4] and not r["changed"] and "61.25" in r["message"]
S.check("pick stores the placement point without changing the layout", pick_event)
def pick_ed50():
    lay, st = demo(), {}
    lay.settings.datum = "ED50"
    m.apply_event(lay, ev(1, "pick", {"lat": 61.25, "lon": 3.4}), st)
    import tb_geo
    assert abs(st["picked"][0] - tb_geo.transform_datum(61.25, 3.4, "WGS84", "ED50")[0]) < 1e-12
S.check("picked point converted into the layout datum", pick_ed50)

def move_many_event():
    lay, st = demo(), {}
    w2 = lay.nodes["W2"].lat
    r = m.apply_event(lay, ev(1, "move_many", {"ids": ["W1", "W2"], "anchor": "W1",
                                               "lat": lay.nodes["W1"].lat + 0.01,
                                               "lon": lay.nodes["W1"].lon}), st)
    assert r["changed"] and "2 items" in r["message"] and abs(lay.nodes["W2"].lat - (w2 + 0.01)) < 1e-9
S.check("move_many shifts the whole selection by the anchor's offset", move_many_event)
S.check("move_many with an unknown anchor errors",
        lambda: m.apply_event(demo(), ev(1, "move_many", {"ids": ["W1"], "anchor": "ZZ", "lat": 60, "lon": 2}), {})["error"])
def payload_tags_and_hidden():
    lay = demo()
    lay.set_tags("W1", ["phase 2"])
    pl = m.build_payload(lay, None, None, set(lay.by_tags(exclude=["phase 2"])))
    w1 = [x for x in pl["nodes"] if x["id"] == "W1"][0]
    assert w1["hidden"] and w1["tags"] == ["phase 2"]
    assert [e["id"] for e in pl["edges"] if e["hidden"]] == ["J_W1"]     # its jumper goes too
    assert not [x for x in pl["nodes"] if x["id"] == "TMPL_A"][0]["hidden"]
S.check("tag filter marks elements hidden, including lines to a hidden node", payload_tags_and_hidden)
S.check("no filter means nothing hidden",
        lambda: not any(x["hidden"] for x in m.build_payload(demo())["nodes"]))

# ── appearance: scale, colour mode, fluid ──
def fluid_defaults():
    lay = demo()
    assert m.fluid_of(lay, lay.edges["FL1"]) == "multiphase"
    assert m.fluid_of(lay, lay.edges["UMB1"]) == "control"
    lay.add_edge(n.Edge("GL1", "gaslift_line", "HOST_A", "TMPL_A", diameter_in=6))
    lay.add_edge(n.Edge("WI1", "winj_line", "HOST_A", "PLET_T", diameter_in=8))
    assert m.fluid_of(lay, lay.edges["GL1"]) == "gas lift" and m.fluid_of(lay, lay.edges["WI1"]) == "water injection"
S.check("fluid defaults come from the item, then the category", fluid_defaults)
def fluid_override():
    lay = demo(); lay.edges["FL1"].attrs["fluid"] = "gas"
    assert m.fluid_of(lay, lay.edges["FL1"]) == "gas"
    lay.edges["FL1"].attrs["fluid"] = "nonsense"
    assert m.fluid_of(lay, lay.edges["FL1"]) == "multiphase"     # unknown value falls back
S.check("stated fluid overrides the default; nonsense is ignored", fluid_override)
def colour_modes():
    lay = demo(); lay.edges["FL1"].attrs["fluid"] = "gas"; lay.edges["UMB1"].phase = 2
    by = lambda d: {e["id"]: e["color"] for e in m.build_payload(lay, None, d)["edges"]}
    assert by(m.DisplaySettings(color_mode="fluid"))["FL1"] == m.FLUID_COLORS["gas"]
    assert by(m.DisplaySettings(color_mode="item"))["FL1"] == lay.catalog.get("fl_rigid_cs").line_color
    ph = by(m.DisplaySettings(color_mode="phase"))
    assert ph["UMB1"] == m.PHASE_COLORS[1] and ph["FL1"] == m.PHASE_COLORS[0]
    lay.edges["FL1"].diameter_in = 0                              # provoke an error finding
    assert by(m.DisplaySettings(color_mode="checks"))["FL1"] == "#EB0037"
S.check("colour modes: fluid, equipment type, phase and checks", colour_modes)
def custom_colour():
    d = m.DisplaySettings(color_mode="fluid", fluid_colors={**m.FLUID_COLORS, "multiphase": "#123456"})
    assert [e for e in m.build_payload(demo(), None, d)["edges"] if e["id"] == "FL1"][0]["color"] == "#123456"
S.check("custom fluid colour is used", custom_colour)
S.check("display block travels in the payload",
        lambda: m.build_payload(demo(), None, m.DisplaySettings(symbol_scale=2.5, line_scale=0.5,
                                                                thickness_by_diameter=False))["display"]
        == {"symbol_scale": 2.5, "line_scale": 0.5, "thickness_by_diameter": False, "color_mode": "item"})
S.raises("bad colour mode raises", ValueError, lambda: m.DisplaySettings(color_mode="rainbow"))
S.raises("scale outside 0.2–4 raises", ValueError, lambda: m.DisplaySettings(symbol_scale=9.0))
def display_event():
    d = m.DisplaySettings()
    assert m.apply_display_event(d, {"type": "display", "payload": {"symbol_scale": 1.7, "line_scale": 2.2}})
    assert d.symbol_scale == 1.7 and d.line_scale == 2.2
    assert not m.apply_display_event(d, {"type": "display", "payload": {"symbol_scale": 1.7}})   # no change
    m.apply_display_event(d, {"type": "display", "payload": {"symbol_scale": 99}})
    assert d.symbol_scale == 4.0                                  # clamped, not rejected
    assert not m.apply_display_event(d, {"type": "select", "payload": {}})
S.check("toolbar size event updates and clamps the display settings", display_event)

# project bundle
def project_rt():
    lay = demo(); lay.catalog.override("tmpl_4slot", procurement_usd=13e6)
    cs = k.CostSettings(contingency_frac=0.2, element_factor={"FL1": 1.1})
    ss = s.ScheduleSettings(rigs=2, phase_offset_days={2: 180}, marine_window=s.Window((5, 1), (9, 30)))
    txt = p.project_to_yaml("Field A", lay, cs, ss)
    name, lay2, cs2, ss2 = p.project_from_yaml(txt)
    assert name == "Field A" and lay2.catalog.get("tmpl_4slot").procurement_usd == 13e6
    assert lay2.quantities() == lay.quantities() and cs2 == cs
    assert ss2.rigs == 2 and ss2.phase_offset_days == {2: 180.0} and ss2.marine_window.start_mmdd == (5, 1)
    assert k.estimate(lay2, cs2)["total_usd"] == k.estimate(lay, cs)["total_usd"]
S.check("project bundle round-trip (catalog override, settings, estimate identical)", project_rt)
S.check("bare layout YAML loads as project", lambda: p.project_from_yaml(open("test_fixtures/demo_field_a_tieback.yaml").read())[1].nodes["W1"].sitp_psi == 4500)
S.raises("wrong schema raises", ValueError, lambda: p.project_from_yaml("schema: other\n"))
def tolerant_loader():
    txt = open("test_fixtures/demo_field_a_tieback.yaml").read()
    no_schema = "\n".join(l for l in txt.splitlines() if not l.startswith("schema:"))
    assert len(p.project_from_yaml_full(no_schema)[1].nodes) == 9      # recognised by its shape
    assert len(p.project_from_yaml_full("\ufeff" + txt)[1].nodes) == 9  # byte-order mark
    assert len(p.project_from_yaml_full(txt.replace("\n", "\r\n"))[1].nodes) == 9   # Windows line endings
S.check("layout file still loads without its schema line, with a BOM or CRLF", tolerant_loader)
def helpful_errors():
    for text, fragment in (("<!DOCTYPE html>", "starts with"),
                           ("version https://git-lfs.github.com/spec/v1\noid sha256:ab\n", "git-lfs"),
                           ("", "file is empty"),
                           ("a: [1,\n", "not valid YAML"),
                           ("schema: other\nfoo: 1\n", "top-level keys")):
        try:
            p.project_from_yaml_full(text)
            raise AssertionError(f"should have failed: {text[:20]}")
        except ValueError as exc:
            assert fragment in str(exc), (text[:20], str(exc))
S.check("unreadable files explain what was found instead", helpful_errors)
def project_without_schema_line():
    lay = demo()
    txt = p.project_to_yaml("X", lay, k.CostSettings(), s.ScheduleSettings())
    stripped = "\n".join(l for l in txt.splitlines() if not l.startswith("schema:"))
    name, lay2, _, _, _, _ = p.project_from_yaml_full(stripped)
    assert lay2.quantities() == lay.quantities()
S.check("project file recognised by its shape when the schema line is gone", project_without_schema_line)
sys.exit(0 if S.report() else 1)
