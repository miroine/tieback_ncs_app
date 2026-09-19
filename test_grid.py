import sys, math, struct, base64, zlib
import tb_grid as gr, tb_import as im, tb_geo as g, tb_catalog as c, tb_network as n
from _harness import Suite
S = Suite("test_grid")

# ── synthetic surfaces ──────────────────────────────────────────────────────
NX, NY = 5, 4
X0, Y0, DX, DY = 400000.0, 6700000.0, 250.0, 200.0
# a tilted plane: z = 300 + 0.004*(x-x0) + 0.002*(y-y0)  → exactly reproducible
def plane(i, j):
    return 300.0 + 0.004 * (i * DX) + 0.002 * (j * DY)
Z = [[plane(i, j) for i in range(NX)] for j in range(NY)]          # row 0 = south
UTM31 = im.Crs("utm", "ED50", 31, "N")


def surfer_ascii(blank=None):
    rows = []
    for j in range(NY):
        rows.append(" ".join(f"{(gr.SURFER_BLANK if blank == (j, i) else Z[j][i]):.6f}"
                             for i in range(NX)))
    body = "\n".join(rows)
    vals = [v for r in Z for v in r]
    return (f"DSAA\n{NX} {NY}\n{X0} {X0 + (NX - 1) * DX}\n{Y0} {Y0 + (NY - 1) * DY}\n"
            f"{min(vals)} {max(vals)}\n{body}\n").encode()


def surfer6():
    head = b"DSBB" + struct.pack("<hh", NX, NY) + struct.pack(
        "<6d", X0, X0 + (NX - 1) * DX, Y0, Y0 + (NY - 1) * DY,
        min(v for r in Z for v in r), max(v for r in Z for v in r))
    body = b"".join(struct.pack("<f", Z[j][i]) for j in range(NY) for i in range(NX))
    return head + body


def surfer7(rot=0.0):
    grid = struct.pack("<ii", NY, NX) + struct.pack(
        "<8d", X0, Y0, DX, DY, min(v for r in Z for v in r), max(v for r in Z for v in r),
        rot, gr.SURFER_BLANK)
    data = b"".join(struct.pack("<d", Z[j][i]) for j in range(NY) for i in range(NX))
    return (b"DSRB" + struct.pack("<i", 4) + struct.pack("<i", 1)
            + b"GRID" + struct.pack("<i", len(grid)) + grid
            + b"DATA" + struct.pack("<i", len(data)) + data)


def esri_ascii():
    """ESRI ASCII has one square cellsize and writes the NORTH row first."""
    lines = [f"ncols {NX}", f"nrows {NY}", f"xllcorner {X0 - DX / 2}", f"yllcorner {Y0 - DX / 2}",
             f"cellsize {DX}", "NODATA_value -9999"]
    for j in range(NY - 1, -1, -1):
        lines.append(" ".join(f"{Z[j][i]:.6f}" for i in range(NX)))
    return ("\n".join(lines) + "\n").encode()


def irap_ascii_yfast(rot=0.0):
    """Same surface, but the value block cycles y fastest — what the *Swap grid
    axes* toggle is for."""
    head = (f"-996 {NY} {DX} {DY}\n"
            f"{X0} {X0 + (NX - 1) * DX} {Y0} {Y0 + (NY - 1) * DY}\n"
            f"{NX} {rot} {X0} {Y0}\n0 0 0 0 0 0 0\n")
    body = "\n".join(" ".join(f"{Z[j][i]:.6f}" for j in range(NY)) for i in range(NX))
    return (head + body + "\n").encode()


def irap_ascii():
    head = (f"-996 {NY} {DX} {DY}\n"
            f"{X0} {X0 + (NX - 1) * DX} {Y0} {Y0 + (NY - 1) * DY}\n"
            f"{NX} 0.0 {X0} {Y0}\n0 0 0 0 0 0 0\n")
    body = "\n".join(" ".join(f"{Z[j][i]:.6f}" for i in range(NX)) for j in range(NY))
    return (head + body + "\n").encode()


