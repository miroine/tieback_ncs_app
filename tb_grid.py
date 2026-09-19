"""
tb_grid.py — Regular grids (.grd and friends) as a map layer and a depth source.

A `.grd` file says nothing about what is inside it, so the reader sniffs the
content instead of trusting the extension. Supported:

* Surfer ASCII               ``DSAA``   (Golden Software)
* Surfer 6 binary            ``DSBB``
* Surfer 7 binary            ``DSRB``
* IRAP classic ASCII         first token ``-996``  (RMS / Petrel export, common on the NCS)
* ZMAP+ ASCII                ``@name, GRID, 5``    (Petrel / Landmark export)
* ESRI / ASCII grid          ``ncols … nrows …``   (also .asc)

Everything lands in one `Grid`: node-registered, axis-aligned in the source CRS,
row 0 at the south edge, column 0 at the west edge, `None` for blank nodes.

What a grid is for here:
* drawn on the map, as contours (vector) and/or a colour image overlay;
* sampled as a **depth source** in place of EMODnet — node water depths and
  seabed profiles along a route, so a surveyed seabed grid drives the free-span
  and cool-down screening instead of a 115 m regional DTM.

Sign convention: a seabed grid may be stored as depth (positive down) or as
elevation (negative below sea level). `depth_convention()` reports which one the
numbers look like; `depth_at()` always returns metres positive down, matching
tb_bathymetry.

Grids are assumed north-up. A rotated IRAP or Surfer 7 grid is rejected rather
than silently drawn in the wrong place.
"""
from __future__ import annotations

import base64
import math
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import tb_geo
import tb_import

# Blank/undefined markers used by the formats above.
SURFER_BLANK = 1.70141e38
IRAP_UNDEF = 9999900.0
BIG = 1e29           # anything this large is a null in every format we read


def _is_blank(v: float, nodata: Optional[float]) -> bool:
    if v is None:
        return True
    if not math.isfinite(v):
        return True
    if abs(v) >= BIG:
        return True
    if nodata is not None and abs(v - nodata) <= max(1e-6, abs(nodata) * 1e-9):
        return True
    return False


# ────────────────────────────────── the grid ───────────────────────────────

