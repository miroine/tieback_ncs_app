import sys, copy, math, json, yaml
import tb_mapextras as mx, tb_share as sh, tb_network as n, tb_catalog as c, tb_map as m
import tb_project, tb_cost, tb_schedule, tb_flowassurance as fa, tb_cases
from _harness import Suite
S = Suite("test_mapextras")
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())

# ── geometry agrees with core.js ────────────────────────────────────────────
S.check("destination is exact", lambda: abs(mx.haversine_m((60, 2), mx.destination(60, 2, 45, 2500)) - 2500) < 1e-6)
S.check("circle area is pi r² on the ground",
        lambda: abs(mx.ring_area_m2(mx.circle_ring((60, 2), 1000, 360)) - math.pi * 1e6) / (math.pi * 1e6) < 1e-3)
S.check("1° square at the equator matches the spherical value (~12 364 km²)",
        lambda: abs(mx.ring_area_m2([(0, 0), (0, 1), (1, 1), (1, 0)]) / 1e6 - 12364) < 5)
S.check("path length sums its legs",
        lambda: abs(mx.path_length_m([(60, 2), (60, 2.1), (60.1, 2.1)])
                    - mx.haversine_m((60, 2), (60, 2.1)) - mx.haversine_m((60, 2.1), (60.1, 2.1))) < 1e-9)

# ── sketches ────────────────────────────────────────────────────────────────
S.raises("unknown sketch kind refused", ValueError, lambda: mx.make_sketch("star"))
S.raises("circle without a centre refused", ValueError, lambda: mx.make_sketch("circle", radius_m=500))
S.raises("circle with no radius refused", ValueError, lambda: mx.make_sketch("circle", center=(60, 2), radius_m=0))
S.raises("polygon needs three points", ValueError, lambda: mx.make_sketch("polygon", coords=[(60, 2), (60, 3)]))
S.raises("line needs two points", ValueError, lambda: mx.make_sketch("line", coords=[(60, 2)]))
S.check("out-of-range points are dropped before the count",
        lambda: len(mx.make_sketch("line", coords=[(60, 2), (95, 2), (60.1, 2.1)])["coords"]) == 2)
S.check("each new sketch gets the next colour",
        lambda: mx.make_sketch("line", coords=[(60, 2), (61, 2)], existing=1)["color"] == mx.SKETCH_COLORS[1])


def measures():
    circ = mx.make_sketch("circle", center=(60, 2), radius_m=500)
    poly = mx.make_sketch("polygon", coords=[(60, 2), (60, 2.1), (60.1, 2.1)])
    line = mx.make_sketch("line", coords=[(60, 2), (60, 2.1)])
    assert abs(mx.measure(circ)["area_m2"] - math.pi * 250000) / (math.pi * 250000) < 1e-3
    assert mx.measure(poly)["area_m2"] > 0 and mx.measure(poly)["perimeter_m"] > 0
    assert abs(mx.measure(line)["length_m"] - mx.haversine_m((60, 2), (60, 2.1))) < 1e-9
    assert "500 m" in mx.describe(circ) and "km²" in mx.describe(poly) or "ha" in mx.describe(poly)
    assert "km" in mx.describe(line)
    return True
S.check("circle, polygon and line are measured", measures)


def crud():
    lay = demo()
    sk = mx.add_sketch(lay, mx.make_sketch("circle", center=(60.5, 2.6), radius_m=500, label="zone"))
    assert mx.sketches(lay) and mx.sketches(lay)[0]["label"] == "zone"
    mx.update_sketch(lay, sk["id"], label="500 m safety zone", radius_m=750, color="#123456")
    s2 = mx.sketches(lay)[0]
    assert s2["label"] == "500 m safety zone" and s2["radius_m"] == 750 and s2["color"] == "#123456"
    mx.update_sketch(lay, sk["id"], color="not a colour")
    assert mx.sketches(lay)[0]["color"] == "#123456", "a bad colour must be ignored"
    assert mx.remove_sketch(lay, sk["id"]) and not mx.sketches(lay)
    assert not mx.remove_sketch(lay, "nope")
    return True
S.check("add, edit and remove a sketch", crud)
S.raises("editing an unknown sketch refused", KeyError, lambda: mx.update_sketch(demo(), "SKnope", label="x"))
S.raises("a zero radius edit is refused", ValueError,
         lambda: (lambda lay: mx.update_sketch(lay, mx.add_sketch(lay, mx.make_sketch(
             "circle", center=(60, 2), radius_m=5))["id"], radius_m=0))(demo()))


