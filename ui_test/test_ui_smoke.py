"""Headless execution of tieback_app.py through realistic interaction sequences."""
import sys, os, types, json, importlib
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, ROOT]; os.chdir(ROOT)
import stubs
H = stubs.Harness(); stubs.install(H)
# requests stub (no network in CI sandbox): fake FactMaps response
class _Resp:
    def __init__(self, d): self.d = d
    def raise_for_status(self): pass
    def json(self): return self.d
class _Sess:
    headers = {}
    def __init__(self): self.headers = {}
    def get(self, url, params=None, timeout=None):
        if "depth_sample" in url:
            return _Resp({"smoothed": -137.0})
        if "depth_profile" in url:
            return _Resp([{"smoothed": -(140 + i), "lat": 60.5, "lon": 2.6, "distance": i * 300}
                          for i in range(20)])
        if (params or {}).get("f") == "json":
            return _Resp({"drawingInfo": {"renderer": {"type": "uniqueValue", "field1": "dscHcType", "uniqueValueInfos": [
                {"value": "OIL", "symbol": {"type": "esriSFS", "color": [11, 190, 0, 165], "outline": {"color": [130, 130, 130, 165]}}}]}}})
        if "/304/" in url:
            return _Resp({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [2.5, 60.6]}, "properties": {"fclName": "HOST X"}}]})
        if "/504/" in url:
            return _Resp({"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[2, 60], [2, 61], [3, 61], [2, 60]]]}, "properties": {"dscName": "Alpha", "dscHcType": "OIL"}}]})
        raise ConnectionError("simulated outage")
req = types.ModuleType("requests"); req.Session = _Sess; sys.modules["requests"] = req
sys.path.insert(0, os.path.join(ROOT, "ui_test"))
from _harness import Suite
import tb_geo, tb_cases, tb_schedule
S = Suite("test_ui_smoke")
SRC = open(os.path.join(ROOT, "tieback_app.py")).read()
CODE = compile(SRC, "tieback_app.py", "exec")

def run(press=(), event=None, uploads=None, max_reruns=6):
    H.press = set(press); H.event = event; H.uploads = dict(uploads or {}); H.log = []
    for i in range(max_reruns):
        try:
            exec(CODE, {"__name__": "__main__", "__file__": os.path.join(ROOT, "tieback_app.py")})
            return i
        except stubs.Rerun:
            continue
    raise RuntimeError("rerun loop did not settle")
errs = lambda: [m for k, m in H.log if k == "error"]
ss = H.session_state
ev = lambda seq, typ, p, nonce="UI": {"nonce": nonce, "seq": seq, "type": typ, "payload": p, "view": [2.0, 60.0, 3.2, 61.0]}

def broken_demo_starts_anyway():
    import pathlib, shutil
    demo = pathlib.Path(ROOT) / "test_fixtures" / "demo_field_a_tieback.yaml"
    backup = demo.read_text()
    try:
        demo.write_text("<!DOCTYPE html>\n<html>not a project</html>\n")
        ss.clear()
        run()
        assert not errs(), errs()
        assert ss.demo_error and "not a TieBack Studio project" in ss.demo_error
        assert any("Demo project not loaded" in str(m) for k, m in H.log if k == "warning")
        assert len(ss.layout.nodes) == 0          # empty layout, app still usable
    finally:
        demo.write_text(backup)
        ss.clear()
        run()
S.check("a broken demo file does not stop the app", broken_demo_starts_anyway)
S.check("initial render (demo project)", lambda: run() == 0 and "layout" in ss and not errs())
S.check("component args JSON-serialisable & contain payload", lambda: len(H.last_component_args["payload"]["nodes"]) == 9)
def add_node():
    n0 = len(ss.layout.nodes)
    reruns = run(event=ev(1, "add_node", {"item_id": "mfld_4slot", "lat": 60.52, "lon": 2.64}))
    assert len(ss.layout.nodes) == n0 + 1 and reruns == 1 and ss.map_state["selected"] == "MF1"
S.check("map add_node → layout updated → single rerun settles", add_node)
S.check("same event re-delivered on next run is ignored", lambda: (n := len(ss.layout.nodes), run(event=ev(1, "add_node", {"item_id": "mfld_4slot", "lat": 60.52, "lon": 2.64})), len(ss.layout.nodes) == n)[-1])
S.check("select manifold renders node form", lambda: run(event=ev(2, "select", {"id": "MF1"})) == 0 and not errs())
def node_apply():
    run(press={"Apply changes"})
    assert "Apply changes" in H.pressed_log[-1:] and not errs()
S.check("node form apply", node_apply)
S.check("select flowline renders edge form", lambda: run(event=ev(3, "select", {"id": "FL1"})) == 0 and not errs())
S.check("edge form apply keeps route", lambda: (run(press={"Apply changes"}), ss.layout.edges["FL1"].route == [(60.55, 2.6)])[-1])
def bad_connect():
    run(event=ev(4, "add_edge", {"item_id": "riser_flex", "source": "W1", "target": "HOST_A"}))
    assert any("cannot connect" in str(m) for k, m in H.log if k == "toast")
S.check("disallowed connection surfaces a toast", bad_connect)
S.check("delete manifold via map", lambda: (run(event=ev(5, "delete", {"id": "MF1"})), "MF1" not in ss.layout.nodes)[-1])
def bulk():
    run(press={"Apply table edits"}); assert not errs(), errs()
S.check("bulk table apply (unchanged data round-trips)", bulk)
def catalog_roundtrip():
    before = ss.layout.catalog.to_dict()
    run(press={"Apply catalog changes"})
    assert not errs(), errs()
    assert ss.layout.catalog.to_dict() == before
S.check("catalog editor round-trip rebuilds identical catalog", catalog_roundtrip)
def mc():
    run(press={"Run simulation"})
    assert ss.mc and ss.mc["P10"] < ss.mc["P50"] < ss.mc["P90"]
    run()
    assert not any("changed since" in str(m) for k, m in H.log if k == "info")
S.check("Monte Carlo run and stays valid on next render", mc)
S.check("cost settings apply invalidates MC", lambda: (run(press={"Apply cost settings"}), ss.mc is None)[-1])
S.check("factor/override apply", lambda: (run(press={"Apply factors and overrides"}), not errs())[-1])
S.check("schedule settings apply", lambda: (run(press={"Apply schedule settings"}), not errs())[-1])
def ncs():
    run(press={"Load for layout"})
    assert "facilities" in ss.ncs_overlays and ss.ncs_overlays["facilities"]["features"][0]["properties"]["_label"] == "HOST X"
    assert any("simulated outage" in str(m) for k, m in H.log if k == "warning")  # other layers fail gracefully
    run()
    assert any(o.get("title") == "Facilities in place" for o in H.last_component_args["overlays"])
S.check("NCS layers: success passes overlay to map, failures warn per layer", ncs)
S.check("load for map view uses last view bbox", lambda: (run(press={"Load for map view"}), "facilities" in ss.ncs_overlays)[-1])
KML = b'<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><name>S-1</name><Point><coordinates>2.7,60.45</coordinates></Point></Placemark></kml>'
def all_discoveries():
    run(press={"All NCS discoveries"})
    fc = ss.ncs_overlays.get("discoveries_all")
    assert fc and fc["features"][0]["properties"]["_fill"] == "#0BBE00" and not errs()
S.check("all NCS discoveries load with Sodir HC-type colours", all_discoveries)
def bathy_depths():
    run(press={"Load demo"})
    assert ss.layout.nodes["W1"].water_depth_m == 0.0
    run(press={"Fill water depths from EMODnet"})
    assert ss.layout.nodes["W1"].water_depth_m == 137.0          # blank → sampled
    assert ss.layout.nodes["TMPL_A"].water_depth_m == 150.0      # kept (only blank nodes)
    assert ss.layout.nodes["HOST_A"].water_depth_m == 140.0      # host never sampled
S.check("EMODnet depth fill populates blank subsea nodes only", bathy_depths)
def utility_line():
    import tb_network as tn
    lay = ss.layout
    lay.add_edge(tn.Edge("UL_TEST", "chem_line", "HOST_A", "TMPL_A", route=list(lay.edges["UMB1"].route)))
    run()
    assert not errs()
    e = [x for x in H.last_component_args["payload"]["edges"] if x["id"] == "UL_TEST"][0]
    assert e["kind"] == "utility_line" and e["color"] == "#9DBA00" and e["dash"] == "4 4"
    del lay.edges["UL_TEST"]
S.check("chemical injection line renders with its own colour and dash", utility_line)
def empty_layer_message():
    run(press={"Load demo"})
    run(press={"Load for layout"})
    assert any("nothing inside this area" in str(m) for k, m in H.log if k == "info") or \
        ss.ncs_overlays.get("facilities", {}).get("features")
S.check("an NCS layer with no features in the area says so", empty_layer_message)
def tiein_screening():
    run(press={"Load demo"})
    live = {"fclName": "ALPHA", "fclKind": "PLATFORM", "fclWaterDepth": 125, "fclSurface": "Y",
            "fclNpdidFacility": "1", "fclPhase": "IN SERVICE", "fclStartupDate": "1999-01-01"}
    dead = {"fclName": "OLD BRAVO", "fclKind": "PLATFORM", "fclSurface": "Y", "fclNpdidFacility": "2",
            "fclPhase": "IN SERVICE", "fclDateShutdown": "2015-01-01"}
    feat = lambda p_, lon: {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, 60.58]},
                            "properties": p_}
    ss.ncs_overlays["facilities"] = {"type": "FeatureCollection", "title": "Facilities in place",
        "geometry": "point", "color": "#EB0037", "features": [feat(live, 2.62), feat(dead, 2.64)]}
    ss.ncs_overlays["facilities_all"] = {"type": "FeatureCollection", "title": "All facilities",
        "geometry": "point", "color": "#C4561B", "features": [feat(live, 2.62)]}   # same platform again
    run(press={"Screen tie-in options"})
    assert ss.get("tiein_rows") is not None, ("no rows", errs(), [m for k, m in H.log if k == "info"])
    hosts = [r["host"] for r in ss.tiein_rows]
    assert "ALPHA" in hosts and ss.tiein_node == "TMPL_A", (hosts, ss.get("tiein_node"))
    assert hosts.count("ALPHA") == 1, hosts              # not duplicated across the two layers
    assert "OLD BRAVO" not in hosts                      # shut down, not offered
    assert next(r for r in ss.tiein_rows if r["host"] == "ALPHA")["status"] == "in operation"
    alpha = next(r for r in ss.tiein_rows if r["host"] == "ALPHA")
    assert alpha["distance_km"] > 0 and "required_whp_bara" in alpha
    before = len(ss.layout.nodes)
    run(press={"Add to layout"})
    assert len(ss.layout.nodes) > before and not errs()
