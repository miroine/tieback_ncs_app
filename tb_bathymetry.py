"""
tb_bathymetry.py — EMODnet Bathymetry access for TieBack Studio.

* Map layers: the component draws the EMODnet WMS (`mean_multicolour`, `contours`);
  the constants here document the same service for reference and for reports.
* Depth sampling: `rest.emodnet-bathymetry.eu/depth_sample` returns DTM statistics for
  one grid cell, `/depth_profile` a section along a LineString. The DTM is ~115 m
  (1/16 arc-minute) and referenced to LAT, so it is survey-indicative only: use the
  project bathymetry survey for design.

Transport-agnostic: pass anything with `.get(url, params=..., timeout=...)`.
Depths are returned positive-down in metres; EMODnet reports elevation with land
positive, so values are negated and land (elevation > 0) is reported as None.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

WMS_URL = "https://ows.emodnet-bathymetry.eu/wms"
WMS_LAYERS = {"colour": "mean_multicolour", "atlas": "mean_atlas_land", "contours": "contours"}
REST_URL = "https://rest.emodnet-bathymetry.eu"
ATTRIBUTION = "Bathymetry © EMODnet Bathymetry Consortium (DTM 2022, ~115 m grid, LAT)"


def _depth_from_payload(d: dict) -> Optional[float]:
    """EMODnet returns mean/min/max/smoothed elevations (negative below sea level)."""
    for key in ("smoothed", "avg", "mean"):
        v = d.get(key)
        if isinstance(v, (int, float)):
            return -float(v) if v < 0 else None
    return None


def depth_at(lat: float, lon: float, session, timeout: float = 20.0) -> Optional[float]:
    """Water depth (m, positive down) at one point; None over land or with no data."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordinates out of range")
    r = session.get(f"{REST_URL}/depth_sample",
                    params={"geom": f"POINT({lon:.6f} {lat:.6f})"}, timeout=timeout)
    r.raise_for_status()
    return _depth_from_payload(r.json() or {})


def depths_at(points: Sequence[Tuple[str, float, float]], session, timeout: float = 20.0) -> Dict[str, Optional[float]]:
    """Depth for several (id, lat, lon) points. Failures come back as None, not exceptions."""
    out: Dict[str, Optional[float]] = {}
    for pid, lat, lon in points:
        try:
            out[pid] = depth_at(lat, lon, session, timeout)
        except Exception:      # noqa: BLE001 — one bad point must not stop the batch
            out[pid] = None
    return out