def safety_zone_contents():
    lay = demo()
    tmpl = lay.nodes["TMPL_A"]
    lat, lon = m.to_display(lay, tmpl.lat, tmpl.lon)
    zone = mx.make_sketch("circle", center=(lat, lon), radius_m=500)
    inside = mx.items_inside(lay, zone, lambda a, b: m.to_display(lay, a, b))
    assert "TMPL_A" in inside and "W1" in inside, inside
    assert "HOST_A" not in inside, "the host is kilometres away"
    return True
S.check("a circle round the template finds what sits inside it", safety_zone_contents)
S.check("a polygon finds what sits inside it",
        lambda: "TMPL_A" in mx.items_inside(demo(), mx.make_sketch("polygon", coords=[
            (60.0, 2.0), (60.0, 3.0), (61.0, 3.0), (61.0, 2.0)])) and
        "TMPL_A" not in mx.items_inside(demo(), mx.make_sketch("polygon", coords=[
            (70.0, 20.0), (70.0, 21.0), (71.0, 21.0)])))
S.check("circle_around names itself",
        lambda: mx.circle_around(demo().nodes["TMPL_A"], 500)["label"].startswith("500 m round"))


def geojson_export():
    lay = demo()
    mx.add_sketch(lay, mx.make_sketch("circle", center=(60.5, 2.6), radius_m=500))
    mx.add_sketch(lay, mx.make_sketch("line", coords=[(60.5, 2.6), (60.6, 2.7)]))
    fc = mx.sketches_geojson(lay)
    kinds = [f["geometry"]["type"] for f in fc["features"]]
    assert kinds == ["Polygon", "LineString"], kinds
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 73, "a closed 72-gon"
    assert 2 < ring[0][0] < 3 and 60 < ring[0][1] < 61, "GeoJSON is lon, lat"
    return True
S.check("sketches export as GeoJSON in lon/lat order", geojson_export)


def sketch_persistence():
    lay = demo()
    mx.add_sketch(lay, mx.make_sketch("polygon", coords=[(60, 2), (60, 2.1), (60.1, 2.1)], label="exclusion"))
    txt = tb_project.project_to_yaml("P", lay, tb_cost.CostSettings(), tb_schedule.ScheduleSettings())
    back = tb_project.project_from_yaml_full(txt)[1]
    assert mx.sketches(back)[0]["label"] == "exclusion"
    return True
S.check("sketches are saved with the project", sketch_persistence)
S.check("an older project with no sketches still loads",
        lambda: n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog()).annotations == [])


def sketches_in_payload():
    lay = demo()
    mx.add_sketch(lay, mx.make_sketch("circle", center=(60.5, 2.6), radius_m=500))
    a = m.build_payload(lay)["annotations"]
    return len(a) == 1 and "circle r = 500 m" in a[0]["measure"]
S.check("sketches reach the map with their measurement", sketches_in_payload)


def sketch_events():
    lay = demo()
    st_ = {}
    r = m.apply_event(lay, {"nonce": "N", "seq": 1, "type": "add_annotation",
                            "payload": {"kind": "polygon", "coords": [[60, 2], [60, 2.1], [60.1, 2.1]]}}, st_)
    assert r["changed"] and len(mx.sketches(lay)) == 1, r
    sid = mx.sketches(lay)[0]["id"]
    r = m.apply_event(lay, {"nonce": "N", "seq": 2, "type": "delete_annotation", "payload": {"id": sid}}, st_)
    assert r["changed"] and not mx.sketches(lay), r
    r = m.apply_event(lay, {"nonce": "N", "seq": 3, "type": "add_annotation",
                            "payload": {"kind": "polygon", "coords": [[60, 2]]}}, st_)
    assert r["error"], "a one-point polygon must be refused, not stored"
    return True
S.check("the map's sketch events add and remove sketches", sketch_events)

# ── bookmarks and the base map ─────────────────────────────────────────────
S.check("a bookmark keeps its view and base map",
        lambda: (lambda b: b["view"] == [1.0, 59.0, 3.0, 61.0] and b["basemap"] == "Sjøkart (Kartverket)"
                 and b["name"] == "View 1")(mx.make_bookmark([1, 59, 3, 61], basemap="Sjøkart (Kartverket)")))
S.raises("an inverted view is refused", ValueError, lambda: mx.make_bookmark([3, 59, 1, 61]))
S.raises("a view needs four numbers", ValueError, lambda: mx.make_bookmark([1, 2]))


