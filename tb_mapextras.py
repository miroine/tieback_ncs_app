"""
tb_mapextras.py — Sketches, bookmarks and map layers added by URL.

Three things that sit on the map but are not subsea equipment:

* **Sketches** — circles, polygons and lines drawn on the map: a 500 m safety
  zone round a template, an anchor pattern, a trawl or exclusion area, a
  corridor. They are saved with the layout, so they travel with the project,
  its concepts and a shared link. Measurements are geodesic.

* **Bookmarks** — named map views, to come back to the same framing or to show
  a colleague exactly what you were looking at.

* **Map layers by URL** — paste an address from ArcGIS Online / ArcGIS Server,
  a WMS, or an XYZ tile service and it becomes a base map or an overlay. The
  address is recognised by its shape, not guessed: an unknown shape is refused
  with the forms that are accepted.

Nothing here needs Streamlit.
"""
from __future__ import annotations

import math
import re
import uuid
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

R_EARTH = 6_371_008.8
SKETCH_KINDS = ("circle", "polygon", "line")
SKETCH_COLORS = ["#C4561B", "#7D4EBF", "#0F8A3C", "#2E86AB", "#B3801A", "#A8326E"]


# ─────────────────────────────── geometry ──────────────────────────────────

def haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    r = math.pi / 180
    dlat, dlon = (b[0] - a[0]) * r, (b[1] - a[1]) * r
    h = math.sin(dlat / 2) ** 2 + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin(dlon / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def path_length_m(coords) -> float:
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def ring_area_m2(ring) -> float:
    """Spherical polygon area (Chamberlain & Duquette), m². Matches core.js."""
    if not ring or len(ring) < 3:
        return 0.0
    r = math.pi / 180
    s = 0.0
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        s += (b[1] - a[1]) * r * (2 + math.sin(a[0] * r) + math.sin(b[0] * r))
    return abs(s * R_EARTH * R_EARTH / 2)


def destination(lat: float, lon: float, bearing_deg: float, dist_m: float) -> Tuple[float, float]:
    r = math.pi / 180
    d, b, p1, l1 = dist_m / R_EARTH, bearing_deg * r, lat * r, lon * r
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return p2 / r, ((l2 / r + 540) % 360) - 180


def circle_ring(center, radius_m: float, n: int = 72) -> List[Tuple[float, float]]:
    return [destination(center[0], center[1], 360.0 * i / n, radius_m) for i in range(n)]


# ─────────────────────────────── sketches ──────────────────────────────────

def _valid_coord(c) -> bool:
    try:
        return -90 <= float(c[0]) <= 90 and -180 <= float(c[1]) <= 180
    except (TypeError, ValueError, IndexError):
        return False


def make_sketch(kind: str, coords=None, center=None, radius_m: float = 0.0, label: str = "",
                color: Optional[str] = None, existing: int = 0) -> dict:
    """A validated sketch record (lat/lon WGS84 — the map's datum)."""
    if kind not in SKETCH_KINDS:
        raise ValueError(f"unknown sketch kind '{kind}'")
    rec = {"id": "SK" + uuid.uuid4().hex[:8], "kind": kind, "label": str(label or "").strip(),
           "color": color or SKETCH_COLORS[existing % len(SKETCH_COLORS)]}
    if kind == "circle":
        if center is None or not _valid_coord(center):
            raise ValueError("a circle needs a centre")
        if not (0 < float(radius_m) <= 500_000):
            raise ValueError("circle radius must be between 0 and 500 km")
        rec.update(center=[float(center[0]), float(center[1])], radius_m=float(radius_m))
    else:
        pts = [[float(c[0]), float(c[1])] for c in (coords or []) if _valid_coord(c)]
        need = 3 if kind == "polygon" else 2
        if len(pts) < need:
            raise ValueError(f"a {kind} needs at least {need} points")
        rec["coords"] = pts
    return rec


def measure(sk: dict) -> Dict[str, float]:
    """Length, area and radius of one sketch, in m / m²."""
    k = sk.get("kind")
    if k == "circle":
        r = float(sk["radius_m"])
        return {"radius_m": r, "perimeter_m": 2 * math.pi * r,
                "area_m2": ring_area_m2(circle_ring(sk["center"], r, 360))}
    if k == "polygon":
        c = sk["coords"]
        return {"perimeter_m": path_length_m(c + [c[0]]), "area_m2": ring_area_m2(c)}
    if k == "line":
        return {"length_m": path_length_m(sk["coords"])}
    return {}


def describe(sk: dict) -> str:
    m = measure(sk)
    if sk["kind"] == "circle":
        return f"circle r = {fmt_len(m['radius_m'])}, {fmt_area(m['area_m2'])}"
    if sk["kind"] == "polygon":
        return f"polygon {fmt_area(m['area_m2'])}, perimeter {fmt_len(m['perimeter_m'])}"
    return f"line {fmt_len(m['length_m'])}"


def fmt_len(m: float) -> str:
    return f"{m / 1000:,.2f} km" if m >= 1000 else f"{m:,.0f} m"


def fmt_area(m2: float) -> str:
    if m2 >= 1e6:
        return f"{m2 / 1e6:,.2f} km²"
    if m2 >= 1e4:
        return f"{m2 / 1e4:,.1f} ha"
    return f"{m2:,.0f} m²"


def sketches(layout) -> List[dict]:
    return list(getattr(layout, "annotations", None) or [])


def add_sketch(layout, sk: dict) -> dict:
    if getattr(layout, "annotations", None) is None:
        layout.annotations = []
    layout.annotations.append(sk)
    return sk


def remove_sketch(layout, sketch_id: str) -> bool:
    before = len(sketches(layout))
    layout.annotations = [s for s in sketches(layout) if s.get("id") != sketch_id]
    return len(layout.annotations) < before


def update_sketch(layout, sketch_id: str, **changes) -> dict:
    for s in sketches(layout):
        if s.get("id") == sketch_id:
            if "label" in changes:
                s["label"] = str(changes["label"] or "").strip()
            if "color" in changes and re.fullmatch(r"#[0-9A-Fa-f]{6}", str(changes["color"] or "")):
                s["color"] = changes["color"]
            if "radius_m" in changes and s["kind"] == "circle":
                r = float(changes["radius_m"])
                if not (0 < r <= 500_000):
                    raise ValueError("circle radius must be between 0 and 500 km")
                s["radius_m"] = r
            return s
    raise KeyError(f"no sketch {sketch_id}")


def circle_around(layout_node, radius_m: float, label: str = "", to_display=None) -> dict:
    """A circle centred on an item — e.g. a 500 m safety zone round a template."""
    lat, lon = layout_node.lat, layout_node.lon
    if to_display is not None:
        lat, lon = to_display(lat, lon)
    return make_sketch("circle", center=(lat, lon), radius_m=radius_m,
                       label=label or f"{fmt_len(radius_m)} round {layout_node.label or layout_node.node_id}")


def sketches_geojson(layout) -> dict:
    """Sketches as GeoJSON (circles as 72-sided polygons), lon/lat order."""
    feats = []
    for s in sketches(layout):
        if s["kind"] == "circle":
            ring = circle_ring(s["center"], s["radius_m"])
            geom = {"type": "Polygon", "coordinates": [[[lo, la] for la, lo in ring + ring[:1]]]}
        elif s["kind"] == "polygon":
            c = s["coords"]
            geom = {"type": "Polygon", "coordinates": [[[lo, la] for la, lo in c + c[:1]]]}
        else:
            geom = {"type": "LineString", "coordinates": [[lo, la] for la, lo in s["coords"]]}
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": {"id": s["id"], "kind": s["kind"], "label": s.get("label", ""),
                                     "measure": describe(s), **measure(s)}})
    return {"type": "FeatureCollection", "features": feats}


