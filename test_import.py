import sys, io, json, struct, zipfile, copy, yaml
import tb_import as im, tb_geo as g, tb_catalog as c, tb_network as n
from _harness import Suite
S = Suite("test_import")

# ── hand-built shapefile per ESRI Shapefile Technical Description (1998) ──
def shp_file(shape_type, records):
    body = b""
    for i, content in enumerate(records, 1):
        body += struct.pack(">ii", i, len(content) // 2) + content
    header = struct.pack(">i", 9994) + b"\x00" * 20 + struct.pack(">i", (100 + len(body)) // 2)
    header += struct.pack("<ii", 1000, shape_type) + struct.pack("<8d", *([0.0] * 8))
    return header + body
def rec_point(x, y): return struct.pack("<idd", 1, x, y)
def rec_poly(stype, parts):
    pts = [p for part in parts for p in part]; idx, k = [], 0
    for part in parts: idx.append(k); k += len(part)
    return (struct.pack("<i4d", stype, 0, 0, 0, 0) + struct.pack("<ii", len(parts), len(pts))
            + struct.pack(f"<{len(idx)}i", *idx) + b"".join(struct.pack("<dd", *p) for p in pts))
def dbf_file(fields, rows):
    hdr_len = 32 + 32 * len(fields) + 1
    rec_len = 1 + sum(f[2] for f in fields)
    out = struct.pack("<BBBBIHH20x", 3, 126, 1, 1, len(rows), hdr_len, rec_len)
    for name, t, ln in fields:
        out += name.encode().ljust(11, b"\x00") + t.encode() + b"\x00" * 4 + bytes([ln, 0]) + b"\x00" * 14
    out += b"\x0d"
    for r in rows:
        out += b" " + b"".join(str(v).encode().ljust(ln)[:ln] if t == "C" else str(v).encode().rjust(ln)[:ln]
                               for (name, t, ln), v in zip(fields, r))
    return out + b"\x1a"
def zipit(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items(): z.writestr(k, v)
    return b.getvalue()
PRJ_ED50_31 = 'PROJCS["ED_1950_UTM_Zone_31N",GEOGCS["GCS_European_1950",DATUM["D_European_1950",SPHEROID["International_1924",6378388.0,297.0]]],PROJECTION["Transverse_Mercator"]]'
PRJ_WGS = 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]]]'

def shp_points_ed50():
    e, nn, _, _ = g.geo_to_utm(60.5, 2.7, 31, "ED50")
    data = zipit({"wells.shp": shp_file(1, [rec_point(e, nn), rec_point(e + 1000, nn)]),
                  "wells.dbf": dbf_file([("NAME", "C", 10), ("DEPTH", "N", 6)], [("A-1", 150), ("A-2", 152)]),
                  "wells.prj": PRJ_ED50_31})
    fc = im.read_any("wells.zip", data)
    lon, lat = fc["features"][0]["geometry"]["coordinates"]
    la_w, lo_w = g.transform_datum(60.5, 2.7, "ED50", "WGS84")
    assert abs(lat - la_w) < 1e-9 and abs(lon - lo_w) < 1e-9
    assert fc["features"][0]["properties"] == {"NAME": "A-1", "DEPTH": 150}
    assert len(fc["features"]) == 2
S.check("shapefile points ED50 UTM31 -> WGS84 with DBF attributes", shp_points_ed50)
def shp_line_poly():
    line = rec_poly(3, [[(2.0, 60.0), (2.5, 60.2)], [(3, 61), (3.1, 61.1)]])
    cw = [(0, 60), (0, 61), (1, 61), (1, 60), (0, 60)]           # clockwise outer
    hole = [(0.2, 60.2), (0.8, 60.2), (0.8, 60.8), (0.2, 60.8), (0.2, 60.2)]
    poly = rec_poly(5, [cw, hole])
    fl = im.read_shapefile_zip(zipit({"l.shp": shp_file(3, [line]), "l.prj": PRJ_WGS}))
    fp = im.read_shapefile_zip(zipit({"p.shp": shp_file(5, [poly]), "p.prj": PRJ_WGS}))
    assert fl["features"][0]["geometry"]["type"] == "MultiLineString"
    gp = fp["features"][0]["geometry"]; assert gp["type"] == "Polygon" and len(gp["coordinates"]) == 2