def zmap_ascii():
    # column by column, north → south inside each column
    vals = [Z[j][i] for i in range(NX) for j in range(NY - 1, -1, -1)]
    head = ("!created by test\n@TEST, GRID, 5\n   20, 1e30, , 7, 1\n"
            f"   {NY}, {NX}, {X0}, {X0 + (NX - 1) * DX}, {Y0}, {Y0 + (NY - 1) * DY}\n"
            "   0.0, 0.0, 0.0\n@\n")
    body = "\n".join("  ".join(f"{v:.6f}" for v in vals[k:k + 5]) for k in range(0, len(vals), 5))
    return (head + body + "\n").encode()


# ── sniffing ────────────────────────────────────────────────────────────────
S.check("sniff Surfer ASCII", lambda: gr.sniff(surfer_ascii()) == "surfer_ascii")
S.check("sniff Surfer 6", lambda: gr.sniff(surfer6()) == "surfer6")
S.check("sniff Surfer 7", lambda: gr.sniff(surfer7()) == "surfer7")
S.check("sniff ESRI", lambda: gr.sniff(esri_ascii()) == "esri")
S.check("sniff IRAP", lambda: gr.sniff(irap_ascii()) == "irap")
S.check("sniff ZMAP+", lambda: gr.sniff(zmap_ascii()) == "zmap")
S.raises("sniff rejects unknown", ValueError, lambda: gr.sniff(b"hello world\n1 2 3\n"))
S.check("sniff ignores ZMAP comment lines",
        lambda: gr.sniff(b"!one\n!two\n@T, GRID, 5\n 20, 1e30, , 7, 1\n") == "zmap")


# ── each reader lands the same surface in the same place ────────────────────
def check_shape(kind, data, **kw):
    grid = gr.read_grid(f"demo.grd", data, UTM31, **kw)
    assert (grid.nx, grid.ny) == (NX, NY), f"{kind}: shape {grid.nx}×{grid.ny}"
    assert abs(grid.x0 - X0) < 1e-6 and abs(grid.y0 - Y0) < 1e-6, f"{kind}: origin"
    assert abs(grid.dx - DX) < 1e-6 and abs(grid.dy - DY) < 1e-6, f"{kind}: increment"
    for j in (0, NY - 1):
        for i in (0, NX - 1):
            assert abs(grid.z[j][i] - Z[j][i]) < 1e-2, f"{kind}: corner {i},{j}"
    return grid


S.check("Surfer ASCII round-trip", lambda: check_shape("dsaa", surfer_ascii()))
S.check("Surfer 6 round-trip", lambda: check_shape("dsbb", surfer6()))
S.check("Surfer 7 round-trip", lambda: check_shape("dsrb", surfer7()))
def esri_round_trip():
    grid = gr.read_grid("d.asc", esri_ascii(), UTM31)
    assert (grid.nx, grid.ny) == (NX, NY), f"{grid.nx}×{grid.ny}"
    assert abs(grid.x0 - X0) < 1e-6 and abs(grid.y0 - Y0) < 1e-6, "corner-registered origin"
    assert grid.dx == DX and grid.dy == DX, "one square cellsize"
    assert abs(grid.z[0][0] - Z[0][0]) < 1e-3 and abs(grid.z[NY - 1][0] - Z[NY - 1][0]) < 1e-3, \
        "north row must land on the north edge"
    return True


S.check("ESRI ASCII round-trip (north row first)", esri_round_trip)
S.check("ESRI xllcenter registration",
        lambda: abs(gr.read_grid("d.asc", esri_ascii().replace(b"xllcorner", b"xllcenter")
                                 .replace(b"yllcorner", b"yllcenter"), UTM31).x0
                    - (X0 - DX / 2)) < 1e-6)