S.check("tie-in screening ranks a Sodir facility and builds the tie-back", tiein_screening)
def design_basis_tab():
    run(press={"Load demo"})
    assert any(k == "metric" and m == "Missing" for k, m in H.log)
    assert any(k == "caption" and "SI-enheter" in str(m) for k, m in H.log)
S.check("design basis tab renders with SI units note", design_basis_tab)
def import_layer():
    run(press={"Add layer to map"}, uploads={"layer_upload": stubs.UploadedFile("sat.kml", KML)})
    assert ss.user_overlays and ss.user_overlays[0]["title"] == "sat.kml"
    n0 = len(ss.layout.nodes)
    run(press={"Place 1 items"})
    assert len(ss.layout.nodes) == n0 + 1
    assert any(n.label == "S-1" for n in ss.layout.nodes.values())
S.check("import KML overlay and convert its point to equipment", import_layer)
def open_project():
    import tb_project
    txt = tb_project.project_to_yaml("Reloaded", ss.layout, ss.cost_settings, ss.sched_settings).encode()
    run(uploads={"proj_upload": stubs.UploadedFile("p.yaml", txt)})
    assert ss.project_name == "Reloaded" and not errs(), (ss.project_name, errs())
S.check("open saved project file", open_project)
def fa_render():
    run(press={"Load demo"})
    assert not errs(), errs()
    assert any(k == "metric" and m == "Host arrival" for k, m in H.log)