@dataclass
class Grid:
    """Node-registered regular grid, row 0 = south edge, column 0 = west edge."""
    nx: int
    ny: int
    x0: float            # coordinate of column 0 (grid node, not cell corner)
    y0: float            # coordinate of row 0
    dx: float
    dy: float
    z: List[List[Optional[float]]]      # z[row][col]
    crs: tb_import.Crs = tb_import.WGS84_GEO
    name: str = "grid"
    source_format: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.nx < 2 or self.ny < 2:
            raise ValueError("a grid needs at least 2 × 2 nodes")
        if self.dx <= 0 or self.dy <= 0:
            raise ValueError("grid increments must be positive")
        if len(self.z) != self.ny or any(len(r) != self.nx for r in self.z):
            raise ValueError(f"grid data is not {self.nx} × {self.ny}")

    # ── geometry ──
    @property
    def x1(self) -> float:
        return self.x0 + (self.nx - 1) * self.dx

    @property
    def y1(self) -> float:
        return self.y0 + (self.ny - 1) * self.dy

    def bbox(self) -> Tuple[float, float, float, float]:
        return self.x0, self.y0, self.x1, self.y1

    def cells(self) -> int:
        return self.nx * self.ny

    # ── values ──
    def finite(self) -> List[float]:
        return [v for row in self.z for v in row if v is not None]

    def stats(self) -> Dict[str, float]:
        vals = self.finite()
        n_blank = self.cells() - len(vals)
        if not vals:
            return {"n": self.cells(), "n_blank": n_blank, "min": float("nan"),
                    "max": float("nan"), "mean": float("nan")}
        return {"n": self.cells(), "n_blank": n_blank, "min": min(vals), "max": max(vals),
                "mean": sum(vals) / len(vals)}

    def value_at(self, x: float, y: float) -> Optional[float]:
        """Bilinear interpolation in grid coordinates. None outside, or on a blank cell."""
        if not (self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1):
            return None
        fx = (x - self.x0) / self.dx
        fy = (y - self.y0) / self.dy
        i = min(int(fx), self.nx - 2)
        j = min(int(fy), self.ny - 2)
        tx, ty = fx - i, fy - j
        c00, c10 = self.z[j][i], self.z[j][i + 1]
        c01, c11 = self.z[j + 1][i], self.z[j + 1][i + 1]
        if None in (c00, c10, c01, c11):
            return None
        return (c00 * (1 - tx) * (1 - ty) + c10 * tx * (1 - ty)
                + c01 * (1 - tx) * ty + c11 * tx * ty)

    # ── coordinates ──
    def to_grid_xy(self, lat: float, lon: float) -> Tuple[float, float]:
        la, lo = lat, lon
        if self.crs.datum != "WGS84":
            la, lo = tb_geo.transform_datum(lat, lon, "WGS84", self.crs.datum)
        if self.crs.kind == "utm":
            e, n, _, _ = tb_geo.geo_to_utm(la, lo, self.crs.zone, self.crs.datum)
            return e, n
        return lo, la

    def to_lonlat(self, x: float, y: float) -> Tuple[float, float]:
        return tb_import.to_wgs84_lonlat(x, y, self.crs)

    def lonlat_bbox(self, edge_samples: int = 24) -> Tuple[float, float, float, float]:
        """(west, south, east, north) in WGS84. Samples the edges — a UTM grid's
        boundary is curved in lat/lon, so the corners alone under-cover it."""
        lons, lats = [], []
        for k in range(edge_samples + 1):
            t = k / edge_samples
            for x, y in ((self.x0 + t * (self.x1 - self.x0), self.y0),
                         (self.x0 + t * (self.x1 - self.x0), self.y1),
                         (self.x0, self.y0 + t * (self.y1 - self.y0)),
                         (self.x1, self.y0 + t * (self.y1 - self.y0))):
                lon, lat = self.to_lonlat(x, y)
                lons.append(lon)
                lats.append(lat)
        return min(lons), min(lats), max(lons), max(lats)

    def value_at_lonlat(self, lat: float, lon: float) -> Optional[float]:
        x, y = self.to_grid_xy(lat, lon)
        return self.value_at(x, y)

    # ── depth semantics ──
    def depth_convention(self) -> str:
        """'positive_down' | 'elevation' | 'unknown' — judged on the sign of the data."""
        vals = self.finite()
        if not vals:
            return "unknown"
        pos = sum(1 for v in vals if v > 0)
        neg = len(vals) - pos
        if pos >= 0.95 * len(vals):
            return "positive_down"
        if neg >= 0.95 * len(vals):
            return "elevation"
        return "unknown"

    def depth_at(self, lat: float, lon: float, convention: Optional[str] = None) -> Optional[float]:
        """Water depth in metres, positive down. None off-grid, on a blank, or above sea level."""
        v = self.value_at_lonlat(lat, lon)
        if v is None:
            return None
        conv = convention or self.depth_convention()
        d = -v if conv == "elevation" else v
        return d if d > 0 else None

    # ── reshaping ──
    def transpose(self) -> "Grid":
        """Swap the row/column order. IRAP classic ASCII does not state which axis
        cycles fastest, so a transposed read is the one thing to try when an
        imported grid comes out with its axes swapped."""
        z = [[self.z[j][i] for j in range(self.ny)] for i in range(self.nx)]
        return Grid(self.ny, self.nx, self.x0, self.y0, self.dy, self.dx, z,
                    self.crs, self.name, self.source_format, dict(self.attrs))

    def decimate(self, max_cells: int = 250_000) -> "Grid":
        """Subsample to at most `max_cells` nodes, for drawing. Engineering
        sampling always uses the full grid."""
        if self.cells() <= max_cells:
            return self
        step = max(2, int(math.ceil(math.sqrt(self.cells() / max_cells))))
        rows = list(range(0, self.ny, step))
        cols = list(range(0, self.nx, step))
        if len(rows) < 2 or len(cols) < 2:
            return self
        z = [[self.z[j][i] for i in cols] for j in rows]
        return Grid(len(cols), len(rows), self.x0 + cols[0] * self.dx, self.y0 + rows[0] * self.dy,
                    self.dx * step, self.dy * step, z, self.crs, self.name,
                    self.source_format, dict(self.attrs))

    def describe(self) -> str:
        s = self.stats()
        unit = "m" if self.crs.kind == "utm" else "°"
        return (f"{self.nx} × {self.ny} nodes, {self.dx:g} × {self.dy:g} {unit} spacing, "
                f"z {s['min']:,.1f} to {s['max']:,.1f}, {s['n_blank']:,} blank, {self.crs.label()}")