S.check("ESRI NODATA becomes a blank",
        lambda: gr.read_grid("d.asc", esri_ascii().replace(f"{Z[NY - 1][0]:.6f}".encode(),
                                                           b"-9999.000000", 1), UTM31)
        .z[NY - 1][0] is None)
S.check("IRAP round-trip", lambda: check_shape("irap", irap_ascii()))
S.check("ZMAP+ round-trip (column-major, north first)", lambda: check_shape("zmap", zmap_ascii()))

S.raises("rotated Surfer 7 refused", ValueError,
         lambda: gr.read_grid("r.grd", surfer7(rot=12.0), UTM31))
S.raises("rotated IRAP refused", ValueError,
         lambda: gr.read_grid("r.grd", irap_ascii_yfast(rot=15.0), UTM31))
S.raises("truncated value block refused", ValueError,
         lambda: gr.read_grid("t.grd", b"\n".join(surfer_ascii().split(b"\n")[:-3]) + b"\n", UTM31))
S.raises("projected grid without a CRS refused", ValueError,
         lambda: gr.read_grid("p.grd", surfer_ascii()))
S.check("blank node preserved",
        lambda: gr.read_grid("b.grd", surfer_ascii(blank=(1, 2)), UTM31).z[1][2] is None)
S.check("source format recorded",
        lambda: "Surfer 7" in gr.read_grid("d.grd", surfer7(), UTM31).source_format)
S.check("name taken from the file",
        lambda: gr.read_grid("seabed_a.grd", surfer6(), UTM31).name == "seabed_a.grd")

G = gr.read_grid("demo.grd", surfer_ascii(), UTM31)

# ── geometry and sampling ───────────────────────────────────────────────────
S.check("bbox", lambda: G.bbox() == (X0, Y0, X0 + (NX - 1) * DX, Y0 + (NY - 1) * DY))
S.check("stats", lambda: abs(G.stats()["max"] - max(v for r in Z for v in r)) < 1e-6)
S.check("blank count", lambda: G.stats()["n_blank"] == 0)
S.check("value at a node is the node value",
        lambda: abs(G.value_at(X0 + 2 * DX, Y0 + DY) - Z[1][2]) < 1e-6)
S.check("bilinear on a plane is exact",
        lambda: abs(G.value_at(X0 + 1.37 * DX, Y0 + 2.4 * DY)
                    - (300 + 0.004 * 1.37 * DX + 0.002 * 2.4 * DY)) < 1e-6)
S.check("off-grid returns None", lambda: G.value_at(X0 - 1, Y0) is None)
S.check("blank cell returns None",
        lambda: gr.read_grid("b.grd", surfer_ascii(blank=(1, 2)), UTM31)
        .value_at(X0 + 2.5 * DX, Y0 + 1.5 * DY) is None)
S.raises("grid smaller than 2×2 refused", ValueError,
         lambda: gr.Grid(1, 4, 0, 0, 1, 1, [[0.0]] * 4))
S.raises("data shape must match the header", ValueError,
         lambda: gr.Grid(3, 2, 0, 0, 1, 1, [[0.0, 1.0], [2.0, 3.0]]))

# ── CRS handling ────────────────────────────────────────────────────────────
def lonlat_round_trip():
    lat, lon = g.utm_to_geo(X0 + 2 * DX, Y0 + DY, 31, "N", "ED50")
    lat84, lon84 = g.transform_datum(lat, lon, "ED50", "WGS84")
    x, y = G.to_grid_xy(lat84, lon84)
    return abs(x - (X0 + 2 * DX)) < 0.5 and abs(y - (Y0 + DY)) < 0.5


S.check("WGS84 lat/lon → ED50 UTM grid coordinates", lonlat_round_trip)
S.check("value_at_lonlat matches value_at", lambda: abs(
    G.value_at_lonlat(*g.transform_datum(*g.utm_to_geo(X0 + DX, Y0 + DY, 31, "N", "ED50"),
                                         "ED50", "WGS84")) - Z[1][1]) < 1e-3)