S.check("shapefile polyline (multi-part) and polygon with hole", shp_line_poly)
def shp_z():
    rec = struct.pack("<idddd", 11, 2.5, 60.5, 100.0, 0.0)
    fc = im.read_shapefile_zip(zipit({"z.shp": shp_file(11, [rec]), "z.prj": PRJ_WGS}))
    assert fc["features"][0]["geometry"]["coordinates"] == [2.5, 60.5]
S.check("PointZ supported", shp_z)
S.raises("shapefile without .prj requires CRS", ValueError,
         lambda: im.read_shapefile_zip(zipit({"a.shp": shp_file(1, [rec_point(2, 60)])})))
S.check("shapefile without .prj accepts explicit CRS",
        lambda: len(im.read_shapefile_zip(zipit({"a.shp": shp_file(1, [rec_point(2, 60)])}), im.WGS84_GEO)["features"]) == 1)
S.raises("bad magic raises", ValueError, lambda: im.read_shapefile_zip(zipit({"a.shp": b"\x00" * 120, "a.prj": PRJ_WGS})))
S.raises("UTM coords without CRS rejected (not lon/lat)", ValueError,
         lambda: im.read_shapefile_zip(zipit({"a.shp": shp_file(1, [rec_point(450000, 6700000)]), "a.prj": PRJ_WGS})))
# CRS parsing
S.check("EPSG:23032", lambda: im.crs_from_text("EPSG:23032") == im.Crs("utm", "ED50", 32, "N"))
S.check("URN EPSG::32631", lambda: im.crs_from_text("urn:ogc:def:crs:EPSG::32631") == im.Crs("utm", "WGS84", 31, "N"))
S.check("CRS84", lambda: im.crs_from_text("urn:ogc:def:crs:OGC:1.3:CRS84") == im.WGS84_GEO)
S.check("EPSG:4230 ED50 geographic", lambda: im.crs_from_epsg(4230) == im.Crs("geographic", "ED50"))
S.check("ETRS89 UTM32 -> WGS84 UTM32", lambda: im.crs_from_epsg(25832) == im.Crs("utm", "WGS84", 32, "N"))
S.check("WKT ED50 UTM31", lambda: im.crs_from_text(PRJ_ED50_31) == im.Crs("utm", "ED50", 31, "N"))
S.check("WKT WGS84 geographic", lambda: im.crs_from_text(PRJ_WGS) == im.WGS84_GEO)
S.raises("unsupported EPSG raises", ValueError, lambda: im.crs_from_epsg(3857))
S.raises("unknown datum WKT raises", ValueError, lambda: im.crs_from_text('GEOGCS["GCS_Tokyo",DATUM["D_Tokyo"]]'))
# GeoJSON
def gj_legacy_crs():
    e, nn, _, _ = g.geo_to_utm(61.0, 4.0, 32, "WGS84")
    obj = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:32632"}},
           "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [e, nn]}, "properties": {"name": "X"}}]}
    fc = im.read_geojson(json.dumps(obj).encode())
    lon, lat = fc["features"][0]["geometry"]["coordinates"]
    assert abs(lat - 61) < 1e-9 and abs(lon - 4) < 1e-9
S.check("GeoJSON legacy crs member (UTM32) reprojected", gj_legacy_crs)
S.check("bare GeoJSON geometry accepted",
        lambda: im.read_geojson(b'{"type":"LineString","coordinates":[[2,60],[3,61]]}')["features"][0]["geometry"]["type"] == "LineString")