def items_inside(layout, sk: dict, to_display=None) -> List[str]:
    """Equipment inside a circle or polygon — who sits in the safety zone."""
    out = []
    for n in layout.nodes.values():
        la, lo = (to_display(n.lat, n.lon) if to_display else (n.lat, n.lon))
        if sk["kind"] == "circle":
            if haversine_m(sk["center"], (la, lo)) <= sk["radius_m"]:
                out.append(n.node_id)
        elif sk["kind"] == "polygon" and _point_in_ring((la, lo), sk["coords"]):
            out.append(n.node_id)
    return out


def _point_in_ring(p, ring) -> bool:
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if ((yi > p[0]) != (yj > p[0])) and (p[1] < (xj - xi) * (p[0] - yi) / (yj - yi + 1e-300) + xi):
            inside = not inside
        j = i
    return inside


# ─────────────────────────────── bookmarks ─────────────────────────────────

def make_bookmark(view, name: str = "", basemap: str = "", existing: int = 0) -> dict:
    """A named view. `view` is [west, south, east, north] in WGS84."""
    if not view or len(view) != 4:
        raise ValueError("a bookmark needs a view [west, south, east, north]")
    w, s, e, n = (float(v) for v in view)
    if not (w < e and s < n and -180 <= w and e <= 180 and -90 <= s and n <= 90):
        raise ValueError("invalid view")
    return {"id": "BM" + uuid.uuid4().hex[:8], "name": str(name or "").strip() or f"View {existing + 1}",
            "view": [w, s, e, n], "basemap": basemap}