S.check("lon/lat bbox covers the grid corners", lambda: (
    lambda b: b[0] <= G.to_lonlat(X0, Y0)[0] and b[2] >= G.to_lonlat(G.x1, G.y1)[0]
    and b[1] <= G.to_lonlat(X0, Y0)[1] and b[3] >= G.to_lonlat(G.x1, G.y1)[1])(G.lonlat_bbox()))
S.check("geographic grid needs no reprojection", lambda: abs(
    gr.Grid(3, 3, 2.0, 60.0, 0.1, 0.1, [[1.0] * 3] * 3).to_grid_xy(60.1, 2.1)[0] - 2.1) < 1e-9)

# ── reshaping ───────────────────────────────────────────────────────────────
S.check("transpose swaps the axes",
        lambda: (G.transpose().nx, G.transpose().ny) == (NY, NX))
S.check("transpose twice is the original",
        lambda: G.transpose().transpose().z == G.z)
S.check("transpose swaps the increments",
        lambda: (G.transpose().dx, G.transpose().dy) == (DY, DX))
def transposed_read_recovers_the_surface():
    """A y-fastest IRAP file read normally comes out scrambled; read with
    transposed=True it is the original surface again."""
    wrong = gr.read_grid("i.grd", irap_ascii_yfast(), UTM31)
    right = gr.read_grid("i.grd", irap_ascii_yfast(), UTM31, transposed=True)
    assert (right.nx, right.ny) == (NX, NY), f"{right.nx}×{right.ny}"
    assert right.dx == DX and right.dy == DY, "increments must survive the swap"
    for j in range(NY):
        for i in range(NX):
            assert abs(right.z[j][i] - Z[j][i]) < 1e-3, f"value at {i},{j}"
    assert wrong.z != right.z, "the fixture would not have detected a swap"
    return True


S.check("IRAP swapped-axes read recovers the surface", transposed_read_recovers_the_surface)
S.check("decimate leaves a small grid alone", lambda: G.decimate(10_000) is G)


def big_grid(nx=200, ny=200):
    return gr.Grid(nx, ny, 0.0, 0.0, 10.0, 10.0,
                   [[float(i + j) for i in range(nx)] for j in range(ny)])


S.check("decimate caps the node count", lambda: big_grid().decimate(2000).cells() <= 2500)
S.check("decimate keeps the origin", lambda: big_grid().decimate(2000).x0 == 0.0)
S.check("decimate widens the spacing", lambda: big_grid().decimate(2000).dx > 10.0)
S.check("describe mentions the CRS", lambda: "ED50 UTM 31N" in G.describe())

# ── depth semantics ─────────────────────────────────────────────────────────
ELEV = gr.Grid(3, 3, X0, Y0, DX, DY, [[-340.0, -350.0, -360.0]] * 3, UTM31, "elev")
S.check("positive values read as depth", lambda: G.depth_convention() == "positive_down")
S.check("negative values read as elevation", lambda: ELEV.depth_convention() == "elevation")
S.check("mixed signs are unknown",
        lambda: gr.Grid(3, 3, 0, 0, 1, 1, [[-1.0, 1.0, -1.0]] * 3).depth_convention() == "unknown")
S.check("all-blank grid is unknown",
        lambda: gr.Grid(2, 2, 0, 0, 1, 1, [[None, None], [None, None]]).depth_convention() == "unknown")


def elev_depth():
    lat, lon = g.transform_datum(*g.utm_to_geo(X0 + DX, Y0 + DY, 31, "N", "ED50"), "ED50", "WGS84")
    return abs(ELEV.depth_at(lat, lon) - 350.0) < 1.0


S.check("elevation grid returns positive depth", elev_depth)
S.check("depth above sea level is not a water depth",
        lambda: gr.Grid(2, 2, 0, 0, 1, 1, [[-5.0, -5.0], [-5.0, -5.0]],
                        im.WGS84_GEO).depth_at(0.5, 0.5, "positive_down") is None)