# ──────────────────────────────── readers ──────────────────────────────────

def _numbers(text: str) -> List[float]:
    out = []
    for tok in text.replace(",", " ").split():
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def _rows_from_flat(vals: Sequence[float], nx: int, ny: int, nodata: Optional[float],
                    order: str) -> List[List[Optional[float]]]:
    """Fold a flat value list into rows, row 0 = south.

    order:
      'x_fast_south'  x cycles fastest, first row is the south edge (Surfer, IRAP)
      'x_fast_north'  x cycles fastest, first row is the north edge (ESRI ASCII)
      'y_fast_north'  y cycles fastest going south, columns west→east (ZMAP+)
    """
    need = nx * ny
    if len(vals) < need:
        raise ValueError(f"grid holds {len(vals):,} values but the header says {need:,}")
    vals = vals[:need]
    clean = [None if _is_blank(v, nodata) else float(v) for v in vals]
    if order == "y_fast_north":
        rows: List[List[Optional[float]]] = [[None] * nx for _ in range(ny)]
        k = 0
        for i in range(nx):                 # column by column, west → east
            for j in range(ny - 1, -1, -1):  # north → south inside a column
                rows[j][i] = clean[k]
                k += 1
        return rows
    rows = [clean[j * nx:(j + 1) * nx] for j in range(ny)]
    if order == "x_fast_north":
        rows.reverse()
    return rows


def read_surfer_ascii(data: bytes, crs: Optional[tb_import.Crs] = None) -> Grid:
    text = data.decode("latin-1")
    if not text.lstrip().upper().startswith("DSAA"):
        raise ValueError("not a Surfer ASCII grid (missing DSAA)")
    nums = _numbers(text.split("DSAA", 1)[1] if "DSAA" in text else text.split("dsaa", 1)[1])
    if len(nums) < 8:
        raise ValueError("truncated Surfer ASCII header")
    nx, ny = int(nums[0]), int(nums[1])
    xlo, xhi, ylo, yhi = nums[2:6]
    vals = nums[8:]
    dx = (xhi - xlo) / (nx - 1) if nx > 1 else 1.0
    dy = (yhi - ylo) / (ny - 1) if ny > 1 else 1.0
    z = _rows_from_flat(vals, nx, ny, SURFER_BLANK, "x_fast_south")
    return Grid(nx, ny, xlo, ylo, dx, dy, z, crs or tb_import.WGS84_GEO,
                source_format="Surfer ASCII (DSAA)")


def read_surfer6_binary(data: bytes, crs: Optional[tb_import.Crs] = None) -> Grid:
    if data[:4] != b"DSBB":
        raise ValueError("not a Surfer 6 binary grid (missing DSBB)")
    nx, ny = struct.unpack("<hh", data[4:8])
    xlo, xhi, ylo, yhi, _zlo, _zhi = struct.unpack("<6d", data[8:56])
    need = nx * ny
    raw = data[56:56 + 4 * need]
    if len(raw) < 4 * need:
        raise ValueError("truncated Surfer 6 binary grid")
    vals = list(struct.unpack(f"<{need}f", raw))
    dx = (xhi - xlo) / (nx - 1) if nx > 1 else 1.0
    dy = (yhi - ylo) / (ny - 1) if ny > 1 else 1.0
    z = _rows_from_flat(vals, nx, ny, SURFER_BLANK, "x_fast_south")
    return Grid(nx, ny, xlo, ylo, dx, dy, z, crs or tb_import.WGS84_GEO,
                source_format="Surfer 6 binary (DSBB)")


