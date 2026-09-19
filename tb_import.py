"""
tb_import.py — Import user map layers and export the layout as GeoJSON.

Supported inputs (all converted to WGS84 lon/lat GeoJSON FeatureCollections):
* GeoJSON (.geojson/.json), incl. legacy `crs` member (EPSG codes / URNs)
* KML / KMZ (Point, LineString, Polygon, MultiGeometry)
* ESRI shapefile as .zip (.shp + .dbf [+ .prj]) — pure-python reader:
  Point, PolyLine, Polygon, MultiPoint and their Z/M variants
* CSV of points: lat/lon columns, or easting/northing (+ zone, datum)

CRS support: WGS84/ED50 geographic, WGS84/ED50 UTM (EPSG 326zz / 230zz),
ETRS89 (treated as WGS84, < 1 m on the NCS). Unknown CRS → error (never guess).
"""
from __future__ import annotations

import csv
import io
import json
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import tb_geo


@dataclass(frozen=True)
class Crs:
    kind: str          # geographic | utm
    datum: str = "WGS84"
    zone: int = 0
    hemisphere: str = "N"

    def label(self) -> str:
        return f"{self.datum} geographic" if self.kind == "geographic" else \
            f"{self.datum} UTM {self.zone}{self.hemisphere}"


WGS84_GEO = Crs("geographic", "WGS84")


def crs_from_epsg(code: int) -> Crs:
    code = int(code)
    if code in (4326, 4258, 4979, 4936):          # WGS84, ETRS89
        return WGS84_GEO
    if code == 4230:
        return Crs("geographic", "ED50")
    if 32601 <= code <= 32660:
        return Crs("utm", "WGS84", code - 32600, "N")
    if 32701 <= code <= 32760:
        return Crs("utm", "WGS84", code - 32700, "S")
    if 23028 <= code <= 23038:
        return Crs("utm", "ED50", code - 23000, "N")
    if 25828 <= code <= 25838:                    # ETRS89 / UTM
        return Crs("utm", "WGS84", code - 25800, "N")
    raise ValueError(f"unsupported EPSG:{code}")


def crs_from_text(text: str) -> Crs:
    """Parse EPSG strings/URNs ('EPSG:23031', 'urn:ogc:def:crs:EPSG::4326', 'CRS84') or WKT (.prj)."""
    t = text.strip()
    if re.search(r"CRS84", t, re.I):
        return WGS84_GEO
    m = re.search(r"EPSG[:\s]*:{0,2}\s*(\d{4,5})\s*$", t, re.I)
    if m and not t.upper().startswith(("PROJCS", "GEOGCS", "PROJCRS", "GEOGCRS")):
        return crs_from_epsg(int(m.group(1)))
    up = t.upper()
    if up.startswith(("PROJCS", "GEOGCS", "PROJCRS", "GEOGCRS")):
        if re.search(r"ED[_ ]?1950|ED50|EUROPEAN[_ ]DATUM[_ ]1950", up):
            datum = "ED50"
        elif re.search(r"WGS[_ ]?(19)?84|ETRS|ETRF|GRS[_ ]?1980", up):
            datum = "WGS84"
        else:
            raise ValueError("unsupported datum in WKT")
        if up.startswith(("PROJCS", "PROJCRS")):
            z = re.search(r"UTM[_ ]ZONE[_ ](\d{1,2})([NS])?", up)
            if not z:
                raise ValueError("projected WKT is not UTM")
            return Crs("utm", datum, int(z.group(1)), z.group(2) or "N")
        return Crs("geographic", datum)
    raise ValueError(f"cannot parse CRS '{t[:60]}'")


def to_wgs84_lonlat(x: float, y: float, crs: Crs) -> Tuple[float, float]:
    if crs.kind == "utm":
        lat, lon = tb_geo.utm_to_geo(x, y, crs.zone, crs.hemisphere, crs.datum)
    else:
        lon, lat = x, y
    if crs.datum != "WGS84":
        lat, lon = tb_geo.transform_datum(lat, lon, crs.datum, "WGS84")
    return lon, lat