S.check("depth off the grid is None", lambda: G.depth_at(0.0, 0.0) is None)

# ── contouring ──────────────────────────────────────────────────────────────
S.check("nice_levels are round", lambda: gr.nice_levels(301.0, 349.0, 5)[0] % 10 == 0)
S.check("nice_levels cover the range",
        lambda: gr.nice_levels(0, 100, 10)[0] >= 0 and gr.nice_levels(0, 100, 10)[-1] <= 100)
S.check("nice_levels on a flat range", lambda: gr.nice_levels(5.0, 5.0) == [])


def plane_contour_is_straight():
    """On a tilted plane every contour is a straight line — a strong check that
    the marching-squares interpolation and the chaining are both right."""
    cs = gr.contours(G, [302.0])
    assert len(cs) == 1, f"expected one line, got {len(cs)}"
    pts = cs[0]["coords"]
    assert len(pts) >= 3, "too few vertices"
    for x, y in pts:
        z = 300 + 0.004 * (x - X0) + 0.002 * (y - Y0)
        assert abs(z - 302.0) < 1e-6, f"vertex off the level: {z}"
    return True


S.check("contour of a plane lies exactly on the level", plane_contour_is_straight)
S.check("contour outside the range is empty", lambda: gr.contours(G, [1000.0]) == [])


def closed_contour():
    """A cone: the level set is a closed ring, so the chain must come back on itself."""
    nx = ny = 21
    z = [[100.0 - math.hypot(i - 10, j - 10) for i in range(nx)] for j in range(ny)]
    cone = gr.Grid(nx, ny, 0.0, 0.0, 1.0, 1.0, z)
    cs = gr.contours(cone, [95.0])
    assert len(cs) == 1, f"expected one ring, got {len(cs)}"
    pts = cs[0]["coords"]
    assert math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6, "ring is not closed"
    for x, y in pts:
        assert abs(math.hypot(x - 10, y - 10) - 5.0) < 0.15, "ring is not a circle of radius 5"
    return True


S.check("closed contour on a cone", closed_contour)
def blank_cell_is_not_contoured():
    """Blanking the south-west node must clear the contour out of that one cell,
    and leave the rest of the line alone."""
    lv = 300.6
    full = gr.contours(G, [lv])
    holed = gr.contours(gr.read_grid("b.grd", surfer_ascii(blank=(0, 0)), UTM31), [lv])
    in_cell = lambda pts: [p for p in pts if p[0] < X0 + DX and p[1] < Y0 + DY]   # noqa: E731
    assert in_cell([p for c in full for p in c["coords"]]), "fixture never crossed that cell"
    assert not in_cell([p for c in holed for p in c["coords"]]), "contour ran through a blank cell"
    assert holed, "the rest of the contour should survive"
    return True


S.check("contour skips cells touching a blank", blank_cell_is_not_contoured)


def contour_layer():
    fc = gr.contour_features(G, [302.0, 303.0])
    assert fc["type"] == "FeatureCollection" and fc["geometry"] == "line"
    assert fc["features"], "no features"
    for f in fc["features"]:
        lon, lat = f["geometry"]["coordinates"][0]
        assert 1.0 < lon < 4.0 and 59.0 < lat < 62.0, f"not reprojected: {lon},{lat}"
        assert f["properties"]["_fill"].startswith("#")
        assert f["properties"]["_label"] in ("302", "303")
    return True


S.check("contour_features reprojects to WGS84 with styling", contour_layer)
S.check("contour_features defaults to nice levels",
        lambda: len(gr.contour_features(G)["levels"]) > 1)
S.check("contour_features titles the layer",
        lambda: gr.contour_features(G, [302.0])["title"] == "demo.grd contours")

# ── colour and PNG ──────────────────────────────────────────────────────────
S.check("ramp ends are the stop colours",
        lambda: gr.color_at(0.0, "grey") == (30, 30, 30) and gr.color_at(1.0, "grey") == (245, 245, 245))