def read_surfer7_binary(data: bytes, crs: Optional[tb_import.Crs] = None) -> Grid:
    if data[:4] != b"DSRB":
        raise ValueError("not a Surfer 7 binary grid (missing DSRB)")
    pos, head, vals = 0, None, None
    blank = SURFER_BLANK
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        size = struct.unpack("<i", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        pos += 8 + size
        if tag == b"GRID":
            if len(body) < 72:
                raise ValueError("truncated Surfer 7 GRID section")
            n_row, n_col = struct.unpack("<ii", body[0:8])
            x0, y0, dx, dy, _zmin, _zmax, rot, blank = struct.unpack("<8d", body[8:72])
            if abs(rot) > 1e-9:
                raise ValueError("rotated Surfer 7 grid — rotated grids are not supported")
            head = (n_col, n_row, x0, y0, dx, dy)
        elif tag == b"DATA":
            vals = list(struct.unpack(f"<{len(body) // 8}d", body[:len(body) // 8 * 8]))
    if head is None or vals is None:
        raise ValueError("Surfer 7 grid has no GRID/DATA section")
    nx, ny, x0, y0, dx, dy = head
    z = _rows_from_flat(vals, nx, ny, blank, "x_fast_south")
    return Grid(nx, ny, x0, y0, dx, dy, z, crs or tb_import.WGS84_GEO,
                source_format="Surfer 7 binary (DSRB)")


def read_esri_ascii(data: bytes, crs: Optional[tb_import.Crs] = None) -> Grid:
    text = data.decode("latin-1")
    hdr: Dict[str, float] = {}
    body_start = 0
    for m in re.finditer(r"^[ \t]*([A-Za-z_]+)[ \t]+(-?[\d.eE+]+)[ \t]*$", text, re.M):
        key = m.group(1).lower()
        if key not in ("ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter",
                       "cellsize", "dx", "dy", "nodata_value"):
            break
        hdr[key] = float(m.group(2))
        body_start = m.end()
    if "ncols" not in hdr or "nrows" not in hdr:
        raise ValueError("not an ESRI ASCII grid (no ncols/nrows header)")
    nx, ny = int(hdr["ncols"]), int(hdr["nrows"])
    dx = hdr.get("cellsize", hdr.get("dx", 1.0))
    dy = hdr.get("cellsize", hdr.get("dy", dx))
    if "xllcenter" in hdr:
        x0, y0 = hdr["xllcenter"], hdr["yllcenter"]
    else:
        # corner-registered: the first node sits half a cell in from the corner
        x0 = hdr.get("xllcorner", 0.0) + dx / 2.0
        y0 = hdr.get("yllcorner", 0.0) + dy / 2.0
    nodata = hdr.get("nodata_value", -9999.0)
    z = _rows_from_flat(_numbers(text[body_start:]), nx, ny, nodata, "x_fast_north")
    return Grid(nx, ny, x0, y0, dx, dy, z, crs or tb_import.WGS84_GEO,
                source_format="ESRI ASCII grid")


def read_irap_ascii(data: bytes, crs: Optional[tb_import.Crs] = None,
                    transposed: bool = False) -> Grid:
    """IRAP classic ASCII (RMS/Petrel).

    Header:  -996  ny  xinc  yinc  /  xmin xmax ymin ymax  /  nx rot x0 y0  /  7 zeros

    The format does not state which axis cycles fastest in the value block. This
    reader takes x fastest, rows from the south — if an imported surface comes
    out with its axes swapped, `transposed=True` (the app's *Swap grid axes*
    toggle) reads it the other way.
    """
    nums = _numbers(data.decode("latin-1"))
    if len(nums) < 19 or int(nums[0]) != -996:
        raise ValueError("not an IRAP classic ASCII grid (first token is not -996)")
    ny = int(nums[1])
    xinc, yinc = float(nums[2]), float(nums[3])   # header line is: -996  ny  xinc  yinc
    xmin, _xmax, ymin, _ymax = nums[4:8]
    nx = int(nums[8])
    rot = float(nums[9])
    if abs(rot) > 1e-6:
        raise ValueError("rotated IRAP grid — rotated grids are not supported")
    vals = nums[19:]
    if transposed:
        z = _rows_from_flat(vals, ny, nx, IRAP_UNDEF, "x_fast_south")
        return Grid(ny, nx, xmin, ymin, yinc, xinc, z, crs or tb_import.WGS84_GEO,
                    source_format="IRAP classic ASCII (axes swapped)").transpose()
    z = _rows_from_flat(vals, nx, ny, IRAP_UNDEF, "x_fast_south")
    return Grid(nx, ny, xmin, ymin, xinc, yinc, z, crs or tb_import.WGS84_GEO,
                source_format="IRAP classic ASCII")


def read_zmap(data: bytes, crs: Optional[tb_import.Crs] = None) -> Grid:
    """ZMAP+ ASCII. Values run column by column, north to south inside a column."""
    lines = [ln.rstrip("\r") for ln in data.decode("latin-1").splitlines()]
    hdr: List[str] = []
    body_at = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("!"):
            continue
        if s.startswith("@"):
            hdr.append(s)
            if len(hdr) >= 4:          # 4th @-line closes the header block
                body_at = i + 1
                break
            continue
        if hdr:
            hdr.append(s)
    if body_at is None or len(hdr) < 4 or "GRID" not in hdr[0].upper():
        raise ValueError("not a ZMAP+ grid (no '@…, GRID, 5' header block)")
    h1 = _numbers(hdr[1])              # width, null, null-text, decimals, start col
    h2 = _numbers(hdr[2])              # nrows, ncols, xmin, xmax, ymin, ymax
    if len(h2) < 6:
        raise ValueError("truncated ZMAP+ header")
    ny, nx = int(h2[0]), int(h2[1])
    xmin, xmax, ymin, ymax = h2[2:6]
    nodata = h1[1] if len(h1) > 1 else 1e30
    dx = (xmax - xmin) / (nx - 1) if nx > 1 else 1.0
    dy = (ymax - ymin) / (ny - 1) if ny > 1 else 1.0
    z = _rows_from_flat(_numbers("\n".join(lines[body_at:])), nx, ny, nodata, "y_fast_north")
    return Grid(nx, ny, xmin, ymin, dx, dy, z, crs or tb_import.WGS84_GEO,
                source_format="ZMAP+ ASCII")


def sniff(data: bytes) -> str:
    """Name the grid format from the content. Raises if nothing matches."""
    if data[:4] == b"DSBB":
        return "surfer6"
    if data[:4] == b"DSRB":
        return "surfer7"
    head = data[:4096].decode("latin-1", "replace")
    if head.lstrip()[:4].upper() == "DSAA":
        return "surfer_ascii"
    stripped = [ln.strip() for ln in head.splitlines() if ln.strip() and not ln.strip().startswith("!")]
    first = stripped[0] if stripped else ""
    if first.split()[:1] == ["-996"]:
        return "irap"
    if first.startswith("@") and "GRID" in head[:400].upper():
        return "zmap"
    if re.match(r"^\s*ncols\s+\d+", head, re.I):
        return "esri"
    raise ValueError("unrecognised grid format — expected Surfer (DSAA/DSBB/DSRB), "
                     "IRAP classic ASCII (-996), ZMAP+ or ESRI ASCII (ncols/nrows)")


READERS = {"surfer_ascii": read_surfer_ascii, "surfer6": read_surfer6_binary,
           "surfer7": read_surfer7_binary, "esri": read_esri_ascii,
           "irap": read_irap_ascii, "zmap": read_zmap}


def read_grid(filename: str, data: bytes, crs: Optional[tb_import.Crs] = None,
              transposed: bool = False) -> Grid:
    kind = sniff(data)
    if kind == "irap":
        g = read_irap_ascii(data, crs, transposed)
    else:
        g = READERS[kind](data, crs)
        if transposed:
            g = g.transpose()
    g.name = filename.rsplit("/", 1)[-1]
    if crs is None and g.crs.kind == "geographic":
        # a projected grid read without a CRS would be placed off Africa — say so early
        x0, y0, x1, y1 = g.bbox()
        if not (-180 <= x0 <= 180 and -180 <= x1 <= 180 and -90 <= y0 <= 90 and -90 <= y1 <= 90):
            raise ValueError("grid coordinates are not lon/lat — choose the source CRS "
                             "(e.g. ED50 UTM 31N) before adding the grid")
    return g


# ──────────────────────────────── contouring ───────────────────────────────

def nice_levels(vmin: float, vmax: float, target: int = 12) -> List[float]:
    """Round contour levels (1/2/5 × 10ⁿ) covering the data range."""
    if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmax <= vmin:
        return []
    raw = (vmax - vmin) / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag)
    start = math.ceil(vmin / step) * step
    out, v = [], start
    while v <= vmax + 1e-9 and len(out) < 500:
        out.append(round(v, 10))
        v += step
    return out


def _interp(p, q, vp, vq, level):
    t = 0.5 if abs(vq - vp) < 1e-12 else (level - vp) / (vq - vp)
    return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))


def _nudge_level(grid: Grid, level: float) -> float:
    """A node sitting exactly on the level makes two edge crossings collapse onto
    the same point — a zero-length segment that forks the chaining and breaks a
    closed contour into fragments. Shifting the level by ~1e-9 of the data range
    removes the degeneracy and moves the line by less than a micron on the ground.
    """
    vals = grid.finite()
    if not vals or not any(v == level for v in vals):
        return level
    rng = max(vals) - min(vals)
    return level + max(abs(level), rng, 1.0) * 1e-9


def _segments(grid: Grid, level: float) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Marching squares over one level. Cells touching a blank node are skipped."""
    level = _nudge_level(grid, level)
    segs = []
    for j in range(grid.ny - 1):
        y_lo = grid.y0 + j * grid.dy
        y_hi = y_lo + grid.dy
        row0, row1 = grid.z[j], grid.z[j + 1]
        for i in range(grid.nx - 1):
            v00, v10, v01, v11 = row0[i], row0[i + 1], row1[i], row1[i + 1]
            if None in (v00, v10, v01, v11):
                continue
            if max(v00, v10, v01, v11) < level or min(v00, v10, v01, v11) >= level:
                continue
            x_lo = grid.x0 + i * grid.dx
            x_hi = x_lo + grid.dx
            sw, se = (x_lo, y_lo), (x_hi, y_lo)
            nw, ne = (x_lo, y_hi), (x_hi, y_hi)
            idx = ((1 if v00 >= level else 0) | (2 if v10 >= level else 0)
                   | (4 if v11 >= level else 0) | (8 if v01 >= level else 0))
            bottom = lambda: _interp(sw, se, v00, v10, level)   # noqa: E731
            right = lambda: _interp(se, ne, v10, v11, level)    # noqa: E731
            top = lambda: _interp(nw, ne, v01, v11, level)      # noqa: E731
            left = lambda: _interp(sw, nw, v00, v01, level)     # noqa: E731
            if idx in (1, 14):
                segs.append((left(), bottom()))
            elif idx in (2, 13):
                segs.append((bottom(), right()))
            elif idx in (3, 12):
                segs.append((left(), right()))
            elif idx in (4, 11):
                segs.append((right(), top()))
            elif idx in (6, 9):
                segs.append((bottom(), top()))
            elif idx in (7, 8):
                segs.append((left(), top()))
            elif idx in (5, 10):                    # saddle — split on the cell average
                avg = (v00 + v10 + v01 + v11) / 4.0
                if (idx == 5) == (avg >= level):
                    segs.append((left(), top()))
                    segs.append((bottom(), right()))
                else:
                    segs.append((left(), bottom()))
                    segs.append((right(), top()))
    return segs


def _chain(segs, tol: float) -> List[List[Tuple[float, float]]]:
    """Join segments end to end into polylines."""
    key = lambda p: (round(p[0] / tol), round(p[1] / tol))     # noqa: E731
    ends: Dict[tuple, List[int]] = {}
    for n, (a, b) in enumerate(segs):
        ends.setdefault(key(a), []).append(n)
        ends.setdefault(key(b), []).append(n)
    used = [False] * len(segs)
    lines = []
    for n in range(len(segs)):
        if used[n]:
            continue
        used[n] = True
        line = [segs[n][0], segs[n][1]]
        for direction in (1, 0):                      # extend forward, then backward
            while True:
                tip = line[-1] if direction else line[0]
                nxt = None
                for m in ends.get(key(tip), ()):
                    if used[m]:
                        continue
                    a, b = segs[m]
                    if key(a) == key(tip):
                        nxt, pt = m, b
                        break
                    if key(b) == key(tip):
                        nxt, pt = m, a
                        break
                if nxt is None:
                    break
                used[nxt] = True
                line.append(pt) if direction else line.insert(0, pt)
        lines.append(line)
    return lines


def contours(grid: Grid, levels: Sequence[float]) -> List[dict]:
    """[{level, coords:[(x, y), …]}, …] in the grid's own CRS."""
    tol = min(grid.dx, grid.dy) / 1000.0
    key = lambda p: (round(p[0] / tol), round(p[1] / tol))       # noqa: E731
    out = []
    for lv in levels:
        # A segment shorter than the chaining tolerance is invisible to the
        # chainer and would orphan itself as a two-point fragment, so it goes
        # before chaining rather than after.
        segs = [s for s in _segments(grid, lv) if key(s[0]) != key(s[1])]
        for line in _chain(segs, tol):
            if len(line) >= 2:
                out.append({"level": float(lv), "coords": line})
    return out


def contour_features(grid: Grid, levels: Optional[Sequence[float]] = None,
                     ramp: str = "depth", title: Optional[str] = None,
                     max_cells: int = 250_000) -> dict:
    """Contours as a WGS84 GeoJSON layer, ready for the map's overlay pipeline."""
    g = grid.decimate(max_cells)
    s = g.stats()
    lv = list(levels) if levels is not None else nice_levels(s["min"], s["max"])
    lines = contours(g, lv)
    lo, hi = (s["min"], s["max"]) if s["max"] > s["min"] else (s["min"], s["min"] + 1)
    feats = []
    for c in lines:
        t = (c["level"] - lo) / (hi - lo)
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString",
                                   "coordinates": [[x, y] for x, y in c["coords"]]},
                      "properties": {"_label": f"{c['level']:g}", "level": c["level"],
                                     "_fill": rgb_hex(color_at(t, ramp)),
                                     "_outline": rgb_hex(color_at(t, ramp))}})
    fc = tb_import.reproject_fc({"type": "FeatureCollection", "features": feats}, g.crs)
    fc.update(title=title or f"{grid.name} contours", geometry="line",
              color="#00243D", levels=[float(x) for x in lv])
    return fc