def display_events():
    d = m.DisplaySettings()
    assert m.apply_display_event(d, {"type": "bookmark", "payload": {"view": [1, 59, 3, 61]}})
    assert len(d.bookmarks) == 1 and d.bookmarks[0]["basemap"] == "Ocean (Esri)"
    assert m.apply_display_event(d, {"type": "basemap", "payload": {"name": "Dark grey (Esri)"}})
    assert d.basemap == "Dark grey (Esri)"
    assert not m.apply_display_event(d, {"type": "basemap", "payload": {"name": "Dark grey (Esri)"}}), \
        "re-choosing the same base map is not a change"
    assert not m.apply_display_event(d, {"type": "bookmark", "payload": {"view": [5, 5, 1, 1]}}), \
        "a bad view must not add a bookmark"
    return True
S.check("bookmark and base-map events update the display settings", display_events)


def display_persistence():
    d = m.DisplaySettings(basemap="Sjøkart (Kartverket)",
                          bookmarks=[mx.make_bookmark([1, 59, 3, 61], "Field A")],
                          custom_layers=[mx.classify_url("https://x.com/arcgis/rest/services/A/MapServer")])
    txt = tb_project.project_to_yaml("P", demo(), tb_cost.CostSettings(), tb_schedule.ScheduleSettings(),
                                     fa.FASettings(), d)
    back = tb_project.project_from_yaml_full(txt)[5]
    assert back.basemap == "Sjøkart (Kartverket)" and back.bookmarks[0]["name"] == "Field A"
    assert back.custom_layers[0]["kind"] == "arcgis_export"
    return True
S.check("base map, bookmarks and URL layers are saved with the project", display_persistence)

# ── map layers by URL ───────────────────────────────────────────────────────
K = mx.classify_url
S.check("ArcGIS tiled service",
        lambda: (lambda s: s["kind"] == "arcgis_tile" and s["url"].endswith("/MapServer/tile/{z}/{y}/{x}"))(
            K("https://services.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Reference/MapServer/tile")))
S.check("ArcGIS tile template kept as is",
        lambda: K("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}")["kind"]
        == "arcgis_tile")
S.check("ArcGIS map service drawn with export",
        lambda: (lambda s: s["kind"] == "arcgis_export" and s["url"].endswith("/MapServer") and s["overlay"])(
            K("https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/MapServer/")))
S.check("an export address is trimmed back to the service",
        lambda: K("https://h.com/arcgis/rest/services/A/MapServer/export")["url"].endswith("/A/MapServer"))
S.check("ArcGIS image service",
        lambda: K("https://h.com/arcgis/rest/services/DEM/ImageServer")["kind"] == "arcgis_export")
S.check("ArcGIS feature layer becomes a vector overlay",
        lambda: (lambda s: s["kind"] == "arcgis_features" and s["layer_url"].endswith("/FeatureServer/3"))(
            K("https://h.com/arcgis/rest/services/Pipelines/FeatureServer/3")))
S.check("a MapServer layer number is a feature layer too",
        lambda: K("https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/MapServer/311")["kind"]
        == "arcgis_features")
S.check("WMS with layers in the address",
        lambda: (lambda s: s["kind"] == "wms" and s["layers"] == "mean_multicolour" and s["url"].endswith("/wms"))(
            K("https://ows.emodnet-bathymetry.eu/wms?service=WMS&request=GetMap&layers=mean_multicolour")))
S.check("WMS layer name given separately", lambda: K("https://ows.emodnet-bathymetry.eu/wms", wms_layers="contours")["layers"] == "contours")
S.raises("a WMS without a layer name is refused", ValueError, lambda: K("https://ows.emodnet-bathymetry.eu/wms"))
S.check("XYZ tiles", lambda: K("https://tile.openstreetmap.org/{z}/{x}/{y}.png")["kind"] == "xyz")
S.check("the service name becomes the layer name",
        lambda: K("https://h.com/arcgis/rest/services/World_Topo_Map/MapServer")["name"] == "World Topo Map")
S.check("a name given wins", lambda: K("https://h.com/arcgis/rest/services/A/MapServer", name="Pipes 2026")["name"] == "Pipes 2026")
S.raises("http:// is refused (the browser would block it)", ValueError, lambda: K("http://h.com/wms?layers=a"))
S.raises("an unrecognised address lists the accepted forms", ValueError, lambda: K("https://example.com/some/page"))


def refusal_text():
    try:
        K("https://example.com/some/page")
    except ValueError as exc:
        return "MapServer" in str(exc) and "{z}" in str(exc)
    return False
S.check("the refusal message shows the accepted address forms", refusal_text)
S.check("opacity is clamped", lambda: K("https://tile.x.com/{z}/{x}/{y}.png", opacity=7)["opacity"] == 1.0)


class _Resp:
    def __init__(self, p, status=200): self._p, self.status_code = p, status
    def raise_for_status(self):
        if self.status_code >= 300: raise RuntimeError(self.status_code)
    def json(self): return self._p