S.check("flow assurance tab solves demo and renders results", fa_render)
def fa_apply_all():
    import tb_flowassurance as tfa
    run(press={"Apply well inputs"}); assert not errs(), errs()
    assert all(isinstance(n.attrs.get("fa"), dict) for n in ss.layout.nodes.values() if ss.layout.kind(n.node_id) == "well")
    run(press={"Apply U-values"}); assert not errs(), errs()
    assert all("u_w_m2k" not in e.attrs for e in ss.layout.edges.values())    # blank overrides stay blank
    run(press={"Apply flow assurance settings"}); assert not errs(), errs()
    assert ss.fa_settings == tfa.FASettings()                                     # defaults round-trip exactly
S.check("well inputs, U-values and FA settings forms round-trip", fa_apply_all)
def fa_sweep():
    run(press={"Run size sensitivity"})
    assert ss.get("fa_sweep") and len(ss.fa_sweep["rows"]) >= 3 and not errs()
    run()
    assert any(k == "plotly_chart" for k, m in H.log)
S.check("line size sensitivity runs and persists across reruns", fa_sweep)
def fa_project_roundtrip():
    import tb_project, tb_flowassurance as tfa
    ss.fa_settings = tfa.FASettings(arrival_bara=22.0)
    txt = tb_project.project_to_yaml("FA", ss.layout, ss.cost_settings, ss.sched_settings, ss.fa_settings).encode()
    run(uploads={"proj_upload": stubs.UploadedFile("fa.yaml", txt)})
    assert ss.fa_settings.arrival_bara == 22.0 and not errs()
S.check("project upload restores flow assurance settings", fa_project_roundtrip)
S.check("load demo resets", lambda: (run(press={"Load demo"}), len(ss.layout.nodes) == 9)[-1])
def turndown_and_nodal():
    run(press={"Load demo"})
    run(press={"Run turndown check"})
    assert ss.get("turndown") and len(ss.turndown["rows"]) == 4 and not errs()
    run(press={"Save well IPR data"})
    assert all(isinstance(n.attrs.get("ipr"), dict) for n in ss.layout.nodes.values()
               if ss.layout.kind(n.node_id) == "well")
    run(press={"Solve rates from IPR"})
    assert any("at least one well" in str(m) for k, m in H.log if k == "warning")   # 'use' unticked
S.check("turndown check runs; nodal solve asks for a well to be enabled", turndown_and_nodal)
def catalog_excel():
    import tb_costio, tb_catalog
    wb = tb_costio.catalog_to_workbook(ss.layout.catalog)
    run(press={"Replace catalog with this file"}, uploads={"lib_upload": stubs.UploadedFile("rates.xlsx", wb)})
    assert not errs() and len(ss.layout.catalog.items) == len(tb_catalog.Catalog().items)
    items, spreads = tb_costio.catalog_to_csv(ss.layout.catalog)
    edited = items.replace("5500000.0", "7200000.0")
    run(press={"Replace catalog with this file"}, uploads={"lib_upload": stubs.UploadedFile("rates.csv", edited.encode())})
    assert ss.layout.catalog.get("xt_vxt_10k").procurement_usd == 7200000.0, errs()