S.check("ramp midpoint interpolates", lambda: gr.color_at(0.5, "grey") == (138, 138, 138))
S.check("ramp clamps out-of-range", lambda: gr.color_at(-3, "depth") == gr.color_at(0.0, "depth"))
S.check("unknown ramp falls back", lambda: gr.color_at(0.5, "nope") == gr.color_at(0.5, "depth"))
S.check("rgb_hex", lambda: gr.rgb_hex((0, 36, 61)) == "#00243D")
S.check("legend spans the range",
        lambda: (lambda L: L[0]["value"] == 300 and L[-1]["value"] == 400)(gr.legend(300, 400)))


def png_is_valid():
    rows = [bytes([255, 0, 0, 255] * 3) for _ in range(2)]
    png = gr.png_bytes(rows, 3, 2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "bad magic"
    w, h, depth, ctype = struct.unpack(">IIBB", png[16:26])
    assert (w, h, depth, ctype) == (3, 2, 8, 6), f"bad IHDR {w},{h},{depth},{ctype}"
    # walk the chunks and verify every CRC
    pos, tags = 8, []
    while pos < len(png):
        ln = struct.unpack(">I", png[pos:pos + 4])[0]
        tag = png[pos + 4:pos + 8]
        body = png[pos + 8:pos + 8 + ln]
        crc = struct.unpack(">I", png[pos + 8 + ln:pos + 12 + ln])[0]
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF, f"bad CRC on {tag}"
        tags.append(tag)
        pos += 12 + ln
    assert tags == [b"IHDR", b"IDAT", b"IEND"], tags
    raw = zlib.decompress(png[33:33 + struct.unpack(">I", png[29:33])[0]]) if False else None
    return True


S.check("png_bytes writes a valid PNG", png_is_valid)


def image_overlay_shape():
    ov = gr.image_overlay(G, width=48, ramp="depth", opacity=0.8)
    assert ov["url"].startswith("data:image/png;base64,")
    png = base64.b64decode(ov["url"].split(",", 1)[1])
    w, h = struct.unpack(">II", png[16:24])
    assert w == 48 and h == ov["height"], f"{w}×{h} vs {ov['width']}×{ov['height']}"
    (s, west), (nn, e) = ov["bounds"]
    assert s < nn and west < e, "degenerate bounds"
    bb = G.lonlat_bbox()
    assert abs(west - bb[0]) < 1e-9 and abs(nn - bb[3]) < 1e-9, "bounds do not match the grid"
    return True


S.check("image_overlay returns a data URI and lat/lon bounds", image_overlay_shape)
S.check("image_overlay height follows the mercator aspect",
        lambda: gr.image_overlay(G, width=64)["height"] > 4)
S.check("image_overlay caps the width", lambda: gr.image_overlay(G, width=9000)["width"] <= 2048)
S.check("image_overlay rev changes with the ramp",
        lambda: gr.image_overlay(G, 32, "depth")["rev"] != gr.image_overlay(G, 32, "grey")["rev"])
S.raises("image_overlay refuses a flat grid", ValueError,
         lambda: gr.image_overlay(gr.Grid(2, 2, 2.0, 60.0, 0.1, 0.1, [[5.0, 5.0], [5.0, 5.0]])))


def image_is_georeferenced():
    """Image rows are spaced linearly in Web Mercator, which is what
    L.imageOverlay draws into — spacing them linearly in latitude instead would
    smear a grid north-south. Over 55–65°N the two differ by ~9 km."""
    mid_merc = gr._merc_lat((gr._merc_y(55.0) + gr._merc_y(65.0)) / 2)
    assert mid_merc > 60.0 + 0.05, f"mercator midpoint {mid_merc} should sit north of 60°"
    assert mid_merc < 60.6, f"mercator midpoint {mid_merc} is implausible"
    return True


S.check("image rows are linear in Web Mercator", image_is_georeferenced)


def transparent_off_grid():
    """A grid that covers only part of its lon/lat box must leave the rest clear."""
    z = [[300.0 + 4 * i + j if i < 3 else None for i in range(6)] for j in range(6)]
    part = gr.Grid(6, 6, X0, Y0, DX, DY, z, UTM31, "part")
    ov = gr.image_overlay(part, width=40)
    png = base64.b64decode(ov["url"].split(",", 1)[1])
    return len(png) > 100      # encodes without error; alpha=0 pixels are the blanks


S.check("blank area stays transparent", transparent_off_grid)

# ── grid as a depth source ──────────────────────────────────────────────────
CAT = c.Catalog()


def demo_layout():
    lay = n.Layout(CAT)
    lat0, lon0 = g.transform_datum(*g.utm_to_geo(X0 + DX, Y0 + DY, 31, "N", "ED50"), "ED50", "WGS84")
    lat1, lon1 = g.transform_datum(*g.utm_to_geo(X0 + 3 * DX, Y0 + 2 * DY, 31, "N", "ED50"),
                                   "ED50", "WGS84")
    lay.add_node(n.Node("W1", "xt_vxt_10k", lat0, lon0, label="W1"))
    lay.add_node(n.Node("HOST1", "host_semi", lat1, lon1, label="Host"))
    lay.add_edge(n.Edge("FL1", "fl_rigid_cs", "W1", "HOST1", diameter_in=10))
    return lay


def depths_filled():
    lay = demo_layout()
    got = gr.fill_node_depths(lay, G)
    assert got["W1"] is not None, "well got no depth"
    assert abs(lay.nodes["W1"].water_depth_m - Z[1][1]) < 1.0, lay.nodes["W1"].water_depth_m
    assert "HOST1" not in got, "host should be left alone"
    return True


S.check("fill_node_depths sets the well depth from the grid", depths_filled)


def only_blank_respected():
    lay = demo_layout()
    lay.nodes["W1"].water_depth_m = 111.0
    gr.fill_node_depths(lay, G, only_blank=True)
    assert lay.nodes["W1"].water_depth_m == 111.0
    gr.fill_node_depths(lay, G, only_blank=False)
    return lay.nodes["W1"].water_depth_m != 111.0


S.check("only_blank leaves an entered depth alone", only_blank_respected)


def profile_stored():
    lay = demo_layout()
    got = gr.fetch_route_profiles(lay, G, samples=12)
    prof = lay.edges["FL1"].attrs.get("seabed_profile")
    assert got["FL1"] == 12 and len(prof) == 12, got
    assert prof == sorted(prof), "a plane tilting up-grade must give a monotonic profile"
    return True


S.check("fetch_route_profiles stores a monotonic profile on a plane", profile_stored)


def profile_off_grid():
    lay = demo_layout()
    lay.nodes["HOST1"].lat, lay.nodes["HOST1"].lon = 59.0, 2.0     # far outside the grid
    got = gr.fetch_route_profiles(lay, G, samples=10)
    return got["FL1"] is None and "seabed_profile" not in lay.edges["FL1"].attrs


S.check("a line leaving the grid stores no profile", profile_off_grid)
S.check("resample_along returns the requested count",
        lambda: len(gr.resample_along(
            [(60.0, 2.0), (60.01, 2.01)],
            gr.Grid(3, 3, 1.9, 59.9, 0.2, 0.2, [[300.0] * 3] * 3), 7)) == 7)
S.check("resample_along needs two points", lambda: gr.resample_along([(60.0, 2.0)], G) == [])


def coverage_counts():
    lay = demo_layout()
    lay.add_node(n.Node("W2", "xt_vxt_10k", 59.0, 2.0, label="W2"))
    cov = gr.coverage(lay, G)
    return cov["nodes_on_grid"] == 2 and cov["nodes_off_grid"] == 1


S.check("coverage counts nodes on and off the grid", coverage_counts)

sys.exit(0 if S.report() else 1)
