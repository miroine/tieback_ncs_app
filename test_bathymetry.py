import sys, copy, yaml
import tb_bathymetry as B, tb_catalog as c, tb_network as n, tb_geo as g
from _harness import Suite
S = Suite("test_bathymetry")

class Resp:
    def __init__(self, d): self.d = d
    def raise_for_status(self): pass
    def json(self): return self.d
class Sess:
    def __init__(self, by_point=None, fail=()): self.by_point, self.fail, self.calls = by_point or {}, set(fail), []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        geom = (params or {}).get("geom", "")
        if any(f in geom for f in self.fail):
            raise ConnectionError("simulated outage")
        if "depth_profile" in url:
            return Resp([{"smoothed": 100.0, "lat": 60.5, "lon": 2.5, "distance": 0},
                         {"smoothed": 140.0, "lat": 60.6, "lon": 2.6, "distance": 12000}])
        for key, val in self.by_point.items():
            if key in geom:
                return Resp(val)
        return Resp({"min": -122.0, "max": -118.0, "avg": -120.0, "smoothed": -119.5})

S.check("depth uses smoothed value, positive down", lambda: B.depth_at(60.5, 2.5, Sess()) == 119.5)
S.check("EMODnet's positive-depth response is accepted (regression: it was discarded, so every lookup came back empty)",
        lambda: B.depth_at(60.5, 2.5, Sess({"2.500000": {"min": 31.2, "max": 31.3, "avg": 31.25,
                                                         "stdev": 0.05, "smoothed": 30.95}})) == 30.95)
S.check("positive depth also works through the batch helper",
        lambda: B.depths_at([("A", 60.5, 2.5)], Sess({"2.500000": {"avg": 142.0}}))["A"] == 142.0)
S.check("zero reading is treated as no data",
        lambda: B.depth_at(60.5, 2.5, Sess({"2.500000": {"smoothed": 0}})) is None)
S.check("falls back to avg when smoothed missing", lambda: B.depth_at(60.5, 2.5, Sess({"2.500000": {"avg": -87.0}})) == 87.0)
# The REST API reports depth positive-down, so a bare number is a depth, not an elevation.
# Land and gaps come back without a numeric value.
S.check("no numeric value (land or gap) → None",
        lambda: B.depth_at(60.5, 5.0, Sess({"5.000000": {"smoothed": None, "avg": None}})) is None)
S.check("negative value is read as an elevation and converted to depth",
        lambda: B.depth_at(60.5, 5.0, Sess({"5.000000": {"smoothed": -314.0}})) == 314.0)
S.check("no data → None", lambda: B.depth_at(60.5, 5.0, Sess({"5.000000": {}})) is None)
def wkt():
    s_ = Sess(); B.depth_at(60.5, 2.25, s_)
    assert s_.calls[0][1]["geom"] == "POINT(2.250000 60.500000)" and s_.calls[0][0].endswith("/depth_sample")
S.check("WKT is POINT(lon lat)", wkt)
S.raises("bad coordinates raise", ValueError, lambda: B.depth_at(95.0, 2.0, Sess()))
def batch():
    got = B.depths_at([("A", 60.5, 2.5), ("B", 60.6, 2.6), ("C", 60.7, 2.7)], Sess(fail=("2.600000",)))
    assert got["A"] == 119.5 and got["B"] is None and got["C"] == 119.5
S.check("batch: one failed point does not stop the rest", batch)
def profile():
    rows = B.depth_profile([(60.5, 2.5), (60.6, 2.6)], Sess())
    assert len(rows) == 2 and rows[1]["depth_m"] == 140.0 and rows[1]["distance_m"] == 12000
S.check("depth profile parsed, depths positive down", profile)
S.raises("profile needs two vertices", ValueError, lambda: B.depth_profile([(60.5, 2.5)], Sess()))

FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def demo(): return n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
def fill_blank():
    lay = demo()
    got = B.fill_node_depths(lay, Sess())
    assert lay.nodes["W1"].water_depth_m == 119.5            # was blank
    assert lay.nodes["TMPL_A"].water_depth_m == 150.0        # kept: only_blank
    assert "HOST_A" not in got                               # host skipped
S.check("fills blank subsea nodes only, skips the host", fill_blank)
def fill_all():
    lay = demo()
    B.fill_node_depths(lay, Sess(), only_blank=False)
    assert lay.nodes["TMPL_A"].water_depth_m == 119.5
S.check("only_blank=False overwrites existing depths", fill_all)
def ed50():
    lay = demo(); lay.settings.datum = "ED50"
    s_ = Sess(); B.fill_node_depths(lay, s_)
    lon = float(s_.calls[0][1]["geom"].split("(")[1].split()[0])
    la, lo = g.transform_datum(lay.nodes["W1"].lat, lay.nodes["W1"].lon, "ED50", "WGS84")
    assert abs(lon - lo) < 1e-6      # sampled in WGS84, not raw ED50
S.check("ED50 layouts sampled at WGS84 positions", ed50)
def failure_keeps():
    lay = demo()
    B.fill_node_depths(lay, Sess(fail=("POINT",)))
    assert lay.nodes["W1"].water_depth_m == 0.0
S.check("service failure leaves depths untouched", failure_keeps)
def resample():
    rows = [{"depth_m": 100}, {"depth_m": None}, {"depth_m": 120}, {"depth_m": 118}]
    out = B.resample_profile(rows, 7)
    assert len(out) == 7 and out[0] == 100 and out[-1] == 118
    assert abs(out[2] - 110.0) < 1e-9        # gap filled by interpolation
S.check("profile resampling fills gaps and keeps the ends", resample)
S.check("all-null profile returns nothing", lambda: B.resample_profile([{"depth_m": None}] * 4) == [])
def route_profiles():
    lay = demo()
    got = B.fetch_route_profiles(lay, Sess(), samples=12)
    assert got["FL1"] == 12 and len(lay.edges["FL1"].attrs["seabed_profile"]) == 12
    assert "RISER1" not in got and "UMB1" not in got     # flowlines and utility lines only
S.check("route profiles stored on flowline edges", route_profiles)
def route_profile_failure():
    lay = demo()
    got = B.fetch_route_profiles(lay, Sess(fail=("LINESTRING",)), samples=12)
    assert got["FL1"] is None and "seabed_profile" not in lay.edges["FL1"].attrs
S.check("failed profile leaves the edge untouched", route_profile_failure)
def spans():
    flat = [100.0] * 20
    dip = [100.0] * 10 + [103, 106, 108, 106, 103] + [100.0] * 10
    assert B.free_spans(flat, 10.0) == []
    assert B.free_spans([100 + 2 * i for i in range(20)], 10.0) == []      # steady slope: pipe lies on it
    sp = B.free_spans(dip, 10.0)
    assert len(sp) == 1 and sp[0]["start_m"] == 90.0 and sp[0]["end_m"] == 150.0 and sp[0]["max_gap_m"] == 8.0
S.check("taut-string spans: flat and sloping seabed clear, a dip spans exactly", spans)
S.check("ripples below the clearance threshold are ignored",
        lambda: B.free_spans([100, 100.2, 100, 100.2, 100], 10.0, gap_m=0.5) == [])
S.check("two separate dips give two spans",
        lambda: len(B.free_spans([100, 101, 104, 101, 100, 100, 103, 100, 100], 10.0)) == 2)
def long_valley():
    sp = B.free_spans([100] * 3 + [130] * 40 + [100] * 3, 10.0, max_span_m=200)
    assert sp[0]["truncated"] and sp[0]["length_m"] == 200
S.check("a long valley is reported but its length truncated", long_valley)
S.check("WMS layer names", lambda: B.WMS_LAYERS["colour"] == "mean_multicolour" and B.WMS_URL.endswith("/wms"))