# ─────────────────────────────── colour + PNG ──────────────────────────────

RAMPS: Dict[str, List[Tuple[float, Tuple[int, int, int]]]] = {
    # shallow → deep, the way a bathymetric chart reads
    "depth": [(0.0, (222, 242, 247)), (0.25, (160, 214, 232)), (0.5, (86, 168, 208)),
              (0.75, (35, 102, 163)), (1.0, (10, 36, 92))],
    # structure maps: high (crest) → low
    "terrain": [(0.0, (26, 60, 30)), (0.3, (92, 148, 74)), (0.55, (222, 210, 140)),
                (0.8, (186, 122, 66)), (1.0, (246, 246, 246))],
    "spectral": [(0.0, (49, 54, 149)), (0.25, (69, 152, 195)), (0.5, (245, 245, 200)),
                 (0.75, (244, 143, 78)), (1.0, (165, 0, 38))],
    "grey": [(0.0, (30, 30, 30)), (1.0, (245, 245, 245))],
}


def color_at(t: float, ramp: str = "depth") -> Tuple[int, int, int]:
    stops = RAMPS.get(ramp) or RAMPS["depth"]
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    for (t0, c0), (t1, c1) in zip(stops[:-1], stops[1:]):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(int(round(a + (b - a) * f)) for a, b in zip(c0, c1))
    return stops[-1][1]