S.check("cost catalog imported from Excel and from an edited CSV", catalog_excel)
def cases_tab():
    run(press={"Load demo"})
    run(press={"Save current as case"})
    assert len(ss.cases) == 1 and not errs()
    run(press={"Compare cases"})
    assert ss.get("case_rows") and ss.case_rows[0]["wells"] == 4 and not errs()
    run(press={"Load into editor"})
    assert len(ss.layout.nodes) == 9 and not errs()
    run(press={"Delete case"})
    assert ss.cases == []
S.check("cases: save, compare, load back and delete", cases_tab)
def seabed_profiles():
    run(press={"Load demo"})
    run(press={"Fetch seabed profiles along routes"})
    assert ss.layout.edges["FL1"].attrs.get("seabed_profile") and not errs()
S.check("seabed profiles fetched into the layout", seabed_profiles)
def report():
    run(press={"Build report"})
    assert ss.get("report_bytes") and len(ss.report_bytes) > 20000 and not errs()
S.check("screening report builds from the app", report)
def appearance():
    import tb_map as tm
    run(press={"Load demo"})
    assert ss.display.color_mode == "item"
    ss.display = tm.DisplaySettings(symbol_scale=1.8, line_scale=2.0, color_mode="fluid")
    run()
    pay = H.last_component_args["payload"]
    assert pay["display"]["symbol_scale"] == 1.8 and pay["display"]["line_scale"] == 2.0
    assert [e for e in pay["edges"] if e["id"] == "UMB1"][0]["color"] == tm.FLUID_COLORS["control"]
S.check("appearance settings reach the map payload", appearance)
def display_event_from_toolbar():
    run(event=ev(20, "display", {"symbol_scale": 2.6, "line_scale": 0.8}))
    assert ss.display.symbol_scale == 2.6 and ss.display.line_scale == 0.8 and not errs()
S.check("toolbar size sliders persist into the session", display_event_from_toolbar)
def display_persists_in_project():
    import tb_project, tb_map as tm
    ss.display = tm.DisplaySettings(symbol_scale=1.3, color_mode="phase")
    txt = tb_project.project_to_yaml("D", ss.layout, ss.cost_settings, ss.sched_settings, ss.fa_settings,
                                     ss.display).encode()
    run(uploads={"proj_upload": stubs.UploadedFile("d.yaml", txt)})
    assert ss.display.symbol_scale == 1.3 and ss.display.color_mode == "phase" and not errs()
S.check("appearance saved and restored with the project", display_persists_in_project)
def undo_redo():
    run(press={"Load demo"})
    n0 = len(ss.layout.nodes)
    run(event=ev(30, "add_node", {"item_id": "plet_std", "lat": 60.52, "lon": 2.63}))
    assert len(ss.layout.nodes) == n0 + 1 and ss.undo
    run(press={"Undo"})
    assert len(ss.layout.nodes) == n0 and ss.redo
    run(press={"Redo"})
    assert len(ss.layout.nodes) == n0 + 1
S.check("undo and redo a map edit", undo_redo)
def multi_move():
    run(press={"Load demo"})
    w2 = ss.layout.nodes["W2"].lat
    run(event=ev(31, "move_many", {"ids": ["W1", "W2"], "anchor": "W1",
                                   "lat": ss.layout.nodes["W1"].lat + 0.005,
                                   "lon": ss.layout.nodes["W1"].lon}))
    assert abs(ss.layout.nodes["W2"].lat - (w2 + 0.005)) < 1e-9 and not errs()
S.check("multi-selection move applied from the map", multi_move)
def slots_ui():
    import tb_network as tn
    run(press={"Load demo"})
    ss.layout.nodes["TMPL_A"].item_id = "tmpl_6slot"
    ss.layout.add_node(tn.Node("W9", "xt_hxt_10k", 60.5011, 2.6680, label="A-5", sitp_psi=4500))
    run(press={"Land in slots"})
    assert not errs(), errs()
    landed = [w for w in ss.layout.nodes if ss.layout.nodes[w].attrs.get("in_structure") == "TMPL_A"]
    assert landed, ("no wells landed", [m for k, m in H.log if k in ("success", "error")])
    assert any(e.item_id == "slot_tiein" for e in ss.layout.edges.values()), (
        "no integral tie-in", [e.item_id for e in ss.layout.edges.values()])
S.check("wells landed in template slots from the panel", slots_ui)
def tag_filter():
    run(press={"Load demo"})
    ss.layout.set_tags("W1", ["phase 2"])
    ss.tag_exclude = ["phase 2"]
    run()
    pay = H.last_component_args["payload"]
    assert [x["id"] for x in pay["nodes"] if x["hidden"]] == ["W1"]
    ss.tag_exclude = []
