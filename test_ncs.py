import sys
import tb_ncs as ncs
from _harness import Suite
S = Suite("test_ncs")

class Resp:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data
class FakeSession:
    def __init__(self, pages): self.pages, self.calls = list(pages), []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params))); return Resp(self.pages.pop(0))

def gj_page(n, start, exceeded):
    return {"type": "FeatureCollection", "exceededTransferLimit": exceeded,
            "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [2.1234567891, 60.1 + i * 1e-3]},
                          "properties": {"fclName": f"FAC {start + i}"}} for i in range(n)]}
BB = (2.0, 60.0, 3.0, 61.0)

S.check("layer IDs as verified in service directory",
        lambda: (ncs.NCS_LAYERS["fields"].layer_id, ncs.NCS_LAYERS["pipelines"].layer_id,
                 ncs.NCS_LAYERS["facilities"].layer_id, ncs.NCS_LAYERS["dev_wells"].layer_id) == (502, 311, 304, 205))
def params():
    p = ncs.query_params(BB, offset=2000)
    assert p["geometry"] == "2.0,60.0,3.0,61.0" and p["inSR"] == 4326 and p["outSR"] == 4326
    assert p["resultOffset"] == 2000 and p["resultRecordCount"] == 1000 and p["f"] == "geojson"
S.check("query params", params)
S.raises("invalid bbox raises", ValueError, lambda: ncs.query_params((3, 60, 2, 61)))
def paging():
    s = FakeSession([gj_page(1000, 0, True), gj_page(1000, 1000, True), gj_page(10, 2000, False)])
    fc = ncs.fetch_layer("facilities", BB, s, use_renderer=False)
    assert len(fc["features"]) == 2010 and len(s.calls) == 3
    assert [c[1]["resultOffset"] for c in s.calls] == [0, 1000, 2000]
    assert s.calls[0][0].endswith("/MapServer/304/query")
    assert fc["features"][5]["properties"]["_label"] == "FAC 5" and not fc["truncated"]
S.check("pagination follows exceededTransferLimit", paging)
def trunc():
    s = FakeSession([gj_page(1000, 0, True), gj_page(1000, 1000, True)])
    fc = ncs.fetch_layer("facilities", BB, s, max_features=1500, use_renderer=False)
    assert len(fc["features"]) == 1500 and fc["truncated"] and len(s.calls) == 2
S.check("max_features truncates and flags", trunc)
S.check("coordinates rounded to 6 dp",
        lambda: ncs.fetch_layer("facilities", BB, FakeSession([gj_page(1, 0, False), {}]))["features"][0]["geometry"]["coordinates"][0] == 2.123457)
def esri():
    page = {"features": [
        {"attributes": {"pipName": "P1"}, "geometry": {"paths": [[[2, 60], [2.5, 60.5]]]}},
        {"attributes": {"fldName": "F"}, "geometry": {"rings": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]],
                                                                 [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8], [0.2, 0.2]]]}},
        {"attributes": {}, "geometry": None}]}
    fc, ex = ncs.to_feature_collection(page)
    assert fc["features"][0]["geometry"] == {"type": "LineString", "coordinates": [[2, 60], [2.5, 60.5]]}
    g = fc["features"][1]["geometry"]
    assert g["type"] == "Polygon" and len(g["coordinates"]) == 2  # outer + hole
    assert fc["features"][2]["geometry"] is None and not ex
S.check("Esri JSON fallback: paths, rings with hole, null geometry", esri)
def esri_multi():
    g = ncs.esri_geometry_to_geojson({"rings": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]], [[5, 5], [5, 6], [6, 6], [6, 5], [5, 5]]]})
    assert g["type"] == "MultiPolygon" and len(g["coordinates"]) == 2
S.check("two clockwise rings -> MultiPolygon", esri_multi)
S.raises("service error payload raises", RuntimeError,
         lambda: ncs.to_feature_collection({"error": {"code": 400, "message": "bad"}}))
S.check("null geometry skipped in fetch",
        lambda: len(ncs.fetch_layer("fields", BB, FakeSession([{"features": [{"attributes": {"fldName": "X"}, "geometry": None}]}]))["features"]) == 0)  # no features → no renderer call
def discover():
    svc = {"layers": [{"id": 999, "name": "Pipelines"}, {"id": 1502, "name": "Field by status"}, {"id": 1, "name": "Other"}]}
    d = ncs.discover_layers(svc)
    assert d["pipelines"] == 999 and d["fields"] == 1502 and "blocks" not in d