def _map_coords(c, crs: Crs):
    if crs == WGS84_GEO:
        return c
    if c and isinstance(c[0], (int, float)):
        lon, lat = to_wgs84_lonlat(c[0], c[1], crs)
        return [lon, lat]
    return [_map_coords(x, crs) for x in c]


def reproject_fc(fc: dict, crs: Crs) -> dict:
    out = []
    for f in fc.get("features", []):
        g = f.get("geometry")
        if g is None:
            continue
        if g["type"] == "GeometryCollection":
            geoms = [{**gg, "coordinates": _map_coords(gg["coordinates"], crs)} for gg in g["geometries"]]
            g2 = {"type": "GeometryCollection", "geometries": geoms}
        else:
            g2 = {**g, "coordinates": _map_coords(g["coordinates"], crs)}
        out.append({"type": "Feature", "geometry": g2, "properties": dict(f.get("properties") or {})})
    return {"type": "FeatureCollection", "features": out}


def _check_lonlat(fc: dict):
    def walk(c):
        if c and isinstance(c[0], (int, float)):
            if not (-180 <= c[0] <= 180 and -90 <= c[1] <= 90):
                raise ValueError("coordinates are not lon/lat — specify the source CRS (e.g. UTM zone/datum)")
        else:
            for x in c:
                walk(x)
    for f in fc["features"]:
        g = f["geometry"]
        for gg in (g["geometries"] if g["type"] == "GeometryCollection" else [g]):
            walk(gg["coordinates"])


# ─────────────────────────────────── GeoJSON ───────────────────────────────

def read_geojson(data: bytes, crs: Optional[Crs] = None) -> dict:
    obj = json.loads(data.decode("utf-8-sig"))
    if obj.get("type") == "Feature":
        obj = {"type": "FeatureCollection", "features": [obj]}
    elif obj.get("type") in ("Point", "LineString", "Polygon", "MultiPoint", "MultiLineString",
                              "MultiPolygon", "GeometryCollection"):
        obj = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": obj, "properties": {}}]}
    if obj.get("type") != "FeatureCollection":
        raise ValueError("not a GeoJSON FeatureCollection/Feature/Geometry")
    src = crs
    if src is None and isinstance(obj.get("crs"), dict):
        name = obj["crs"].get("properties", {}).get("name", "")
        src = crs_from_text(name)
    fc = reproject_fc(obj, src or WGS84_GEO)
    _check_lonlat(fc)
    return fc


# ───────────────────────────────────── KML ─────────────────────────────────

def _kml_coords(text: str):
    pts = []
    for tok in text.split():
        parts = tok.split(",")
        if len(parts) >= 2:
            pts.append([float(parts[0]), float(parts[1])])
    return pts


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _kml_geoms(el) -> List[dict]:
    out = []
    for child in el:
        name = _local(child.tag)
        if name == "Point":
            c = next((x for x in child.iter() if _local(x.tag) == "coordinates"), None)
            if c is not None and c.text:
                out.append({"type": "Point", "coordinates": _kml_coords(c.text)[0]})
        elif name == "LineString":
            c = next((x for x in child.iter() if _local(x.tag) == "coordinates"), None)
            if c is not None and c.text:
                out.append({"type": "LineString", "coordinates": _kml_coords(c.text)})
        elif name == "Polygon":
            rings = []
            for bnd in child:
                for c in bnd.iter():
                    if _local(c.tag) == "coordinates" and c.text:
                        rings.append((_local(bnd.tag), _kml_coords(c.text)))
            outer = [r for b, r in rings if b == "outerBoundaryIs"]
            inner = [r for b, r in rings if b == "innerBoundaryIs"]
            if outer:
                out.append({"type": "Polygon", "coordinates": outer[:1] + inner})
        elif name == "MultiGeometry":
            out.extend(_kml_geoms(child))
    return out


def read_kml(data: bytes) -> dict:
    root = ET.fromstring(data)
    feats = []
    for pm in root.iter():
        if _local(pm.tag) != "Placemark":
            continue
        props = {}
        for child in pm:
            n = _local(child.tag)
            if n in ("name", "description") and child.text:
                props[n] = child.text.strip()
            if n == "ExtendedData":
                for d in child.iter():
                    if _local(d.tag) == "Data" and d.get("name"):
                        v = next((x.text for x in d if _local(x.tag) == "value"), None)
                        props[d.get("name")] = v
        geoms = _kml_geoms(pm)
        if not geoms:
            continue
        geom = geoms[0] if len(geoms) == 1 else {"type": "GeometryCollection", "geometries": geoms}
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    fc = {"type": "FeatureCollection", "features": feats}
    _check_lonlat(fc)
    return fc


