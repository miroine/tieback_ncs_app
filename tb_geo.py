"""
tb_geo.py — Geodesy engine for TieBack Studio (no pyproj dependency).

Scope
-----
* Ellipsoids: WGS84, International 1924 (ED50).
* Transverse Mercator / UTM forward + inverse using the Krüger n-series to
  6th order (Karney 2011) — sub-millimetre inside a UTM zone.
* ED50 <-> WGS84 datum shift via 7-parameter Helmert (position-vector
  convention). Default parameters are EPSG:1133 (3-parameter, ~10 m accuracy).
  Replace with a region-specific transformation for survey-grade work.
* Vincenty inverse geodesic (distance + azimuths) on any ellipsoid.
* Route (polyline) length with a configurable routing allowance.

Units: metres, decimal degrees. Engine is pure-python + math; no UI imports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

# ─────────────────────────────── ellipsoids ────────────────────────────────


@dataclass(frozen=True)
class Ellipsoid:
    name: str
    a: float        # semi-major axis [m]
    inv_f: float    # inverse flattening

    @property
    def f(self) -> float:
        return 1.0 / self.inv_f

    @property
    def b(self) -> float:
        return self.a * (1.0 - self.f)

    @property
    def e2(self) -> float:
        return self.f * (2.0 - self.f)


WGS84 = Ellipsoid("WGS84", 6378137.0, 298.257223563)
INTL1924 = Ellipsoid("International 1924", 6378388.0, 297.0)


@dataclass(frozen=True)
class Datum:
    name: str
    ellipsoid: Ellipsoid
    # Helmert to WGS84, position-vector convention:
    # tx,ty,tz [m], rx,ry,rz [arc-seconds], ds [ppm]
    to_wgs84: tuple = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


DATUM_WGS84 = Datum("WGS84", WGS84)
DATUM_ED50 = Datum("ED50", INTL1924, (-87.0, -98.0, -121.0, 0.0, 0.0, 0.0, 0.0))
DATUMS = {"WGS84": DATUM_WGS84, "ED50": DATUM_ED50}

K0_UTM = 0.9996
FALSE_EASTING = 500000.0
FALSE_NORTHING_S = 10000000.0


# ────────────────────────── Krüger series (Karney) ─────────────────────────


def _alpha_beta(ell: Ellipsoid):
    n = ell.f / (2.0 - ell.f)
    n2, n3, n4, n5, n6 = n**2, n**3, n**4, n**5, n**6
    A = ell.a / (1.0 + n) * (1.0 + n2 / 4.0 + n4 / 64.0 + n6 / 256.0)
    alpha = (
        n / 2 - 2 * n2 / 3 + 5 * n3 / 16 + 41 * n4 / 180 - 127 * n5 / 288 + 7891 * n6 / 37800,
        13 * n2 / 48 - 3 * n3 / 5 + 557 * n4 / 1440 + 281 * n5 / 630 - 1983433 * n6 / 1935360,
        61 * n3 / 240 - 103 * n4 / 140 + 15061 * n5 / 26880 + 167603 * n6 / 181440,
        49561 * n4 / 161280 - 179 * n5 / 168 + 6601661 * n6 / 7257600,
        34729 * n5 / 80640 - 3418889 * n6 / 1995840,
        212378941 * n6 / 319334400,
    )
    beta = (
        n / 2 - 2 * n2 / 3 + 37 * n3 / 96 - n4 / 360 - 81 * n5 / 512 + 96199 * n6 / 604800,
        n2 / 48 + n3 / 15 - 437 * n4 / 1440 + 46 * n5 / 105 - 1118711 * n6 / 3870720,
        17 * n3 / 480 - 37 * n4 / 840 - 209 * n5 / 4480 + 5569 * n6 / 90720,
        4397 * n4 / 161280 - 11 * n5 / 504 - 830251 * n6 / 7257600,
        4583 * n5 / 161280 - 108847 * n6 / 3991680,
        20648693 * n6 / 638668800,
    )
    return A, alpha, beta


def tm_forward(lat: float, lon: float, lon0: float, ell: Ellipsoid = WGS84,
               k0: float = K0_UTM, fe: float = FALSE_EASTING, fn: float = 0.0):
    """Geographic (deg) -> Transverse Mercator easting/northing (m)."""
    A, alpha, _ = _alpha_beta(ell)
    e = math.sqrt(ell.e2)
    phi = math.radians(lat)
    lam = math.radians(lon - lon0)
    s = math.sin(phi)
    t = math.sinh(math.atanh(s) - e * math.atanh(e * s))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.atanh(math.sin(lam) / math.sqrt(1.0 + t * t))
    xi, eta = xi_p, eta_p
    for j, a_j in enumerate(alpha, start=1):
        xi += a_j * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p)
        eta += a_j * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p)
    return fe + k0 * A * eta, fn + k0 * A * xi


def tm_inverse(easting: float, northing: float, lon0: float, ell: Ellipsoid = WGS84,
               k0: float = K0_UTM, fe: float = FALSE_EASTING, fn: float = 0.0):
    """Transverse Mercator easting/northing (m) -> geographic (deg)."""
    A, _, beta = _alpha_beta(ell)
    e = math.sqrt(ell.e2)
    xi = (northing - fn) / (k0 * A)
    eta = (easting - fe) / (k0 * A)
    xi_p, eta_p = xi, eta
    for j, b_j in enumerate(beta, start=1):
        xi_p -= b_j * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b_j * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    tau_p = math.sin(xi_p) / math.sqrt(math.sinh(eta_p) ** 2 + math.cos(xi_p) ** 2)
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    tau = tau_p
    for _ in range(10):
        sig = math.sinh(e * math.atanh(e * tau / math.sqrt(1 + tau * tau)))
        tau_i = tau * math.sqrt(1 + sig * sig) - sig * math.sqrt(1 + tau * tau)
        d_tau = ((tau_p - tau_i) / math.sqrt(1 + tau_i * tau_i)
                 * (1 + (1 - ell.e2) * tau * tau) / ((1 - ell.e2) * math.sqrt(1 + tau * tau)))
        tau += d_tau
        if abs(d_tau) < 1e-14:
            break
    return math.degrees(math.atan(tau)), lon0 + math.degrees(lam)


# ────────────────────────────────── UTM ────────────────────────────────────


def utm_zone_for(lat: float, lon: float) -> int:
    """Standard UTM zone incl. the Norway (32V) and Svalbard exceptions."""
    zone = int((lon + 180.0) // 6.0) + 1
    if 56.0 <= lat < 64.0 and 3.0 <= lon < 12.0:
        zone = 32
    if 72.0 <= lat < 84.0:
        if 0.0 <= lon < 9.0:
            zone = 31
        elif 9.0 <= lon < 21.0:
            zone = 33
        elif 21.0 <= lon < 33.0:
            zone = 35
        elif 33.0 <= lon < 42.0:
            zone = 37
    return max(1, min(60, zone))


def central_meridian(zone: int) -> float:
    return -183.0 + 6.0 * zone


def geo_to_utm(lat: float, lon: float, zone: int | None = None,
               datum: str = "WGS84"):
    """Returns (easting, northing, zone, hemisphere)."""
    ell = DATUMS[datum].ellipsoid
    z = zone if zone is not None else utm_zone_for(lat, lon)
    fn = 0.0 if lat >= 0 else FALSE_NORTHING_S
    e, n = tm_forward(lat, lon, central_meridian(z), ell, fn=fn)
    return e, n, z, ("N" if lat >= 0 else "S")


def utm_to_geo(easting: float, northing: float, zone: int,
               hemisphere: str = "N", datum: str = "WGS84"):
    ell = DATUMS[datum].ellipsoid
    fn = 0.0 if hemisphere.upper() == "N" else FALSE_NORTHING_S
    return tm_inverse(easting, northing, central_meridian(zone), ell, fn=fn)


# ─────────────────────────────── datum shift ───────────────────────────────


def geo_to_ecef(lat, lon, h, ell: Ellipsoid):
    phi, lam = math.radians(lat), math.radians(lon)
    N = ell.a / math.sqrt(1 - ell.e2 * math.sin(phi) ** 2)
    x = (N + h) * math.cos(phi) * math.cos(lam)
    y = (N + h) * math.cos(phi) * math.sin(lam)
    z = (N * (1 - ell.e2) + h) * math.sin(phi)
    return x, y, z


def ecef_to_geo(x, y, z, ell: Ellipsoid):
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    phi = math.atan2(z, p * (1 - ell.e2))
    for _ in range(10):
        N = ell.a / math.sqrt(1 - ell.e2 * math.sin(phi) ** 2)
        h = p / math.cos(phi) - N
        phi_new = math.atan2(z, p * (1 - ell.e2 * N / (N + h)))
        if abs(phi_new - phi) < 1e-15:
            phi = phi_new
            break
        phi = phi_new
    N = ell.a / math.sqrt(1 - ell.e2 * math.sin(phi) ** 2)
    h = p / math.cos(phi) - N
    return math.degrees(phi), math.degrees(lon), h


def _helmert(x, y, z, params, inverse=False):
    tx, ty, tz, rx, ry, rz, ds = params
    sec = math.pi / (180.0 * 3600.0)
    rx, ry, rz = rx * sec, ry * sec, rz * sec
    s = ds * 1e-6
    if inverse:
        tx, ty, tz, rx, ry, rz, s = -tx, -ty, -tz, -rx, -ry, -rz, -s
    x2 = tx + (1 + s) * (x - rz * y + ry * z)
    y2 = ty + (1 + s) * (rz * x + y - rx * z)
    z2 = tz + (1 + s) * (-ry * x + rx * y + z)
    return x2, y2, z2


def transform_datum(lat, lon, src: str, dst: str, h: float = 0.0):
    """Geographic transform between named datums via WGS84 hub."""
    if src == dst:
        return lat, lon
    ds, dd = DATUMS[src], DATUMS[dst]
    x, y, z = geo_to_ecef(lat, lon, h, ds.ellipsoid)
    if ds.name != "WGS84":
        x, y, z = _helmert(x, y, z, ds.to_wgs84)
    if dd.name != "WGS84":
        x, y, z = _helmert(x, y, z, dd.to_wgs84, inverse=True)
    la, lo, _ = ecef_to_geo(x, y, z, dd.ellipsoid)
    return la, lo


# ───────────────────────────── Vincenty inverse ────────────────────────────


def vincenty_inverse(lat1, lon1, lat2, lon2, ell: Ellipsoid = WGS84,
                     tol: float = 1e-12, max_iter: int = 500):
    """Returns (distance_m, azimuth1_deg, azimuth2_deg)."""
    if lat1 == lat2 and lon1 == lon2:
        return 0.0, 0.0, 0.0
    a, b, f = ell.a, ell.b, ell.f
    L = math.radians(lon2 - lon1)
    U1 = math.atan((1 - f) * math.tan(math.radians(lat1)))
    U2 = math.atan((1 - f) * math.tan(math.radians(lat2)))
    sinU1, cosU1, sinU2, cosU2 = math.sin(U1), math.cos(U1), math.sin(U2), math.cos(U2)
    lam = L
    for _ in range(max_iter):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sig = math.hypot(cosU2 * sin_lam, cosU1 * sinU2 - sinU1 * cosU2 * cos_lam)
        if sin_sig == 0:
            return 0.0, 0.0, 0.0
        cos_sig = sinU1 * sinU2 + cosU1 * cosU2 * cos_lam
        sig = math.atan2(sin_sig, cos_sig)
        sin_alpha = cosU1 * cosU2 * sin_lam / sin_sig
        cos2_alpha = 1 - sin_alpha ** 2
        cos2sm = cos_sig - 2 * sinU1 * sinU2 / cos2_alpha if cos2_alpha != 0 else 0.0
        C = f / 16 * cos2_alpha * (4 + f * (4 - 3 * cos2_alpha))
        lam_prev = lam
        lam = L + (1 - C) * f * sin_alpha * (
            sig + C * sin_sig * (cos2sm + C * cos_sig * (-1 + 2 * cos2sm ** 2)))
        if abs(lam - lam_prev) < tol:
            break
    else:
        raise ValueError("Vincenty inverse failed to converge (near-antipodal points)")
    u2 = cos2_alpha * (a * a - b * b) / (b * b)
    A = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    B = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    d_sig = B * sin_sig * (cos2sm + B / 4 * (
        cos_sig * (-1 + 2 * cos2sm ** 2)
        - B / 6 * cos2sm * (-3 + 4 * sin_sig ** 2) * (-3 + 4 * cos2sm ** 2)))
    s = b * A * (sig - d_sig)
    az1 = math.degrees(math.atan2(cosU2 * math.sin(lam), cosU1 * sinU2 - sinU1 * cosU2 * math.cos(lam)))
    az2 = math.degrees(math.atan2(cosU1 * math.sin(lam), -sinU1 * cosU2 + cosU1 * sinU2 * math.cos(lam)))
    return s, az1 % 360.0, az2 % 360.0


def geodesic_distance(lat1, lon1, lat2, lon2, datum: str = "WGS84") -> float:
    return vincenty_inverse(lat1, lon1, lat2, lon2, DATUMS[datum].ellipsoid)[0]


def polyline_length(vertices: Sequence[tuple[float, float]], datum: str = "WGS84") -> float:
    """Sum of geodesic segment lengths for [(lat, lon), ...]."""
    total = 0.0
    for (la1, lo1), (la2, lo2) in zip(vertices[:-1], vertices[1:]):
        total += geodesic_distance(la1, lo1, la2, lo2, datum)
    return total


def route_length(vertices: Sequence[tuple[float, float]], allowance_frac: float = 0.03,
                 end_allowance_m: float = 0.0, datum: str = "WGS84") -> float:
    """Design length = geodesic polyline × (1 + allowance) + fixed end allowance.

    allowance_frac covers seabed undulation, lay tolerance and route
    deviations (typical 2–5 %); end_allowance_m covers tie-in spools/overlength.
    """
    if len(vertices) < 2:
        return 0.0
    if allowance_frac < 0 or end_allowance_m < 0:
        raise ValueError("allowances must be non-negative")
    return polyline_length(vertices, datum) * (1.0 + allowance_frac) + end_allowance_m


def dms_to_deg(d: float, m: float = 0.0, s: float = 0.0) -> float:
    sign = -1.0 if d < 0 or (d == 0 and (m < 0 or s < 0)) else 1.0
    return sign * (abs(d) + abs(m) / 60.0 + abs(s) / 3600.0)
