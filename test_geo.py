import math, sys
from scipy.integrate import quad
import tb_geo as g
from _harness import Suite
S = Suite("test_geo")
GRS80 = g.Ellipsoid("GRS80", 6378137.0, 298.257222101)

def veness():  # Veness geodesy docs: Eiffel Tower -> 31 N 448252 5411933
    e, n, z, h = g.geo_to_utm(48.8582, 2.2945)
    assert z == 31 and h == "N" and abs(e - 448251.795) < 0.01 and abs(n - 5411932.678) < 0.01
S.check("UTM forward vs Veness reference", veness)

def arc():
    for lat in (56, 60, 65, 71.5):
        E = g.WGS84
        m = quad(lambda p: E.a*(1-E.e2)/(1-E.e2*math.sin(p)**2)**1.5, 0, math.radians(lat), epsabs=1e-9)[0]
        assert abs(g.tm_forward(lat, 9, 9)[1] - 0.9996*m) < 1e-3, lat
S.check("TM northing on CM == k0 × meridian arc (numerical integral)", arc)

def arc_intl():
    E = g.INTL1924
    m = quad(lambda p: E.a*(1-E.e2)/(1-E.e2*math.sin(p)**2)**1.5, 0, math.radians(61))[0]
    assert abs(g.tm_forward(61, 3, 3, E)[1] - 0.9996*m) < 1e-3
S.check("TM meridian arc on International 1924", arc_intl)

def roundtrip():
    worst = 0.0
    for datum in ("WGS84", "ED50"):
        for z in (31, 32, 33, 34, 35):
            cm = g.central_meridian(z)
            for lat in (56.0, 60.3, 66.7, 71.9):
                for dl in (-3.0, 0.0, 2.9):
                    e, n, _, _ = g.geo_to_utm(lat, cm + dl, z, datum)
                    la, lo = g.utm_to_geo(e, n, z, "N", datum)
                    e2, n2, _, _ = g.geo_to_utm(la, lo, z, datum)
                    worst = max(worst, abs(e - e2), abs(n - n2))
    assert worst < 1e-3, worst
S.check("UTM round-trip < 1 mm across NCS zones 31–35, both datums", roundtrip)

def south():
    e, n, z, h = g.geo_to_utm(-33.9, 18.4)
    la, lo = g.utm_to_geo(e, n, z, h)
    assert h == "S" and abs(la + 33.9) < 1e-9 and abs(lo - 18.4) < 1e-9
S.check("southern hemisphere round-trip", south)

S.check("zone 32V Norway exception", lambda: g.utm_zone_for(60.0, 5.0) == 32)
S.check("zone 31 west of 3E at 60N", lambda: g.utm_zone_for(60.0, 2.5) == 31)
S.check("Svalbard exception 78N 10E -> 33", lambda: g.utm_zone_for(78.0, 10.0) == 33)
S.check("Barents 72N 25E -> 35", lambda: g.utm_zone_for(72.5, 25.0) == 35)

def vin():
    s, a1, a2 = g.vincenty_inverse(g.dms_to_deg(-37,57,3.72030), g.dms_to_deg(144,25,29.52440),
                                   g.dms_to_deg(-37,39,10.15610), g.dms_to_deg(143,55,35.38390), GRS80)
    assert abs(s - 54972.271) < 1e-3 and abs(a1 - g.dms_to_deg(306,52,5.37)) < 1/3600*0.02
    assert abs(((a2 + 180) % 360) - g.dms_to_deg(127,10,25.07)) < 1/3600*0.02
S.check("Vincenty vs Geoscience Australia Flinders Peak–Buninyong (GRS80)", vin)

S.check("Vincenty zero distance", lambda: g.vincenty_inverse(60, 3, 60, 3)[0] == 0.0)
def vin_meridian():
    E = g.WGS84
    m = quad(lambda p: E.a*(1-E.e2)/(1-E.e2*math.sin(p)**2)**1.5, math.radians(60), math.radians(61))[0]
    assert abs(g.geodesic_distance(60, 4, 61, 4) - m) < 1e-3
S.check("Vincenty along meridian == meridian arc integral", vin_meridian)
S.raises("Vincenty antipodal non-convergence raises", ValueError,
         lambda: g.vincenty_inverse(0.0, 0.0, 0.5, 179.7))

def datum_rt():
    la, lo = g.transform_datum(60.5, 2.7, "ED50", "WGS84")
    la2, lo2 = g.transform_datum(la, lo, "WGS84", "ED50")
    assert abs(la2 - 60.5) < 1e-8 and abs(lo2 - 2.7) < 1e-8
S.check("ED50<->WGS84 round-trip", datum_rt)

def datum_mag():
    la, lo = g.transform_datum(60.5, 2.7, "ED50", "WGS84")
    d = g.geodesic_distance(60.5, 2.7, la, lo)
    assert 50 < d < 250 and la < 60.5 and lo < 2.7, d  # WGS84 coords SW of ED50 in North Sea