# ─────────────────────────── map layers by URL ─────────────────────────────

ACCEPTED_FORMS = (
    "ArcGIS tiled service: …/MapServer/tile/{z}/{y}/{x}",
    "ArcGIS map or image service: …/MapServer or …/ImageServer (drawn with export)",
    "ArcGIS feature layer: …/MapServer/<n> or …/FeatureServer/<n> (features fetched as a vector layer)",
    "WMS: an address containing service=WMS or ending /wms, with a layers= parameter or a layer name",
    "XYZ tiles: an address with {z}, {x} and {y}",
)

_ARCGIS_LAYER = re.compile(r"/(MapServer|FeatureServer)/(\d+)/?$", re.I)
_ARCGIS_SERVICE = re.compile(r"/(MapServer|ImageServer)/?$", re.I)


def classify_url(url: str, name: str = "", as_overlay: Optional[bool] = None,
                 wms_layers: str = "", opacity: float = 1.0) -> dict:
    """Turn a pasted address into a layer spec for the map, or raise with the accepted forms.

    Returns {kind, url, name, overlay, opacity, [layers], [layer_url]} where kind is
    one of 'xyz', 'arcgis_tile', 'arcgis_export', 'wms', 'arcgis_features'.
    """
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("paste an address")
    if not raw.lower().startswith(("https://", "http://")):
        raise ValueError("the address must start with https://")
    if raw.lower().startswith("http://"):
        # the app is served over https — a browser blocks http tiles as mixed content
        raise ValueError("http:// map services are blocked by the browser on an https page — "
                         "use the https:// address of the same service")
    u = urlparse(raw)
    path, q = u.path, parse_qs(u.query)
    qlow = {k.lower(): v for k, v in q.items()}
    base = f"{u.scheme}://{u.netloc}{u.path}"
    host = u.netloc
    title = str(name or "").strip()
    op = max(0.05, min(1.0, float(opacity)))

    if "{z}" in raw and "{x}" in raw and "{y}" in raw:
        tiled_arcgis = "/tile/" in path.lower()
        return {"kind": "arcgis_tile" if tiled_arcgis else "xyz", "url": raw,
                "name": title or _name_from(path, host),
                "overlay": bool(as_overlay) if as_overlay is not None else False, "opacity": op}

    if m := re.search(r"/MapServer/tile/?$", path, re.I):
        return {"kind": "arcgis_tile", "url": base.rstrip("/") + "/{z}/{y}/{x}",
                "name": title or _name_from(path, host),
                "overlay": bool(as_overlay) if as_overlay is not None else False, "opacity": op}

    if ("service" in qlow and qlow["service"][0].lower() == "wms") or path.lower().rstrip("/").endswith("/wms") \
            or "wmsserver" in path.lower():
        layers = wms_layers or (qlow.get("layers", [""])[0])
        if not layers:
            raise ValueError("a WMS needs a layer name — add layers=<name> to the address or fill in "
                             "'WMS layers'")
        return {"kind": "wms", "url": base, "layers": layers,
                "name": title or f"{layers} ({host})",
                "overlay": True if as_overlay is None else bool(as_overlay), "opacity": op}

    if m := _ARCGIS_LAYER.search(path):
        return {"kind": "arcgis_features", "url": base.rstrip("/"), "layer_url": base.rstrip("/"),
                "name": title or f"{_name_from(path, host)} layer {m.group(2)}",
                "overlay": True, "opacity": op}

    if _ARCGIS_SERVICE.search(path) or re.search(r"/(MapServer|ImageServer)/export(Image)?/?$", path, re.I):
        root = re.sub(r"/export(Image)?/?$", "", base, flags=re.I).rstrip("/")
        return {"kind": "arcgis_export", "url": root, "layers": wms_layers or "",
                "name": title or _name_from(path, host),
                "overlay": bool(as_overlay) if as_overlay is not None else True, "opacity": op}

    raise ValueError("not a map address this app recognises. Accepted:\n- " + "\n- ".join(ACCEPTED_FORMS))