def depth_profile(vertices: Sequence[Tuple[float, float]], session, timeout: float = 30.0) -> List[dict]:
    """Depth section along a route of (lat, lon) vertices."""
    if len(vertices) < 2:
        raise ValueError("need at least two vertices")
    wkt = ",".join(f"{lon:.6f} {lat:.6f}" for lat, lon in vertices)
    r = session.get(f"{REST_URL}/depth_profile", params={"geom": f"LINESTRING({wkt})"}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else data.get("profile") or data.get("samples") or []
    out = []
    for i, rec in enumerate(rows):
        if not isinstance(rec, dict):
            continue
        d = _depth_from_payload(rec)
        out.append(dict(index=i, depth_m=d, lat=rec.get("lat"), lon=rec.get("lon"),
                        distance_m=rec.get("distance")))
    return out


def resample_profile(rows: List[dict], n: int = 40) -> List[float]:
    """Even-spaced depth samples (m, positive down) from a depth_profile result.

    Gaps (land or no data) are filled by linear interpolation between the
    neighbouring valid samples so the routing model always has a value.
    """
    depths = [r.get("depth_m") for r in rows]
    valid = [(i, d) for i, d in enumerate(depths) if d is not None]
    if not valid:
        return []
    out = []
    for k in range(n):
        x = k * (len(depths) - 1) / max(n - 1, 1)
        lo = max([i for i, _ in valid if i <= x], default=valid[0][0])
        hi = min([i for i, _ in valid if i >= x], default=valid[-1][0])
        d_lo = dict(valid)[lo]
        d_hi = dict(valid)[hi]
        out.append(d_lo if hi == lo else d_lo + (d_hi - d_lo) * (x - lo) / (hi - lo))
    return out


def fetch_route_profiles(layout, session, samples: int = 40, categories=("flowline", "utility_line"),
                         timeout: float = 30.0) -> Dict[str, Optional[int]]:
    """Sample the seabed along each line's as-laid route and store it on the edge.

    Saved as edge.attrs["seabed_profile"] = [depth_m, ...] evenly spaced from the
    upstream to the downstream end, so it travels with the project file.
    Returns {edge_id: number of samples stored, or None on failure}.
    """
    import tb_map
    out: Dict[str, Optional[int]] = {}
    for e in layout.edges.values():
        if layout.catalog.get(e.item_id).category not in categories:
            continue
        pts = [tb_map.to_display(layout, la, lo) for la, lo in layout.edge_shape(e)]
        if len(pts) < 2:
            out[e.edge_id] = None
            continue
        try:
            prof = resample_profile(depth_profile(pts, session, timeout), samples)
        except Exception:      # noqa: BLE001 — one bad line must not stop the batch
            prof = []
        if prof:
            e.attrs["seabed_profile"] = [round(d, 1) for d in prof]
            out[e.edge_id] = len(prof)
        else:
            out[e.edge_id] = None
    return out


def free_spans(depths: List[float], dx_m: float, gap_m: float = 0.5,
               max_span_m: float = 400.0) -> List[dict]:
    """Screening free-span check using a taut-string (upper convex hull) model.

    The pipe is treated as a weightless string laid over the seabed: it touches
    at the hull points and spans the gaps between them. Bending stiffness,
    submerged weight and lateral routing are not modelled, so this flags
    candidates for survey and span analysis, not span lengths for design.
    Stretches longer than `max_span_m` are reported but truncated in length,
    since a real pipe touches down in a long valley.
    """
    n = len(depths)
    if n < 3 or dx_m <= 0:
        return []
    pts = [(i * dx_m, -float(d)) for i, d in enumerate(depths)]     # elevation, up positive
    hull = []                                                       # monotone chain, upper hull
    for p in pts:
        while len(hull) >= 2:
            (x1, z1), (x2, z2) = hull[-2], hull[-1]
            cross = (x2 - x1) * (p[1] - z1) - (z2 - z1) * (p[0] - x1)
            if cross > 0:           # middle point lies below the chord → pipe spans over it
                hull.pop()
            else:
                break
        hull.append(p)
    spans = []
    for (x0, z0), (x1, z1) in zip(hull[:-1], hull[1:]):
        i0, i1 = int(round(x0 / dx_m)), int(round(x1 / dx_m))
        if i1 - i0 < 2:
            continue
        max_gap = max(z0 + (z1 - z0) * (k - i0) / (i1 - i0) - pts[k][1] for k in range(i0 + 1, i1))
        if max_gap >= gap_m:
            spans.append(dict(start_m=x0, end_m=x1, length_m=min(x1 - x0, max_span_m),
                              max_gap_m=round(max_gap, 2), truncated=(x1 - x0) > max_span_m))
    return spans


def fill_node_depths(layout, session, only_blank: bool = True, timeout: float = 20.0) -> Dict[str, Optional[float]]:
    """Set water_depth_m on subsea nodes from the DTM. Hosts are left alone.

    Returns {node_id: depth or None}; nodes that returned no data keep their old value.
    """
    import tb_map
    targets = []
    for n in layout.nodes.values():
        if layout.kind(n.node_id) == "host":
            continue
        if only_blank and n.water_depth_m > 0:
            continue
        lat, lon = tb_map.to_display(layout, n.lat, n.lon)     # EMODnet is WGS84
        targets.append((n.node_id, lat, lon))
    got = depths_at(targets, session, timeout)
    for nid, d in got.items():
        if d is not None:
            layout.nodes[nid].water_depth_m = round(d, 1)
    return got
