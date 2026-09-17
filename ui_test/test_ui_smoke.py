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
    def get(self, url, params=None, timeout=None):
        if "/304/" in url:
            return _Resp({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [2.5, 60.6]}, "properties": {"fclName": "HOST X"}}]})
        raise ConnectionError("simulated outage")
req = types.ModuleType("requests"); req.Session = _Sess; sys.modules["requests"] = req
sys.path.insert(0, os.path.join(ROOT, "ui_test"))
from _harness import Suite
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
def empty():
    run(press={"New empty layout (keeps catalog)"})
    assert len(ss.layout.nodes) == 0 and not errs()
S.check("empty layout renders (no host, no wells, no schedule crash)", empty)
sys.exit(0 if S.report() else 1)