def rgb_hex(c: Tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % c


def png_bytes(rows: Sequence[Sequence[int]], width: int, height: int) -> bytes:
    """8-bit RGBA PNG. `rows` is `height` sequences of 4 × width bytes."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag: bytes, body: bytes) -> bytes:
        c = tag + body
        return struct.pack(">I", len(body)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _merc_y(lat: float) -> float:
    lat = max(min(lat, 85.05112878), -85.05112878)
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _merc_lat(y: float) -> float:
    return math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2)


def image_overlay(grid: Grid, width: int = 480, ramp: str = "depth", opacity: float = 0.7,
                  vmin: Optional[float] = None, vmax: Optional[float] = None,
                  max_height: int = 900, title: Optional[str] = None) -> dict:
    """Resample the grid onto a lon/lat raster and return a Leaflet image overlay.

    The image is built in Web Mercator rows (what `L.imageOverlay` draws into),
    and every pixel is inverse-projected into grid coordinates, so a UTM grid
    lands in the right place rather than being stretched over its lat/lon box.
    """
    w, s, e, n = grid.lonlat_bbox()
    if e <= w or n <= s:
        raise ValueError("grid has no extent in lon/lat")
    my0, my1 = _merc_y(s), _merc_y(n)
    width = max(16, min(int(width), 2048))
    height = int(round(width * (my1 - my0) / math.radians(e - w)))
    height = max(16, min(height, max_height))
    st = grid.stats()
    lo = st["min"] if vmin is None else vmin
    hi = st["max"] if vmax is None else vmax
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        raise ValueError("grid has no usable value range to colour")
    alpha = max(0, min(255, int(round(opacity * 255))))
    rows = []
    for py in range(height):
        my = my1 - (my1 - my0) * (py + 0.5) / height
        lat = _merc_lat(my)
        row = bytearray(width * 4)
        for px in range(width):
            lon = w + (e - w) * (px + 0.5) / width
            v = grid.value_at(*grid.to_grid_xy(lat, lon))
            if v is None:
                continue                              # transparent
            r, g, b = color_at((v - lo) / (hi - lo), ramp)
            k = px * 4
            row[k], row[k + 1], row[k + 2], row[k + 3] = r, g, b, alpha
        rows.append(row)
    png = png_bytes(rows, width, height)
    return {"title": title or grid.name,
            "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
            "bounds": [[s, w], [n, e]], "opacity": 1.0, "width": width, "height": height,
            "vmin": lo, "vmax": hi, "ramp": ramp,
            "rev": f"{grid.name}:{width}x{height}:{ramp}:{lo:.3f}:{hi:.3f}:{alpha}"}


def legend(vmin: float, vmax: float, ramp: str = "depth", steps: int = 6) -> List[dict]:
    return [{"value": vmin + (vmax - vmin) * k / (steps - 1),
             "color": rgb_hex(color_at(k / (steps - 1), ramp))} for k in range(steps)]


# ───────────────────────── grid as a depth source ──────────────────────────

def fill_node_depths(layout, grid: Grid, only_blank: bool = True,
                     convention: Optional[str] = None) -> Dict[str, Optional[float]]:
    """Set water_depth_m on subsea nodes from the grid. Hosts are left alone.

    Mirrors tb_bathymetry.fill_node_depths so a loaded survey grid can stand in
    for the regional DTM. Nodes off the grid keep their existing value.
    """
    import tb_map
    conv = convention or grid.depth_convention()
    out: Dict[str, Optional[float]] = {}
    for n in layout.nodes.values():
        if layout.kind(n.node_id) == "host":
            continue
        if only_blank and n.water_depth_m > 0:
            continue
        lat, lon = tb_map.to_display(layout, n.lat, n.lon)     # grids are georeferenced in WGS84 terms
        d = grid.depth_at(lat, lon, conv)
        out[n.node_id] = d
        if d is not None:
            n.water_depth_m = round(d, 1)
    return out


def fetch_route_profiles(layout, grid: Grid, samples: int = 40,
                         categories=("flowline", "utility_line"),
                         convention: Optional[str] = None) -> Dict[str, Optional[int]]:
    """Store a seabed profile on each line from the grid (same shape as tb_bathymetry)."""
    import tb_map
    conv = convention or grid.depth_convention()
    out: Dict[str, Optional[int]] = {}
    for e in layout.edges.values():
        if layout.catalog.get(e.item_id).category not in categories:
            continue
        pts = [tb_map.to_display(layout, la, lo) for la, lo in layout.edge_shape(e)]
        if len(pts) < 2:
            out[e.edge_id] = None
            continue
        prof = resample_along(pts, grid, samples, conv)
        if prof:
            e.attrs["seabed_profile"] = [round(d, 1) for d in prof]
            out[e.edge_id] = len(prof)
        else:
            out[e.edge_id] = None
    return out


def resample_along(vertices: Sequence[Tuple[float, float]], grid: Grid, n: int = 40,
                   convention: Optional[str] = None) -> List[float]:
    """Depth at `n` evenly spaced points along a lat/lon polyline. [] if the line
    leaves the grid — a half-covered profile would quietly bias the free-span check."""
    if len(vertices) < 2 or n < 2:
        return []
    conv = convention or grid.depth_convention()
    cum = [0.0]
    for (la1, lo1), (la2, lo2) in zip(vertices[:-1], vertices[1:]):
        cum.append(cum[-1] + tb_geo.geodesic_distance(la1, lo1, la2, lo2))
    total = cum[-1]
    if total <= 0:
        return []
    out = []
    for k in range(n):
        target = total * k / (n - 1)
        i = max(0, min(len(cum) - 2, next((m for m in range(len(cum) - 1) if cum[m + 1] >= target),
                                          len(cum) - 2)))
        span = cum[i + 1] - cum[i]
        t = 0.0 if span <= 0 else (target - cum[i]) / span
        la = vertices[i][0] + t * (vertices[i + 1][0] - vertices[i][0])
        lo = vertices[i][1] + t * (vertices[i + 1][1] - vertices[i][1])
        d = grid.depth_at(la, lo, conv)
        if d is None:
            return []
        out.append(d)
    return out


def coverage(layout, grid: Grid) -> Dict[str, int]:
    """How much of the layout the grid actually covers — worth knowing before
    trusting it as the depth source."""
    import tb_map
    inside = outside = 0
    for n in layout.nodes.values():
        lat, lon = tb_map.to_display(layout, n.lat, n.lon)
        if grid.value_at(*grid.to_grid_xy(lat, lon)) is None:
            outside += 1
        else:
            inside += 1
    return {"nodes_on_grid": inside, "nodes_off_grid": outside}