S.raises("GeoJSON with projected coords and no crs rejected", ValueError,
         lambda: im.read_geojson(b'{"type":"Point","coordinates":[450000,6700000]}'))
# KML / KMZ
KML = b'''<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>Host A</name><Point><coordinates>2.5,60.6,0</coordinates></Point></Placemark>
<Placemark><name>Route</name><ExtendedData><Data name="dia"><value>10</value></Data></ExtendedData>
<LineString><coordinates>2.5,60.6 2.6,60.55
 2.66,60.5</coordinates></LineString></Placemark>
<Placemark><name>Area</name><Polygon><outerBoundaryIs><LinearRing><coordinates>2,60 3,60 3,61 2,61 2,60</coordinates></LinearRing></outerBoundaryIs>
<innerBoundaryIs><LinearRing><coordinates>2.2,60.2 2.8,60.2 2.8,60.8 2.2,60.8 2.2,60.2</coordinates></LinearRing></innerBoundaryIs></Polygon></Placemark>
<Placemark><name>Multi</name><MultiGeometry><Point><coordinates>1,60</coordinates></Point><Point><coordinates>1.1,60</coordinates></Point></MultiGeometry></Placemark>
</Document></kml>'''
def kml():
    fc = im.read_kml(KML); f = fc["features"]
    assert [x["properties"]["name"] for x in f] == ["Host A", "Route", "Area", "Multi"]
    assert f[0]["geometry"]["coordinates"] == [2.5, 60.6]
    assert len(f[1]["geometry"]["coordinates"]) == 3 and f[1]["properties"]["dia"] == "10"
    assert len(f[2]["geometry"]["coordinates"]) == 2 and f[3]["geometry"]["type"] == "GeometryCollection"
S.check("KML point/line (multi-line text)/polygon+hole/multigeometry/extended data", kml)
S.check("KMZ", lambda: len(im.read_any("x.kmz", zipit({"doc.kml": KML}))["features"]) == 4)
# CSV
def csv_latlon():
    fc = im.read_any("wells.csv", b"Well name;Latitude;Longitude\nA-1;60,5;2,7\nA-2;60.51;2.71\nbad;x;y\n")
    assert len(fc["features"]) == 2 and fc["features"][0]["properties"]["_label"] == "A-1"
    assert fc["features"][0]["geometry"]["coordinates"] == [2.7, 60.5]
S.check("CSV lat/lon, semicolon, decimal comma, bad row skipped", csv_latlon)
def csv_utm():
    e, nn, _, _ = g.geo_to_utm(60.5, 2.7, 31, "ED50")
    fc = im.read_csv_points(f"name,easting,northing\nA,{e},{nn}\n".encode(), im.Crs("utm", "ED50", 31))
    lon, lat = fc["features"][0]["geometry"]["coordinates"]
    la_w, lo_w = g.transform_datum(60.5, 2.7, "ED50", "WGS84")
    assert abs(lat - la_w) < 1e-9 and abs(lon - lo_w) < 1e-9
S.check("CSV easting/northing ED50 UTM31", csv_utm)
S.raises("CSV E/N without UTM CRS raises", ValueError, lambda: im.read_csv_points(b"name,easting,northing\nA,450000,6700000\n"))
S.raises("unsupported extension raises", ValueError, lambda: im.read_any("x.dwg", b""))
# layout export
FIX = yaml.safe_load(open("test_fixtures/demo_field_a_tieback.yaml"))
def export():
    lay = n.Layout.from_dict(copy.deepcopy(FIX), c.Catalog())
    fc = im.layout_to_geojson(lay)
    assert len(fc["features"]) == 18
    fl = [f for f in fc["features"] if f["properties"]["id"] == "FL1"][0]
    assert fl["geometry"]["coordinates"][1] == [2.60, 60.55] and fl["properties"]["design_length_m"] > 14000
    rt = im.read_geojson(json.dumps(fc).encode())
    assert len(rt["features"]) == 18