def _name_from(path: str, host: str) -> str:
    parts = [p for p in path.split("/") if p]
    for i, p in enumerate(parts):
        if p.lower() in ("mapserver", "featureserver", "imageserver") and i > 0:
            return parts[i - 1].replace("_", " ")
    return host


def fetch_arcgis_features(layer_url: str, bbox, session, max_features: int = 5000,
                          timeout: float = 30.0) -> dict:
    """Features of any ArcGIS MapServer/FeatureServer layer inside bbox, as GeoJSON.

    Reuses the Sodir client's paging and Esri-JSON fallback, which is what makes
    it work against services that ignore f=geojson.
    """
    import tb_ncs
    url = layer_url.rstrip("/") + "/query"
    feats: List[dict] = []
    offset, truncated, fmt = 0, False, "geojson"
    while True:
        r = session.get(url, params=tb_ncs.query_params(bbox, offset, 0.0, fmt), timeout=timeout)
        r.raise_for_status()
        payload = r.json()
        if fmt == "geojson" and isinstance(payload, dict) and "error" in payload:
            fmt = "json"
            r = session.get(url, params=tb_ncs.query_params(bbox, offset, 0.0, fmt), timeout=timeout)
            r.raise_for_status()
            payload = r.json()
        if isinstance(payload, dict) and "error" in payload:
            raise ValueError(f"the service refused the query: {payload['error'].get('message', payload['error'])}")
        fc, exceeded = tb_ncs.to_feature_collection(payload)
        page = [f for f in fc["features"] if f.get("geometry")]
        for f in page:
            props = dict(f.get("properties") or {})
            props.setdefault("_label", next((str(props[k]) for k in ("name", "NAME", "Name", "navn", "title")
                                             if props.get(k)), ""))
            feats.append({"type": "Feature", "geometry": f["geometry"], "properties": props})
            if len(feats) >= max_features:
                truncated = True
                break
        if truncated or not exceeded or not page:
            break
        offset += len(page)
    return {"type": "FeatureCollection", "features": feats, "truncated": truncated}


# Ready-made base maps and overlays the map offers without pasting anything.
EXTRA_BASEMAPS = ("Ocean (Esri)", "Satellite (Esri)", "Topographic (Esri)", "Light grey (Esri)",
                  "Dark grey (Esri)", "OpenStreetMap", "Sjøkart (Kartverket)", "Topografisk (Kartverket)",
                  "Gråtone (Kartverket)")