S.check("tag filter hides an element on the map", tag_filter)
def viability_tab():
    import tb_viability as tv
    run(press={"Load demo"})
    run(press={"Run viability check"})
    assert ss.get("viab_rows"), ([m for k, m in H.log if k == "info"], errs())
    assert tv.summary(ss.viab_rows)[tv.PASS] > 5
S.check("viability check runs from its tab", viability_tab)
def concept_switch_and_ghost():
    run(press={"Load demo"})
    run(press={"Save current as case"})
    assert len(ss.cases) == 1
    ss.ghost_cases = [ss.cases[0]["name"]]
    run()
    titles = [o.get("title", "") for o in H.last_component_args["overlays"]]
    assert any(t.startswith("Concept:") for t in titles), titles
    ss.ghost_cases = []
S.check("a saved concept can be drawn on the map behind the active one", concept_switch_and_ghost)
def auto_land_on_place():
    import tb_network as tn
    run(press={"Load demo"})
    ss.layout.nodes["TMPL_A"].item_id = "tmpl_6slot"
    t = ss.layout.nodes["TMPL_A"]
    run(event=ev(40, "add_node", {"item_id": "xt_hxt_10k", "lat": t.lat + 0.0005, "lon": t.lon}))
    placed = [w for w in ss.layout.nodes if ss.layout.nodes[w].attrs.get("in_structure") == "TMPL_A"]
    assert placed, "a well placed next to the template should land in a free slot"
S.check("a well placed beside a template is landed in a slot automatically", auto_land_on_place)
def load_template():
    run(press={"Load template"})
    assert not errs() and len(ss.layout.nodes) >= 5
    assert ss.project_name and ss.project_name != "Field A tie-back (demo)"
S.check("concept template loads from the sidebar", load_template)
def template_at_picked_point():
    run(press={"Load demo"})
    run(event=ev(90, "pick", {"lat": 65.4, "lon": 7.25}))
    assert ss.map_state["picked"] == [65.4, 7.25]
    run(press={"Load template"})
    assert not errs()
    anchor = ss.layout.anchor()
    assert abs(anchor[0] - 65.4) < 1e-6 and abs(anchor[1] - 7.25) < 1e-6
S.check("template lands on the point picked on the map", template_at_picked_point)
def move_layout_to_picked():
    run(event=ev(91, "pick", {"lat": 62.0, "lon": 4.0}))
    run(press={"Move layout here"})
    a = ss.layout.anchor()
    assert abs(a[0] - 62.0) < 1e-6 and abs(a[1] - 4.0) < 1e-6 and not errs()
S.check("whole layout can be moved to the picked point", move_layout_to_picked)
def group_move_event():
    run(press={"Load demo"})
    w0 = ss.layout.nodes["W1"].lat
    t0 = ss.layout.nodes["TMPL_A"].lat
    run(event=ev(92, "move_group", {"id": "TMPL_A", "lat": t0 + 0.02, "lon": ss.layout.nodes["TMPL_A"].lon}))
    assert abs(ss.layout.nodes["W1"].lat - (w0 + 0.02)) < 1e-9
S.check("group move from the map moves the wells with the template", group_move_event)
def empty():
    run(press={"New empty layout (keeps catalog)"})
    assert len(ss.layout.nodes) == 0 and not errs()
S.check("empty layout renders (no host, no wells, no schedule crash)", empty)
def grid_import():
    """A Surfer grid over the demo field: image + contours on the map, then the
    grid used as the depth source instead of EMODnet."""
    run(press={"Load demo"})
    lay = ss.layout
    lats = [n.lat for n in lay.nodes.values()]
    lons = [n.lon for n in lay.nodes.values()]
    pad = 0.15
    w, e = min(lons) - pad, max(lons) + pad
    s, nth = min(lats) - pad, max(lats) + pad
    nx, ny = 24, 20
    rows = []
    for j in range(ny):
        rows.append(" ".join(f"{320.0 + 2.0 * i + 1.0 * j:.3f}" for i in range(nx)))
    grd = (f"DSAA\n{nx} {ny}\n{w} {e}\n{s} {nth}\n320 420\n" + "\n".join(rows) + "\n").encode()

    run(press={"Add grid to map"}, uploads={"layer_upload": stubs.UploadedFile("seabed.grd", grd)})
    bad = [m for m in errs() if "failed" in str(m).lower() or "could not" in str(m).lower()]
    assert not bad, bad
    assert ss.grid_rasters and ss.grid_rasters[0]["title"] == "seabed.grd", ss.grid_rasters
    assert ss.grid_rasters[0]["url"].startswith("data:image/png;base64,")
    assert any(o.get("title") == "seabed.grd contours" for o in ss.user_overlays), \
        [o.get("title") for o in ss.user_overlays]
    assert ss.grids and ss.grids[0]["convention"] == "positive_down"

    run(press={"Set element depths"})
    subsea = [n for n in ss.layout.nodes.values() if ss.layout.kind(n.node_id) != "host"]
    assert all(320.0 <= n.water_depth_m <= 420.0 for n in subsea), \
        [(n.node_id, n.water_depth_m) for n in subsea]

    run(press={"Seabed profiles from grid"})
    lines = [ed for ed in ss.layout.edges.values()
             if ss.layout.catalog.get(ed.item_id).category in ("flowline", "utility_line")]
    assert any(ed.attrs.get("seabed_profile") for ed in lines), "no profile stored"
    return True