def read_kmz(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
        if name is None:
            raise ValueError("KMZ contains no .kml")
        return read_kml(z.read(name))


# ──────────────────────────────── Shapefile ────────────────────────────────

SHP_TYPES = {1: "Point", 3: "PolyLine", 5: "Polygon", 8: "MultiPoint",
             11: "Point", 13: "PolyLine", 15: "Polygon", 18: "MultiPoint",
             21: "Point", 23: "PolyLine", 25: "Polygon", 28: "MultiPoint"}


def _read_dbf(data: bytes) -> List[dict]:
    if len(data) < 32:
        return []
    n_rec = struct.unpack("<I", data[4:8])[0]
    hdr_len, rec_len = struct.unpack("<HH", data[8:12])
    fields = []
    pos = 32
    while pos < hdr_len - 1 and data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\x00")[0].decode("latin-1").strip()
        ftype = chr(data[pos + 11])
        flen = data[pos + 16]
        fdec = data[pos + 17]
        fields.append((name, ftype, flen, fdec))
        pos += 32
    rows = []
    for i in range(n_rec):
        off = hdr_len + i * rec_len
        rec = data[off:off + rec_len]
        if not rec or rec[:1] == b"*":
            rows.append(None)
            continue
        p, row = 1, {}
        for name, ftype, flen, fdec in fields:
            raw = rec[p:p + flen].decode("latin-1").strip()
            p += flen
            if ftype in "NF":
                try:
                    row[name] = (float(raw) if (fdec or "." in raw) else int(raw)) if raw else None
                except ValueError:
                    row[name] = None
            elif ftype == "L":
                row[name] = raw.upper() in ("Y", "T") if raw else None
            else:
                row[name] = raw
        rows.append(row)
    return rows


def _split_parts(points, parts):
    bounds = list(parts) + [len(points)]
    return [points[bounds[i]:bounds[i + 1]] for i in range(len(parts))]


def _read_shp(data: bytes) -> List[Optional[dict]]:
    if len(data) < 100 or struct.unpack(">i", data[0:4])[0] != 9994:
        raise ValueError("not a shapefile (.shp magic mismatch)")
    file_len = struct.unpack(">i", data[24:28])[0] * 2
    pos, geoms = 100, []
    while pos + 8 <= min(file_len, len(data)):
        content_len = struct.unpack(">i", data[pos + 4:pos + 8])[0] * 2
        rec = data[pos + 8:pos + 8 + content_len]
        pos += 8 + content_len
        stype = struct.unpack("<i", rec[0:4])[0]
        if stype == 0:
            geoms.append(None)
            continue
        kind = SHP_TYPES.get(stype)
        if kind is None:
            raise ValueError(f"unsupported shape type {stype}")
        if kind == "Point":
            x, y = struct.unpack("<dd", rec[4:20])
            geoms.append({"type": "Point", "coordinates": [x, y]})
        elif kind == "MultiPoint":
            n = struct.unpack("<i", rec[36:40])[0]
            pts = [list(struct.unpack("<dd", rec[40 + 16 * i:56 + 16 * i])) for i in range(n)]
            geoms.append({"type": "MultiPoint", "coordinates": pts})
        else:
            n_parts, n_pts = struct.unpack("<ii", rec[36:44])
            parts = struct.unpack(f"<{n_parts}i", rec[44:44 + 4 * n_parts])
            base = 44 + 4 * n_parts
            pts = [list(struct.unpack("<dd", rec[base + 16 * i:base + 16 + 16 * i])) for i in range(n_pts)]
            rings = _split_parts(pts, parts)
            if kind == "PolyLine":
                geoms.append({"type": "LineString", "coordinates": rings[0]} if len(rings) == 1
                             else {"type": "MultiLineString", "coordinates": rings})
            else:
                polys: List[list] = []
                for r in rings:  # shapefile: outer rings clockwise (negative signed area)
                    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r[:-1], r[1:])) / 2
                    if area <= 0 or not polys:
                        polys.append([r])
                    else:
                        polys[-1].append(r)
                geoms.append({"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
                             else {"type": "MultiPolygon", "coordinates": polys})
    return geoms


def read_shapefile_parts(shp: bytes, dbf: Optional[bytes] = None, prj: Optional[bytes] = None,
                         crs: Optional[Crs] = None) -> dict:
    """A shapefile from its loose parts: .shp is required, .dbf and .prj optional.

    Without a .prj the CRS must be given — a shapefile carries no CRS of its own
    and guessing one would put the layer kilometres from where it belongs.
    """
    geoms = _read_shp(shp)
    rows = _read_dbf(dbf) if dbf else []
    src = crs
    if src is None:
        if prj is None:
            raise ValueError("shapefile has no .prj — upload it alongside the .shp "
                             "or choose the source CRS")
        src = crs_from_text(prj.decode("latin-1"))
    feats = []
    for i, g in enumerate(geoms):
        row = rows[i] if i < len(rows) else {}
        if g is None or row is None:
            continue
        feats.append({"type": "Feature", "geometry": g, "properties": row})
    fc = reproject_fc({"type": "FeatureCollection", "features": feats}, src)
    _check_lonlat(fc)
    return fc


def read_shapefile_zip(data: bytes, crs: Optional[Crs] = None) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shp = next((n for n in names if n.lower().endswith(".shp")), None)
        if shp is None:
            raise ValueError("zip contains no .shp")
        stem = shp[:-4]
        find = lambda ext: next((n for n in names if n.lower() == (stem + ext).lower()), None)  # noqa: E731
        dbf, prj = find(".dbf"), find(".prj")
        return read_shapefile_parts(z.read(shp), z.read(dbf) if dbf else None,
                                    z.read(prj) if prj else None, crs)


# ───────────────────────────────────── CSV ─────────────────────────────────

LAT_PATTERNS = (r"^lat(itude)?$", r"^y_?wgs", r"lat")
LON_PATTERNS = (r"^lon(g|gitude)?$", r"^x_?wgs", r"lon")
EAST_PATTERNS = (r"^east(ing)?$", r"^e$", r"^x$", r"east")
NORTH_PATTERNS = (r"^north(ing)?$", r"^n$", r"^y$", r"north")
NAME_PATTERNS = (r"^name$", r"^label$", r"^id$", r"well", r"name")


def _find_col(cols, patterns):
    for p in patterns:
        for c in cols:
            if re.search(p, c.strip(), re.I):
                return c
    return None


def read_csv_points(data: bytes, crs: Optional[Crs] = None) -> dict:
    text = data.decode("utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        raise ValueError("CSV is empty")
    cols = list(rows[0].keys())
    lat_c, lon_c = _find_col(cols, LAT_PATTERNS), _find_col(cols, LON_PATTERNS)
    e_c, n_c = _find_col(cols, EAST_PATTERNS), _find_col(cols, NORTH_PATTERNS)
    name_c = _find_col(cols, NAME_PATTERNS)
    use_geo = lat_c and lon_c and (crs is None or crs.kind == "geographic")
    if not use_geo and not (e_c and n_c):
        raise ValueError("CSV needs lat/lon or easting/northing columns")
    if not use_geo and (crs is None or crs.kind != "utm"):
        raise ValueError("easting/northing CSV requires a UTM CRS (zone + datum)")
    src = crs or WGS84_GEO
    feats = []
    for r in rows:
        try:
            x = float(str(r[lon_c if use_geo else e_c]).replace(",", "."))
            y = float(str(r[lat_c if use_geo else n_c]).replace(",", "."))
        except (TypeError, ValueError):
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]},
                      "properties": {**r, "_label": r.get(name_c, "") if name_c else ""}})
    fc = reproject_fc({"type": "FeatureCollection", "features": feats}, src)
    _check_lonlat(fc)
    return fc