# ── the live payload shapes, as the service actually answers ───────────────
class _Resp:
    def __init__(self, payload, status=200): self._p, self.status_code = payload, status
    def raise_for_status(self):
        if self.status_code != 200: raise RuntimeError(f"HTTP {self.status_code}")
    def json(self): return self._p
class _Sess:
    """Fake transport. `payload` may be one body, or {endpoint: body} — the two
    endpoints answer with different shapes, so diagnose() needs both."""
    def __init__(self, payload, status=200): self._p, self._s = payload, status
    def get(self, url, params=None, timeout=None):
        self.last = (url, params)
        body = self._p
        if isinstance(body, dict) and body and set(body) <= {"depth_sample", "depth_profile"}:
            body = body["depth_profile" if url.endswith("depth_profile") else "depth_sample"]
        return _Resp(body() if callable(body) else body, self._s)
class _Boom:
    def get(self, *a, **k): raise OSError("Name or service not known")

# depth_profile answers with a bare array of elevations, no positions
LIVE_PROFILE = [-100.96802, -100.84319, -100.77765, -99.71803]
LIVE_SAMPLE = {"avg": -100.96802, "smoothed": -100.93068, "elementarySurfaces": 1,
               "reference": {"identifier": "GEBCO2024", "type": "DTM"}}

def profile_from_bare_array():
    """Regression: the service returns [-100.97, …]; skipping non-dict records
    threw every sample away and looked exactly like 'no bathymetry data'."""
    rows = B.depth_profile([(60.5, 2.5), (60.55, 2.6)], _Sess(LIVE_PROFILE))
    assert len(rows) == 4, rows
    assert all(r["depth_m"] is not None for r in rows), rows
    assert abs(rows[0]["depth_m"] - 100.968) < 1e-3, rows[0]
    return True
S.check("depth_profile reads the live bare-array payload", profile_from_bare_array)

S.check("a bare-array profile resamples to the requested count",
        lambda: len(B.resample_profile(B.depth_profile([(60.5, 2.5), (60.6, 2.6)],
                                                       _Sess(LIVE_PROFILE)), 12)) == 12)
S.check("depth_sample reads the live object payload",
        lambda: abs(B.depth_at(60.5, 2.5, _Sess(LIVE_SAMPLE)) - 100.93068) < 1e-4)
S.check("record-shaped profiles still work",
        lambda: B.depth_profile([(60.5, 2.5), (60.6, 2.6)],
                                _Sess([{"smoothed": -90.0, "lat": 60.5, "lon": 2.5}]))[0]["depth_m"] == 90.0)
S.check("a null in the array is a gap, not a zero",
        lambda: B.depth_profile([(60.5, 2.5), (60.6, 2.6)], _Sess([-90.0, None, -92.0]))[1]["depth_m"] is None)

def diagnose_reports_success():
    rep = B.diagnose(_Sess(lambda: LIVE_SAMPLE))
    assert rep["depth_sample"]["ok"], rep
    assert abs(rep["depth_sample"]["depth_m"] - 100.93) < 0.1
    return True
S.check("diagnose reports a working service", diagnose_reports_success)

S.check("diagnose reports a network failure with its reason",
        lambda: "Name or service not known" in B.diagnose(_Boom())["depth_sample"]["error"])
S.check("diagnose reports an HTTP error status",
        lambda: B.diagnose(_Sess({}, status=503))["depth_sample"]["status"] == 503)
S.check("the message names the host when the network is down",
        lambda: "rest.emodnet-bathymetry.eu" in B.diagnosis_message(B.diagnose(_Boom())))
S.check("the message is positive when both endpoints answer",
        lambda: "reachable" in B.diagnosis_message(B.diagnose(
            _Sess({"depth_sample": LIVE_SAMPLE, "depth_profile": LIVE_PROFILE}))))
S.check("diagnose counts the profile samples it got",
        lambda: B.diagnose(_Sess({"depth_sample": LIVE_SAMPLE,
                                  "depth_profile": LIVE_PROFILE}))["depth_profile"]["with_depth"] == 4)

sys.exit(0 if S.report() else 1)