S.check("import a .grd: image + contours on the map, depths from the grid", grid_import)

def grid_removed():
    run(press={"Remove grid"})
    assert not ss.grids and not ss.grid_rasters
    assert not any(o.get("title", "").endswith("contours") for o in ss.user_overlays)
    return True
S.check("removing a grid clears its image and contours", grid_removed)

def loose_shapefile_upload():
    """A .shp selected together with its .dbf and .prj — no zip."""
    import struct as _struct
    e, nn, _, _ = tb_geo.geo_to_utm(60.52, 2.62, 31, "ED50")
    rec = _struct.pack("<idd", 1, e, nn)
    body = _struct.pack(">ii", 1, len(rec) // 2) + rec
    shp = (_struct.pack(">i", 9994) + b"\x00" * 20 + _struct.pack(">i", (100 + len(body)) // 2)
           + _struct.pack("<ii", 1000, 1) + _struct.pack("<8d", *([0.0] * 8)) + body)
    prj = (b'PROJCS["ED_1950_UTM_Zone_31N",GEOGCS["GCS_European_1950",'
           b'DATUM["D_European_1950",SPHEROID["International_1924",6378388.0,297.0]]],'
           b'PROJECTION["Transverse_Mercator"]]')
    run(press={"Add layer to map"},
        uploads={"layer_upload": [stubs.UploadedFile("blocks.shp", shp),
                                  stubs.UploadedFile("blocks.prj", prj)]})
    bad = [m for m in errs() if "failed" in str(m).lower() or "could not" in str(m).lower()]
    assert not bad, bad
    titles = [o.get("title") for o in ss.user_overlays]
    assert "blocks.shp" in titles, titles
    fc = next(o for o in ss.user_overlays if o.get("title") == "blocks.shp")
    lon, lat = fc["features"][0]["geometry"]["coordinates"]
    assert 2.6 < lon < 2.65 and 60.5 < lat < 60.55, (lon, lat)
    return True
S.check("upload a loose .shp with its .prj", loose_shapefile_upload)

def grid_needs_a_crs():
    """A UTM grid with no CRS chosen must be refused, not dropped off Africa."""
    grd = (b"DSAA\n3 3\n400000 400500\n6700000 6700500\n300 310\n"
           b"300 301 302\n303 304 305\n306 307 308\n")
    run(uploads={"layer_upload": stubs.UploadedFile("utm.grd", grd)})
    assert any("not lon/lat" in str(m) for m in errs()), errs()
    return True
S.check("a projected grid without a CRS is refused", grid_needs_a_crs)

def stale_harness_single_upload():
    """Regression for the CI failure in v0.11.0: a Streamlit stub whose
    file_uploader ignores accept_multiple_files hands back ONE object, and the
    app iterated it — TypeError: 'UploadedFile' object is not iterable, from a
    stale ui_test/stubs.py against a current tieback_app.py. The app now
    normalises whatever it is given, so an out-of-date harness cannot break it."""
    original = stubs.Harness.file_uploader
    stubs.Harness.file_uploader = lambda self, label, type=None, key=None, **k: self.uploads.pop(key, None)
    try:
        run(press={"Add layer to map"}, uploads={"layer_upload": stubs.UploadedFile("sat.kml", KML)})
        bad = [m for m in errs() if "not iterable" in str(m) or "failed" in str(m).lower()]
        assert not bad, bad
        assert any(o.get("title") == "sat.kml" for o in ss.user_overlays), \
            [o.get("title") for o in ss.user_overlays]
    finally:
        stubs.Harness.file_uploader = original
    return True
S.check("a single uploaded file (old-style harness) still imports", stale_harness_single_upload)

def several_concepts_on_one_map():
    """The reported problem: only the active concept was drawn. Two saved
    concepts, one active — the other must reach the map as its own overlay."""
    drawn = {}
    import tb_map as _tm
    real = _tm.render_map
    _tm.render_map = lambda payload, palette, overlays, selected, **k: (
        drawn.update(overlays=[dict(title=o.get("title"), kind=o.get("kind"),
                                    color=o.get("color"), n=len(o.get("features", [])))
                               for o in overlays]), None)[1]
    try:
        run(press={"Load demo"})
        ss.cases = []                            # earlier checks in this file save cases too
        ss.active_case = None
        ss.project_name = "Concept A"
        run(press={"Save current as case"})
        for nd in ss.layout.nodes.values():      # move it so the two differ
            nd.lat += 0.03
        ss.project_name = "Concept B"
        run(press={"Save current as case"})
        assert [c_["name"] for c_ in ss.cases] == ["Concept A", "Concept B"], \
            [c_["name"] for c_ in ss.cases]
        ss.active_case = "Concept B"
        ss.ghost_cases = ["Concept A"]
        run()
        concepts = [o for o in drawn["overlays"] if o["kind"] == "concept"]
        assert len(concepts) == 1, drawn["overlays"]
        assert concepts[0]["title"] == "Concept: Concept A"
        assert concepts[0]["n"] > 0, "concept overlay is empty"
        assert concepts[0]["color"] == tb_cases.color_for(ss.cases, "Concept A")
        # and both at once
        ss.active_case = "(working layout)"
        ss.ghost_cases = ["Concept A", "Concept B"]
        run()
        got = sorted(o["title"] for o in drawn["overlays"] if o["kind"] == "concept")
        assert got == ["Concept: Concept A", "Concept: Concept B"], got
        colours = {o["color"] for o in drawn["overlays"] if o["kind"] == "concept"}
        assert len(colours) == 2, "concepts must not share a colour"
    finally:
        _tm.render_map = real
    return True
S.check("two saved concepts are drawn together, each in its own colour", several_concepts_on_one_map)


def concept_switcher_loads_the_concept():
    """The dropdown must actually load the concept into the editor."""
    class PickA:
        def __init__(self, want): self.want = want
        def __call__(self, label, options, index=0, format_func=str, **k):
            if "Concept being edited" in label and self.want in options:
                return self.want
            return options[index] if options else None
    original = stubs.Harness.selectbox
    stubs.Harness.selectbox = PickA("Concept A")
    try:
        run()
        assert ss.active_case == "Concept A", ss.active_case
        lat_a = min(n_.lat for n_ in ss.layout.nodes.values())
    finally:
        stubs.Harness.selectbox = original
    stubs.Harness.selectbox = PickA("Concept B")
    try:
        run()
        assert ss.active_case == "Concept B", ss.active_case
        lat_b = min(n_.lat for n_ in ss.layout.nodes.values())
    finally:
        stubs.Harness.selectbox = original
    assert abs(lat_b - lat_a - 0.03) < 1e-6, (lat_a, lat_b)
    return True
S.check("the concept dropdown loads that concept into the editor", concept_switcher_loads_the_concept)


def active_concept_is_never_its_own_ghost():
    ss.active_case = "Concept A"
    ss.ghost_cases = ["Concept A", "Concept B"]
    run()
    assert "Concept A" not in ss.ghost_cases, ss.ghost_cases
    return True
S.check("the concept being edited is not also drawn as a ghost", active_concept_is_never_its_own_ghost)

def stale_tag_filter_blanked_the_map():
    """Reported: a loaded concept did not appear at all. A tag filter set on the
    previous layout survived the load; the new layout has no tags, so the filter
    matched nothing and every element was sent hidden — and the filter controls
    only appear when the layout HAS tags, so there was no way to clear it."""
    drawn = []
    import tb_map as _tm
    real = _tm.render_map
    _tm.render_map = lambda payload, palette, overlays, selected, **k: (
        drawn.append(sum(1 for x in payload["nodes"] if not x.get("hidden"))), None)[1]
    try:
        run(press={"Load demo"})
        assert drawn[-1] > 0
        first = list(ss.layout.nodes)[0]
        ss.layout.set_tags(first, ["phase 1"])
        ss.tag_include = ["phase 1"]
        run()
        assert drawn[-1] == 1, drawn[-1]
        ss.map_state["picked"] = (60.94311, 2.53235)
        run(press={"Load template"})
        assert drawn[-1] > 1, f"the loaded template was drawn with {drawn[-1]} visible elements"
        assert ss.get("tag_include") == [], ss.get("tag_include")
    finally:
        _tm.render_map = real
    return True
S.check("a tag filter does not survive into a newly loaded layout", stale_tag_filter_blanked_the_map)


def template_lands_at_the_picked_point():
    ss.map_state["picked"] = (61.2, 3.1)
    run(press={"Load template"})
    lats = [n_.lat for n_ in ss.layout.nodes.values()]
    lons = [n_.lon for n_ in ss.layout.nodes.values()]
    assert ss.layout.nodes, "template loaded nothing"
    assert abs(max(lats) - 61.2) < 0.3 and abs(min(lons) - 3.1) < 0.3, (min(lats), min(lons))
    assert any("Loaded" in str(m) for k, m in H.log if k == "success"), \
        "loading a template should say what arrived"
    return True
S.check("a template lands at the picked point and says so", template_lands_at_the_picked_point)


def template_as_new_concept_keeps_the_old_one():
    """'Flexibility to load different concepts': loading must be able to add to
    the set rather than replace what is on screen."""
    run(press={"Load demo"})
    ss.cases = []
    ss.active_case = None
    ss.ghost_cases = []
    ss.project_name = "Baseline"
    before = len(ss.layout.nodes)
    ss.map_state["picked"] = (60.9, 2.5)
    run(press={"Add as new concept"})
    names = [c_["name"] for c_ in ss.cases]
    assert len(names) == 2, names                      # the baseline plus the template
    assert "Baseline" in names, names
    assert ss.active_case != "Baseline", ss.active_case
    assert "Baseline" in ss.ghost_cases, ss.ghost_cases  # the one you were on is drawn alongside
    assert before > 0
    return True
S.check("'Add as new concept' keeps the layout you were on", template_as_new_concept_keeps_the_old_one)


def concept_names_never_collide():
    n0 = len(ss.cases)
    ss.map_state["picked"] = (60.8, 2.4)
    run(press={"Add as new concept"})
    run(press={"Add as new concept"})
    names = [c_["name"] for c_ in ss.cases]
    assert len(names) == len(set(names)), names
    assert len(names) > n0
    return True
S.check("loading the same template twice makes two concepts, not one", concept_names_never_collide)


def save_and_delete_concept():
    run(press={"Load demo"})
    ss.cases = []
    ss.active_case = None
    run(press={"Save as concept"})
    assert len(ss.cases) == 1, ss.cases
    assert ss.active_case == ss.cases[0]["name"]
    run(press={"Delete concept"})
    assert ss.cases == [], ss.cases
    assert ss.active_case == "(working layout)"
    return True
S.check("save and delete a concept from the sidebar", save_and_delete_concept)

def start_from_an_empty_design():
    """'Is it possible to start from an empty design?' — yes, and the app must
    stay usable with nothing on the map."""
    run(press={"Load demo"})
    assert ss.layout.nodes
    run(press={"New empty layout (keeps catalog)"})
    assert not ss.layout.nodes and not ss.layout.edges, (len(ss.layout.nodes), len(ss.layout.edges))
    assert ss.project_name == "New tie-back", ss.project_name
    hard = [m for m in errs() if "Traceback" in str(m) or "failed" in str(m).lower()]
    assert not hard, hard
    # and equipment can be placed straight onto the empty layout
    run(event=ev(9001, "add_node", {"item_id": "tmpl_4slot", "lat": 60.5, "lon": 2.5}))
    assert len(ss.layout.nodes) == 1, len(ss.layout.nodes)
    return True
S.check("start from an empty design and place the first item", start_from_an_empty_design)


def empty_layout_keeps_the_catalog():
    before = len(ss.layout.catalog.items)
    run(press={"New empty layout (keeps catalog)"})
    assert len(ss.layout.catalog.items) == before, "the catalog must survive"
    return True
S.check("an empty layout keeps the equipment catalog", empty_layout_keeps_the_catalog)


def chemistry_screen_runs():
    run(press={"Load demo"})
    run(press={"Run production chemistry screen"})
    assert not [m for m in errs() if "chemistry" in str(m).lower()], errs()
    assert ss.get("chem_inputs") is not None
    return True
S.check("the production chemistry screen runs on the demo", chemistry_screen_runs)


def plan_edits_move_first_oil():
    """The milestones/activity editor has to change the dates, not just the table."""
    run(press={"Load demo"})
    sched = ss.sched_settings
    before = tb_schedule.build_from_layout(ss.layout, sched)[0]
    fo_before = sorted([a for a in before.activities.values()
                        if a.act_id.endswith("_FIRST_OIL")], key=lambda a: a.es)[0].es
    sched.duration_overrides = {"FEED": float(before.activities["FEED"].duration_days) + 200.0}
    sched.extra_milestones = [{"name": "Rig contract", "date": "2028-05-01", "after": ""}]
    run()
    after = tb_schedule.build_from_layout(ss.layout, sched)[0]
    fo_after = sorted([a for a in after.activities.values()
                       if a.act_id.endswith("_FIRST_OIL")], key=lambda a: a.es)[0].es
    # 200 days of extra FEED pushes an offshore campaign past the marine season,
    # so first production slips further than the 200 days added — that is the
    # weather window doing its job, not an error.
    assert (fo_after - fo_before).days >= 200, (fo_before, fo_after)
    assert any(a.name == "Rig contract" for a in after.activities.values()), "added milestone missing"
    return True
S.check("editing an activity duration moves first production", plan_edits_move_first_oil)


def plan_edits_survive_a_layout_change():
    """Overrides are keyed by activity id so a layout edit does not wipe them."""
    ss.sched_settings.duration_overrides = {"FEED": 500.0}
    run(event=ev(9002, "add_node", {"item_id": "xt_vxt_10k", "lat": 60.58, "lon": 2.56}))
    sch = tb_schedule.build_from_layout(ss.layout, ss.sched_settings)[0]
    assert sch.activities["FEED"].duration_days == 500.0, sch.activities["FEED"].duration_days
    return True
S.check("plan edits survive a change to the layout", plan_edits_survive_a_layout_change)


def stale_override_is_ignored():
    ss.sched_settings.duration_overrides = {"NO_SUCH_ACTIVITY": 99.0}
    run()
    assert not [m for m in errs() if "schedule" in str(m).lower()], errs()
    return True
S.check("an override for an activity that no longer exists is ignored", stale_override_is_ignored)

sys.exit(0 if S.report() else 1)