class _Sess:
    def __init__(self, pages): self.pages, self.calls = list(pages), []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return _Resp(self.pages.pop(0))


def feature_fetch():
    page = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2.5, 60.5]}, "properties": {"name": "P-1"}}]}
    s = _Sess([page])
    fc = mx.fetch_arcgis_features("https://h.com/arcgis/rest/services/A/FeatureServer/0", (2, 60, 3, 61), s)
    assert s.calls[0][0].endswith("/FeatureServer/0/query"), s.calls[0][0]
    assert fc["features"][0]["properties"]["_label"] == "P-1"
    return True
S.check("features of any ArcGIS layer are fetched as GeoJSON", feature_fetch)
S.raises("a service error is reported, not returned as empty", ValueError,
         lambda: mx.fetch_arcgis_features("https://h.com/x/FeatureServer/0", (2, 60, 3, 61),
                                          _Sess([{"error": {"message": "no"}}, {"error": {"message": "Token required"}}])))

# ── sharing ─────────────────────────────────────────────────────────────────
def project_yaml(lay=None, display=None):
    return tb_project.project_to_yaml("Field A concept", lay or demo(), tb_cost.CostSettings(),
                                      tb_schedule.ScheduleSettings(), fa.FASettings(), display)


S.check("an unchanged catalogue shares as an empty diff",
        lambda: sh.catalog_diff(c.Catalog()) == {"items": [], "removed_items": [], "spreads": [], "removed_spreads": []})


def catalog_diff_round_trip():
    cat = c.Catalog()
    cat.override("fl_rigid_cs", procurement_usd=12345.0)
    d = sh.catalog_diff(cat)
    assert [i["item_id"] for i in d["items"]] == ["fl_rigid_cs"], d
    back = c.Catalog.from_dict(sh.catalog_from_diff(d))
    assert back.get("fl_rigid_cs").procurement_usd == 12345.0
    assert len(back.items) == len(c.Catalog().items)
    return True
S.check("a changed rate travels as a one-item diff and comes back", catalog_diff_round_trip)


def link_round_trip():
    """The whole point: the colleague sees exactly what was built."""
    lay = demo()
    import tb_fluids as fl
    fl.set_reservoir(lay, fl.new_reservoir("Garn", "gas condensate"))
    fl.assign_reservoir(lay, ["W1"], "Garn")
    mx.add_sketch(lay, mx.make_sketch("circle", center=(60.5, 2.6), radius_m=500, label="zone"))
    disp = m.DisplaySettings(basemap="Sjøkart (Kartverket)", color_mode="fluid",
                             bookmarks=[mx.make_bookmark([2.4, 60.4, 2.8, 60.7], "Template")])
    payload = sh.build_payload(project_yaml(lay, disp), view=[2.4, 60.4, 2.8, 60.7], note="for review")
    token = sh.encode(payload)
    got = sh.restore_payload(sh.decode(token))
    name, lay2, cost, sched, fas, disp2 = tb_project.project_from_yaml_full(got["project_yaml"])
    assert name == "Field A concept"
    assert set(lay2.nodes) == set(lay.nodes) and set(lay2.edges) == set(lay.edges)
    for nid in lay.nodes:
        assert (lay2.nodes[nid].lat, lay2.nodes[nid].lon) == (lay.nodes[nid].lat, lay.nodes[nid].lon), nid
    assert fl.well_fluid(lay2, "W1") == ("gas condensate", "from reservoir Garn")
    assert mx.sketches(lay2)[0]["label"] == "zone"
    assert disp2.basemap == "Sjøkart (Kartverket)" and disp2.color_mode == "fluid"
    assert disp2.bookmarks[0]["name"] == "Template"
    assert got["view"] == [2.4, 60.4, 2.8, 60.7] and got["note"] == "for review"
    return True
S.check("a shared link reproduces the design exactly", link_round_trip)


def demo_link_is_short():
    link = sh.design_link("https://tieback.example.app", sh.encode(sh.build_payload(project_yaml())))
    assert sh.link_ok(link), len(link)
    assert len(link) < 3000, f"the demo link is {len(link)} characters"
    return True
S.check("the demo design fits in a link comfortably", demo_link_is_short)