S.check("layout -> GeoJSON (lon/lat order, lengths) and re-import", export)
S.check("point_features incl. MultiPoint",
        lambda: len(im.point_features({"features": [{"geometry": {"type": "MultiPoint", "coordinates": [[1, 2], [3, 4]]}, "properties": {}}]})) == 2)

# ── loose shapefile parts (.shp uploaded next to .dbf / .prj, no zip) ───────
def parts_of(prefix="wells"):
    e, nn, _, _ = g.geo_to_utm(60.5, 2.7, 31, "ED50")
    return {f"{prefix}.shp": shp_file(1, [rec_point(e, nn), rec_point(e + 1000, nn)]),
            f"{prefix}.dbf": dbf_file([("NAME", "C", 10)], [("A-1",), ("A-2",)]),
            f"{prefix}.prj": PRJ_ED50_31.encode()}


def loose_parts():
    f = parts_of()
    fc = im.read_shapefile_parts(f["wells.shp"], f["wells.dbf"], f["wells.prj"])
    assert len(fc["features"]) == 2
    lon, lat = fc["features"][0]["geometry"]["coordinates"]
    la_w, lo_w = g.transform_datum(60.5, 2.7, "ED50", "WGS84")
    assert abs(lat - la_w) < 1e-9 and abs(lon - lo_w) < 1e-9, "not reprojected from the .prj"
    assert fc["features"][0]["properties"]["NAME"] == "A-1", "attributes lost"
    return True


S.check("shapefile from loose .shp/.dbf/.prj", loose_parts)
S.check("loose .shp alone, CRS given",
        lambda: len(im.read_shapefile_parts(parts_of()["wells.shp"],
                                            crs=im.Crs("utm", "ED50", 31))["features"]) == 2)
S.raises("loose .shp alone without a CRS raises", ValueError,
         lambda: im.read_shapefile_parts(parts_of()["wells.shp"]))
S.check("read_any accepts a bare .shp with a CRS",
        lambda: len(im.read_any("wells.shp", parts_of()["wells.shp"],
                                im.Crs("utm", "ED50", 31))["features"]) == 2)
S.check("zip reader still works through the shared parts reader",
        lambda: len(im.read_any("wells.zip", zipit({k: v for k, v in parts_of().items()}))["features"]) == 2)


def grouping():
    names = ["wells.shp", "wells.dbf", "wells.prj", "wells.shx", "blocks.geojson", "notes.txt"]
    primary, parts = im.group_uploads(names)
    assert primary == ["wells.shp", "blocks.geojson", "notes.txt"], primary
    assert parts["wells.shp"][".dbf"] == "wells.dbf" and parts["wells.shp"][".prj"] == "wells.prj"
    return True


S.check("multi-file selection groups shapefile sidecars by stem", grouping)
S.check("sidecar without its .shp is dropped",
        lambda: im.group_uploads(["orphan.dbf", "a.geojson"])[0] == ["a.geojson"])
S.check("sidecar matching is case-insensitive",
        lambda: im.group_uploads(["W.SHP", "w.dbf"])[1]["W.SHP"][".dbf"] == "w.dbf")
S.check("two shapefiles in one selection stay separate",
        lambda: len(im.group_uploads(["a.shp", "a.prj", "b.shp", "b.prj"])[0]) == 2)


def read_many():
    files = dict(parts_of())
    files["pts.geojson"] = json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2.7, 60.5]}, "properties": {}}]}).encode()
    out = im.read_uploads(files)
    assert [nm for nm, _ in out] == ["wells.shp", "pts.geojson"], [nm for nm, _ in out]
    assert len(out[0][1]["features"]) == 2 and len(out[1][1]["features"]) == 1
    return True


S.check("read_uploads reads a mixed selection in one go", read_many)
S.raises("read_uploads reports the file that failed", ValueError,
         lambda: im.read_uploads({"bad.shp": b"\x00" * 120, "bad.prj": PRJ_WGS.encode()}))

sys.exit(0 if S.report() else 1)