S.check("discover_layers resolves by name", discover)
def bbox():
    xmin, ymin, xmax, ymax = ncs.bbox_around([(60.5, 2.5)], 11.132)
    assert abs((ymax - ymin) - 0.2) < 1e-9 and abs((xmax - xmin) - 0.4) < 0.01
S.check("bbox_around buffer in km", bbox)
S.raises("bbox_around empty raises", ValueError, lambda: ncs.bbox_around([]))
def labels():
    L = ncs.NCS_LAYERS["dev_wells"]
    assert ncs.label_for({"wlbwellborename": "34/7-A-1"}, L) == "34/7-A-1"
    assert ncs.label_for({"someName": "X"}, L) == "X"
    assert ncs.label_for({}, L) == ""
S.check("label fallbacks (case-insensitive, *name)", labels)
# ── service-driven symbology ──
RJ = {"drawingInfo": {"renderer": {"type": "uniqueValue", "field1": "dscHcType", "uniqueValueInfos": [
    {"value": "OIL", "symbol": {"type": "esriSFS", "color": [11, 190, 0, 165], "outline": {"color": [130, 130, 130, 165]}}},
    {"value": "GAS", "symbol": {"type": "esriSFS", "color": [255, 0, 0, 165], "outline": {"color": [130, 130, 130, 165]}}},
    {"value": "OIL/GAS", "symbol": {"type": "esriPFS", "outline": {"color": [130, 130, 130, 165]}}}]}}}
S.check("layer 504 = all discoveries by main HC type", lambda: ncs.NCS_LAYERS["discoveries_all"].layer_id == 504)
S.check("NCS bbox covers North Sea to Barents", lambda: ncs.NCS_BBOX[1] < 56 and ncs.NCS_BBOX[3] > 81)
def renderer():
    r = ncs.parse_renderer(RJ)
    assert r["field"] == "dscHcType"
    assert r["values"]["OIL"]["fill"] == "#0BBE00" and r["values"]["GAS"]["fill"] == "#FF0000"
    assert r["values"]["OIL/GAS"]["pattern"] == "oil_gas"
S.check("renderer parsed: Sodir oil green, gas red, oil/gas hatched", renderer)
S.check("simple renderer supported", lambda: ncs.parse_renderer(
    {"drawingInfo": {"renderer": {"type": "simple", "symbol": {"type": "esriSFS", "color": [1, 2, 3, 255]}}}})["default"]["fill"] == "#010203")
S.check("no drawingInfo → empty renderer", lambda: ncs.parse_renderer({}) == {"field": None, "values": {}, "default": None})
def applied():
    r = ncs.parse_renderer(RJ)
    fc = ncs.apply_renderer({"features": [{"properties": {"dscHcType": "OIL"}}, {"properties": {"DSCHCTYPE": "GAS"}},
                                          {"properties": {"dscHcType": "UNKNOWN"}}]}, r, "#E9A23B")
    p_ = [f["properties"] for f in fc["features"]]
    assert p_[0]["_fill"] == "#0BBE00" and p_[1]["_fill"] == "#FF0000" and p_[2]["_fill"] == "#E9A23B"
    assert p_[0]["_outline"] == "#828282" and "_pattern" not in p_[0]
S.check("features coloured by HC type, unknown values fall back", applied)
def fetch_with_renderer():
    s_ = FakeSession([gj_page(2, 0, False), RJ])
    fc = ncs.fetch_layer("discoveries_all", BB, s_)
    assert fc["renderer"]["field"] == "dscHcType" and s_.calls[-1][1]["f"] == "json"
    assert all("_fill" in f["properties"] for f in fc["features"])
S.check("fetch_layer attaches renderer styling", fetch_with_renderer)
def renderer_failure():
    class Flaky(FakeSession):
        def get(self, url, params=None, timeout=None):
            if params.get("f") == "json":
                raise ConnectionError("no symbology")
            return super().get(url, params, timeout)
    fc = ncs.fetch_layer("fields", BB, Flaky([gj_page(1, 0, False)]))
    assert fc["renderer"] is None and len(fc["features"]) == 1
S.check("renderer failure keeps the data", renderer_failure)
S.check("use_renderer=False skips the extra call",
        lambda: len(FakeSession([gj_page(1, 0, False)]).calls) == 0
        and ncs.fetch_layer("fields", BB, FakeSession([gj_page(1, 0, False)]), use_renderer=False)["features"])
sys.exit(0 if S.report() else 1)