def concepts_travel():
    cases = [tb_cases.snapshot("A", demo(), tb_cost.CostSettings(), tb_schedule.ScheduleSettings()),
             tb_cases.snapshot("B", demo(), tb_cost.CostSettings(), tb_schedule.ScheduleSettings())]
    payload = sh.build_payload(project_yaml(), cases=cases, active_case="B")
    assert "catalog" not in payload["cases"][0]["project"], "concepts must share their catalogue as a diff"
    got = sh.restore_payload(sh.decode(sh.encode(payload)))
    assert [c_["name"] for c_ in got["cases"]] == ["A", "B"] and got["active_case"] == "B"
    lay_b = tb_cases.restore(got["cases"][1])[1]
    assert set(lay_b.nodes) == set(demo().nodes)
    return True
S.check("saved concepts travel in the link and reopen", concepts_travel)


def truncated_link():
    token = sh.encode(sh.build_payload(project_yaml()))
    try:
        sh.decode(token[: len(token) // 2])
    except ValueError as exc:
        return "cut short" in str(exc)
    return False
S.check("a link cut short says so, in words", truncated_link)
S.raises("an unknown link format is refused", ValueError, lambda: sh.decode("xyz"))
S.raises("a payload from something else is refused", ValueError, lambda: sh.restore_payload({"schema": "other"}))


def zip_bomb_refused():
    import base64, zlib
    bomb = sh.PREFIX + base64.urlsafe_b64encode(zlib.compress(b"0" * (sh.MAX_DECODED_BYTES + 10), 9)).decode().rstrip("=")
    try:
        sh.decode(bomb)
    except ValueError as exc:
        return "too large" in str(exc)
    return False
S.check("a link that inflates past the limit is refused", zip_bomb_refused)
S.check("a long link is flagged", lambda: not sh.link_ok("https://x/?design=" + "a" * sh.MAX_LINK_CHARS))


def local_store(tmp=None):
    import tempfile
    d = tempfile.mkdtemp()
    store = sh.LocalStore(d)
    sid = store.save(sh.build_payload(project_yaml()))
    got = sh.restore_payload(store.load(sid))
    assert "Field A concept" in got["project_yaml"]
    assert sh.short_link("https://a.app/?x=1", sid) == f"https://a.app/?share={sid}"
    assert not store.durable, "the local store must admit it is not durable"
    return True
S.check("a short link to a stored copy round-trips", local_store)
S.raises("a missing stored design explains why", ValueError,
         lambda: sh.LocalStore(__import__("tempfile").mkdtemp()).load("abcdef1234567890"))
S.raises("a malformed share id is refused before touching the disk", ValueError,
         lambda: sh.LocalStore("/tmp").load("../../etc/passwd"))


class _GistSess:
    def __init__(self): self.store = {}
    def post(self, url, json=None, headers=None, timeout=None):
        gid = "g" * 20
        self.store[gid] = json["files"]
        assert json["public"] is False, "the gist must be secret"
        return _Resp({"id": gid}, 201)
    def get(self, url, headers=None, timeout=None):
        gid = url.rsplit("/", 1)[-1]
        if gid not in self.store:
            return _Resp({}, 404)
        return _Resp({"files": self.store[gid]}, 200)


def gist_store():
    s = _GistSess()
    store = sh.GistStore("token", s)
    sid = store.save(sh.build_payload(project_yaml()))
    got = sh.restore_payload(store.load(sid))
    assert "Field A concept" in got["project_yaml"] and store.durable
    return True
S.check("a secret GitHub gist stores and returns the design", gist_store)
S.raises("a deleted gist says so", ValueError, lambda: sh.GistStore("t", _GistSess()).load("h" * 20))
S.raises("a gist store needs a token", ValueError, lambda: sh.GistStore("", _GistSess()))
S.check("the confidentiality note names the risk",
        lambda: "anyone" in sh.CONFIDENTIALITY and "real field data" in sh.CONFIDENTIALITY)


def redelivered_bookmark_is_not_repeated():
    """Streamlit returns the component's last value on every rerun. A bookmark
    event must be applied once, however many times it arrives."""
    d = m.DisplaySettings()
    state = {}
    ev_ = {"nonce": "N1", "seq": 4, "type": "bookmark", "payload": {"view": [1, 59, 3, 61]}}
    assert m.apply_display_event(d, ev_, state)
    m.apply_event(demo(), ev_, state)                  # the app consumes it, marking it seen
    for _ in range(5):
        assert not m.apply_display_event(d, ev_, state)
    assert len(d.bookmarks) == 1, len(d.bookmarks)
    return True
S.check("a bookmark event delivered again is not applied again", redelivered_bookmark_is_not_repeated)
S.check("an unseen event is new, a seen one is not",
        lambda: m.is_new_event({"nonce": "a", "seq": 2}, {"seen": {"a": 1}})
        and not m.is_new_event({"nonce": "a", "seq": 1}, {"seen": {"a": 1}}))

sys.exit(0 if S.report() else 1)