def read_any(filename: str, data: bytes, crs: Optional[Crs] = None) -> dict:
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext in ("geojson", "json"):
        return read_geojson(data, crs)
    if ext == "kml":
        return read_kml(data)
    if ext == "kmz":
        return read_kmz(data)
    if ext == "zip":
        return read_shapefile_zip(data, crs)
    if ext == "shp":
        return read_shapefile_parts(data, None, None, crs)
    if ext in ("csv", "txt"):
        return read_csv_points(data, crs)
    raise ValueError(f"unsupported file type .{ext}")


SIDECARS = (".dbf", ".prj", ".shx", ".cpg", ".sbn", ".sbx", ".qix", ".idx")


def as_upload_list(value) -> list:
    """Whatever a file uploader hands back, as a list.

    Streamlit returns a list when `accept_multiple_files` is on and a single
    object when it is off, and test harnesses do not always mirror that. Taking
    the returned value at its word and iterating it turns a harness mismatch into
    a TypeError in the middle of the app.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None]
    return [value]


def group_uploads(names: List[str]) -> Tuple[List[str], Dict[str, Dict[str, str]]]:
    """Split a multi-file selection into layers to read and shapefile sidecars.

    Returns (primary filenames, {shp filename: {".dbf": name, ".prj": name}}). A
    .dbf or .prj on its own is a sidecar with no shapefile and is simply dropped.
    """
    lower = {n: n.lower() for n in names}
    shps = [n for n in names if lower[n].endswith(".shp")]
    parts: Dict[str, Dict[str, str]] = {}
    claimed = set()
    for shp in shps:
        stem = lower[shp][:-4]
        found = {}
        for n in names:
            for ext in SIDECARS:
                if lower[n] == stem + ext:
                    found[ext] = n
                    claimed.add(n)
        parts[shp] = found
    primary = [n for n in names
               if n not in claimed and not lower[n].endswith(SIDECARS)]
    return primary, parts


def read_uploads(files: Dict[str, bytes], crs: Optional[Crs] = None) -> List[Tuple[str, dict]]:
    """Read a whole multi-file selection. Shapefile parts are matched by stem, so
    selecting `blocks.shp`, `blocks.dbf` and `blocks.prj` together loads one layer.

    Returns [(name, FeatureCollection), …]; raises on the first file that fails.
    """
    primary, parts = group_uploads(list(files))
    out: List[Tuple[str, dict]] = []
    for name in primary:
        if name.lower().endswith(".shp"):
            side = parts.get(name, {})
            out.append((name, read_shapefile_parts(
                files[name],
                files.get(side.get(".dbf", "")),
                files.get(side.get(".prj", "")), crs)))
        else:
            out.append((name, read_any(name, files[name], crs)))
    return out


# ─────────────────────────── layout <-> features ───────────────────────────

def point_features(fc: dict) -> List[Tuple[float, float, dict]]:
    out = []
    for f in fc["features"]:
        g = f["geometry"]
        props = f.get("properties") or {}
        if g["type"] == "Point":
            out.append((g["coordinates"][1], g["coordinates"][0], props))
        elif g["type"] == "MultiPoint":
            out.extend((c[1], c[0], props) for c in g["coordinates"])
    return out


def feature_label(props: dict) -> str:
    for k in ("_label", "name", "NAME", "Name", "label", "id", "ID"):
        if props.get(k) not in (None, ""):
            return str(props[k])
    return ""


def layout_to_geojson(layout) -> dict:
    feats = []
    for n in layout.nodes.values():
        it = layout.catalog.get(n.item_id)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [n.lon, n.lat]},
                      "properties": {"id": n.node_id, "label": n.label, "item_id": n.item_id,
                                     "item": it.name, "category": it.category, "phase": n.phase}})
    for e in layout.edges.values():
        it = layout.catalog.get(e.item_id)
        coords = [[lon, lat] for lat, lon in layout.edge_vertices(e)]
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                      "properties": {"id": e.edge_id, "label": e.label, "item_id": e.item_id,
                                     "item": it.name, "category": it.category,
                                     "from": e.from_node, "to": e.to_node, "diameter_in": e.diameter_in,
                                     "design_length_m": round(layout.edge_length(e), 1), "phase": e.phase}})
    return {"type": "FeatureCollection", "features": feats}
