"""
tb_ncs.py — Sodir (Norwegian Offshore Directorate) FactMaps layer client.

Service: Factmaps/FactMapsWGS84 MapServer (EPSG:4326). Sodir states the WGS84
service is transformed from ED50 using ESRI ED_1950_To_WGS_1984_18. Layer IDs
verified against the service directory in Sept 2026; `discover_layers()` can
re-resolve them by name if the service is re-published.

The client is transport-agnostic: pass any object with a
`get(url, params=..., timeout=...)` returning `.json()` / `.raise_for_status()`
(requests.Session in the app, a fake in tests). No Streamlit imports.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

SERVICE_URL = "https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/MapServer"
PAGE_SIZE = 1000  # service MaxRecordCount
NCS_BBOX = (-1.0, 55.5, 37.0, 82.0)   # whole Norwegian Continental Shelf, lon/lat

# Sodir picture-fill classes (hatched in FactMaps) mapped to pattern ids the map component draws
PICTURE_FILL_PATTERNS = {"OIL/GAS": "oil_gas", "GAS/CONDENSATE": "gas_condensate"}


@dataclass(frozen=True)
class NcsLayer:
    key: str
    layer_id: int
    title: str
    geometry: str          # point | line | polygon
    label_fields: Tuple[str, ...]
    color: str
    name_pattern: str      # regex for discover_layers()
    default_on: bool = False


NCS_LAYERS: Dict[str, NcsLayer] = {l.key: l for l in (
    NcsLayer("fields", 502, "Fields", "polygon", ("fldName", "FIELDNAME", "fieldName"), "#9DBA00",
             r"^field by status$", True),
    NcsLayer("discoveries", 503, "Discoveries (active, by HC type)", "polygon",
             ("dscName", "DISCNAME", "discName"), "#E9A23B", r"^discovery, active"),
    NcsLayer("discoveries_all", 504, "Discoveries (all, by main HC type)", "polygon",
             ("dscName", "DISCNAME", "discName"), "#E9A23B", r"^discovery, all"),
    NcsLayer("facilities", 304, "Facilities in place", "point", ("fclName", "FACNAME"), "#EB0037",
             r"^facilities, in place$", True),
    NcsLayer("pipelines", 311, "Pipelines", "line", ("pipName", "PIPENAME"), "#00243D",
             r"^pipelines$", True),
    NcsLayer("dev_wells", 205, "Development wellbores", "point", ("wlbWellboreName", "WELLBORENAME"),
             "#243746", r"^development wellbores$"),
    NcsLayer("expl_wells", 204, "Exploration wellbores", "point", ("wlbWellboreName", "WELLBORENAME"),
             "#6F6F6F", r"^exploration wellbores$"),
    NcsLayer("licences", 616, "Production licences (current)", "polygon", ("prlName", "PRLNAME"),
             "#7D4EBF", r"^production licence, all - current with geometry$"),
    NcsLayer("blocks", 802, "Blocks", "polygon", ("blkName", "BLOCKNAME", "blkLabel"), "#A8B4BE",
             r"^blocks$"),
    NcsLayer("quadrants", 803, "Quadrants", "polygon", ("qadName",), "#C3CDD5", r"^quadrants$"),
    NcsLayer("structural", 704, "Structural elements", "polygon", ("strName", "NAME"), "#B08D57",
             r"^structural elements$"),
)}


def bbox_around(points: Sequence[Tuple[float, float]], buffer_km: float = 30.0):
    """(xmin, ymin, xmax, ymax) lon/lat bbox around (lat, lon) points with a km buffer."""
    import math
    if not points:
        raise ValueError("no points for bbox")
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    dlat = buffer_km / 111.32
    mid = math.radians((min(lats) + max(lats)) / 2)
    dlon = buffer_km / (111.32 * max(math.cos(mid), 0.05))
    return (max(min(lons) - dlon, -180.0), max(min(lats) - dlat, -90.0),
            min(max(lons) + dlon, 180.0), min(max(lats) + dlat, 90.0))


def query_params(bbox: Tuple[float, float, float, float], offset: int = 0,
                 simplify_deg: float = 0.0005, fmt: str = "geojson") -> dict:
    xmin, ymin, xmax, ymax = bbox
    if not (xmin < xmax and ymin < ymax):
        raise ValueError("invalid bbox")
    p = {
        "where": "1=1",
        "geometry": f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "geometryPrecision": 6,
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        "f": fmt,
    }
    if simplify_deg > 0:
        p["maxAllowableOffset"] = simplify_deg
    return p


# ── Esri JSON -> GeoJSON (fallback when the server ignores f=geojson) ──────

def _ring_area(ring):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring[:-1], ring[1:])) / 2.0


def esri_geometry_to_geojson(g: Optional[dict]) -> Optional[dict]:
    if not g:
        return None
    if "x" in g and "y" in g:
        if g["x"] is None or g["y"] is None:
            return None
        return {"type": "Point", "coordinates": [g["x"], g["y"]]}
    if "points" in g:
        return {"type": "MultiPoint", "coordinates": [p[:2] for p in g["points"]]}
    if "paths" in g:
        paths = [[p[:2] for p in path] for path in g["paths"]]
        if len(paths) == 1:
            return {"type": "LineString", "coordinates": paths[0]}
        return {"type": "MultiLineString", "coordinates": paths}
    if "rings" in g:
        # Esri: outer rings clockwise (negative signed area), holes counter-clockwise
        polys: List[list] = []
        for ring in g["rings"]:
            r = [p[:2] for p in ring]
            if _ring_area(r) <= 0 or not polys:
                polys.append([r])
            else:
                polys[-1].append(r)
        if len(polys) == 1:
            return {"type": "Polygon", "coordinates": polys[0]}
        return {"type": "MultiPolygon", "coordinates": polys}
    return None


def to_feature_collection(payload: dict) -> Tuple[dict, bool]:
    """Normalise a query response (GeoJSON or Esri JSON). Returns (fc, exceeded)."""
    if "error" in payload:
        err = payload["error"]
        raise RuntimeError(f"FactMaps error {err.get('code')}: {err.get('message')}")
    exceeded = bool(payload.get("exceededTransferLimit")
                    or payload.get("properties", {}).get("exceededTransferLimit"))
    if payload.get("type") == "FeatureCollection":
        return {"type": "FeatureCollection", "features": payload.get("features", [])}, exceeded
    feats = []
    for f in payload.get("features", []):
        geom = esri_geometry_to_geojson(f.get("geometry"))
        feats.append({"type": "Feature", "geometry": geom, "properties": f.get("attributes", {})})
    return {"type": "FeatureCollection", "features": feats}, exceeded


def _round_coords(c, nd):
    if isinstance(c, (int, float)):
        return round(float(c), nd)
    return [_round_coords(x, nd) for x in c]


def label_for(props: dict, layer: NcsLayer) -> str:
    for f in layer.label_fields:
        if props.get(f) not in (None, ""):
            return str(props[f])
    lower = {k.lower(): v for k, v in props.items()}
    for f in layer.label_fields:
        v = lower.get(f.lower())
        if v not in (None, ""):
            return str(v)
    for k, v in props.items():
        if re.search(r"name$", k, re.I) and v not in (None, ""):
            return str(v)
    return ""


def fetch_layer(key: str, bbox, session, max_features: int = 5000, simplify_deg: float = 0.0005,
                decimals: int = 6, timeout: float = 30.0, layer_id: Optional[int] = None,
                use_renderer: bool = True) -> dict:
    """Fetch one FactMaps layer inside bbox, paginating past MaxRecordCount.

    Returns a FeatureCollection with slim properties: _label, _layer, plus the
    original attributes. `truncated` is set when max_features was hit.
    """
    layer = NCS_LAYERS[key]
    lid = layer.layer_id if layer_id is None else layer_id
    url = f"{SERVICE_URL}/{lid}/query"
    features: List[dict] = []
    offset, truncated = 0, False
    while True:
        resp = session.get(url, params=query_params(bbox, offset, simplify_deg), timeout=timeout)
        resp.raise_for_status()
        fc, exceeded = to_feature_collection(resp.json())
        page = fc["features"]
        for f in page:
            if not f.get("geometry"):
                continue
            props = dict(f.get("properties") or {})
            props["_label"] = label_for(props, layer)
            props["_layer"] = key
            f = {"type": "Feature", "properties": props,
                 "geometry": {**f["geometry"],
                              "coordinates": _round_coords(f["geometry"]["coordinates"], decimals)}}
            features.append(f)
            if len(features) >= max_features:
                truncated = True
                break
        if truncated or not exceeded or not page:
            break
        offset += len(page)
    fc = {"type": "FeatureCollection", "features": features,
          "layer": key, "title": layer.title, "color": layer.color,
          "geometry": layer.geometry, "truncated": truncated}
    if use_renderer and features:
        try:
            fc["renderer"] = fetch_renderer(key, session, timeout, lid)
            apply_renderer(fc, fc["renderer"], layer.color)
        except Exception:      # noqa: BLE001 — symbology is optional; keep the data
            fc["renderer"] = None
    return fc


def parse_renderer(layer_json: dict) -> dict:
    """Read a layer's ArcGIS drawingInfo so overlays match FactMaps symbology.

    Returns {"field": <attribute>, "values": {value: {fill, outline, pattern}},
             "default": {...} | None}. Picture-fill classes (Sodir hatches oil/gas and
    gas/condensate) carry a `pattern` key instead of a solid colour.
    """
    r = (layer_json.get("drawingInfo") or {}).get("renderer") or {}

    def sym_style(sym, value=None):
        if not isinstance(sym, dict):
            return None
        col = sym.get("color")
        out = ((sym.get("outline") or {}).get("color")) or [130, 130, 130, 255]
        style = {"outline": rgba_hex(out)}
        if col:
            style["fill"] = rgba_hex(col)
        pat = PICTURE_FILL_PATTERNS.get(str(value).upper()) if value is not None else None
        if sym.get("type") in ("esriPFS", "esriPMS") or (col is None and pat):
            style["pattern"] = pat or "oil_gas"
            style.setdefault("fill", "#9E9E9E")
        return style

    out = {"field": r.get("field1"), "values": {}, "default": sym_style(r.get("defaultSymbol"))}
    for info in r.get("uniqueValueInfos", []) or []:
        st = sym_style(info.get("symbol"), info.get("value"))
        if st:
            out["values"][str(info.get("value"))] = st
    if r.get("type") == "simple":
        out["default"] = sym_style(r.get("symbol")) or out["default"]
    return out


def rgba_hex(c) -> str:
    if not c:
        return "#808080"
    r, g, b = int(c[0]), int(c[1]), int(c[2])
    return f"#{r:02X}{g:02X}{b:02X}"


def fetch_renderer(key: str, session, timeout: float = 30.0, layer_id=None) -> dict:
    lid = NCS_LAYERS[key].layer_id if layer_id is None else layer_id
    resp = session.get(f"{SERVICE_URL}/{lid}", params={"f": "json"}, timeout=timeout)
    resp.raise_for_status()
    return parse_renderer(resp.json())


def apply_renderer(fc: dict, renderer: dict, fallback_color: str) -> dict:
    """Tag each feature with _fill / _outline / _pattern from the service renderer."""
    field = (renderer or {}).get("field")
    for f in fc.get("features", []):
        props = f["properties"]
        st = None
        if field:
            val = props.get(field)
            if val is None:
                lower = {k.lower(): v for k, v in props.items()}
                val = lower.get(str(field).lower())
            st = (renderer.get("values") or {}).get(str(val))
        st = st or (renderer or {}).get("default") or {}
        props["_fill"] = st.get("fill", fallback_color)
        props["_outline"] = st.get("outline", "#828282")
        if st.get("pattern"):
            props["_pattern"] = st["pattern"]
    return fc


def discover_layers(service_json: dict) -> Dict[str, int]:
    """Resolve layer IDs by name from `{SERVICE_URL}?f=json` (if IDs change)."""
    found: Dict[str, int] = {}
    for lyr in service_json.get("layers", []):
        name = str(lyr.get("name", "")).strip().lower()
        for key, spec in NCS_LAYERS.items():
            if key not in found and re.search(spec.name_pattern, name, re.I):
                found[key] = int(lyr["id"])
    return found