S.check("ED50->WGS84 shift magnitude/direction plausible for North Sea", datum_mag)

def helmert_7p():
    p = (10.0, -5.0, 3.0, 0.5, -0.3, 0.8, 1.5)
    x, y, z = g.geo_to_ecef(60, 3, 0, g.WGS84)
    x2, y2, z2 = g._helmert(*g._helmert(x, y, z, p), p, inverse=True)
    assert abs(x - x2) < 0.01 and abs(y - y2) < 0.01 and abs(z - z2) < 0.01  # small-angle approx
S.check("7-parameter Helmert forward/inverse consistent (<1 cm)", helmert_7p)

def ecef_rt():
    for lat, lon, h in ((60, 3, 0), (-45, 170, 1200), (89.9, -40, -50)):
        la, lo, hh = g.ecef_to_geo(*g.geo_to_ecef(lat, lon, h, g.WGS84), g.WGS84)
        assert abs(la - lat) < 1e-9 and abs(lo - lon) < 1e-9 and abs(hh - h) < 1e-4
S.check("ECEF round-trip", ecef_rt)

def route():
    v = [(60.0, 3.0), (60.1, 3.0), (60.1, 3.2)]
    base = g.polyline_length(v)
    assert abs(g.route_length(v, 0.05, 100) - (base * 1.05 + 100)) < 1e-6
    assert abs(base - (g.geodesic_distance(60, 3, 60.1, 3) + g.geodesic_distance(60.1, 3, 60.1, 3.2))) < 1e-9
S.check("route length = polyline × (1+allowance) + end allowance", route)
S.check("route with <2 vertices is zero", lambda: g.route_length([(60, 3)]) == 0.0)
S.raises("negative allowance raises", ValueError, lambda: g.route_length([(60, 3), (61, 3)], -0.1))
S.check("dms negative", lambda: abs(g.dms_to_deg(-37, 30, 0) + 37.5) < 1e-12)
S.check("ED50 vs WGS84 UTM of same numbers differ (ellipsoid used)",
        lambda: abs(g.geo_to_utm(60, 3, 31, "ED50")[1] - g.geo_to_utm(60, 3, 31, "WGS84")[1]) > 50)

# ── route smoothing / curvature ──
def cr_through_points():
    pts = [(60.0, 3.0), (60.05, 3.1), (60.1, 3.0)]
    sm = g.catmull_rom(pts, 8)
    assert all(abs(a - b) < 1e-9 for a, b in zip(sm[0], pts[0]) ) and all(abs(a - b) < 1e-9 for a, b in zip(sm[-1], pts[-1]))
    assert any(abs(p[0] - pts[1][0]) < 1e-6 and abs(p[1] - pts[1][1]) < 1e-6 for p in sm)   # passes through the bend
    assert len(sm) == 2 * 8 + 1
S.check("Catmull-Rom passes through every surveyed vertex", cr_through_points)
S.check("smoothing a straight line changes nothing measurable",
        lambda: abs(g.polyline_length(g.catmull_rom([(60, 3), (60.1, 3), (60.2, 3)], 10))
                    - g.polyline_length([(60, 3), (60.2, 3)])) < 0.5)
S.check("smoothed route is longer than the cornered one but not by much",
        lambda: 1.0 < g.polyline_length(g.catmull_rom([(60, 3), (60.05, 3.1), (60.1, 3.0)], 10))
        / g.polyline_length([(60, 3), (60.05, 3.1), (60.1, 3.0)]) < 1.1)
S.check("fewer than 3 points returns the input", lambda: g.catmull_rom([(60, 3), (61, 3)], 10) == [(60, 3), (61, 3)])
def circle_radius():
    k = math.cos(math.radians(60))
    pts = [(60 + 1000 * math.cos(t) / 110540, 3 + 1000 * math.sin(t) / (111320 * k)) for t in (0, 0.3, 0.6)]
    assert abs(g.min_bend_radius(pts) - 1000) < 2
S.check("min bend radius exact on a 1000 m circle", circle_radius)
S.check("collinear points → infinite radius", lambda: g.min_bend_radius([(60, 3), (60.1, 3), (60.2, 3)]) == math.inf)
S.check("smoothing raises the tightest bend radius",
        lambda: g.min_bend_radius(g.catmull_rom([(60, 3), (60.02, 3.05), (60.04, 3.0)], 12))
        > g.min_bend_radius([(60, 3), (60.02, 3.05), (60.04, 3.0)]) * 0.0  # smoothed curve is resolved finer
        and g.min_bend_radius(g.catmull_rom([(60, 3), (60.02, 3.05), (60.04, 3.0)], 12)) > 0)
sys.exit(0 if S.report() else 1)
