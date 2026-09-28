"""v0.22: template depths belong to the template's site; imported layers can be restyled."""
import sys, copy, yaml
import tb_network as n, tb_catalog as c, tb_bathymetry as B, tb_mapextras as mx
from _harness import Suite
S = Suite("test_v022")


class _Resp:
    def __init__(self, d): self.d = d
    def raise_for_status(self): pass
    def json(self): return self.d


class _Sess:
    headers = {}
    def get(self, url, params=None, timeout=None):
        return _Resp({"smoothed": -321.0})


def tpl(name="phased_with_boosting.yaml"):
    return n.Layout.from_dict(yaml.safe_load(open(f"templates/{name}")), c.Catalog())


def subsea(lay):
    return [k for k in lay.nodes if lay.kind(k) != "host"]


def template_depths_cleared_when_placed():
    lay = tpl()
    assert any(lay.nodes[k].water_depth_m == 150 for k in subsea(lay)), "the fixture should carry 150 m"
    lay.edges[next(iter(lay.edges))].attrs["seabed_profile"] = [150.0] * 5
    lay.place_split(65.4, 7.25)
    assert all(lay.nodes[k].water_depth_m == 0 for k in subsea(lay)), \
        [(k, lay.nodes[k].water_depth_m) for k in subsea(lay)]
    return not any("seabed_profile" in e.attrs for e in lay.edges.values())
S.check("a template placed at a new point forgets its drawing-site depths and seabed profiles",
        template_depths_cleared_when_placed)


def host_depth_applied_near_the_host():
    lay = tpl()
    hosts = [k for k in lay.nodes if lay.kind(k) == "host"]
    h = lay.nodes[hosts[0]]
    lay.place_split(65.4, 7.25, h.lat + 0.3, h.lon + 0.3, host_label="Host B", host_depth_m=345.0)
    near = [k for k in lay.host_side(hosts[0]) if lay.kind(k) != "host"]
    far = [k for k in subsea(lay) if k not in near]
    assert all(lay.nodes[k].water_depth_m == 345.0 and lay.nodes[k].attrs["depth_source"] == "host" for k in near)
    return all(lay.nodes[k].water_depth_m == 0 for k in far)
S.check("elements at a host take the host's depth; the field's are left for the seabed fill",
        host_depth_applied_near_the_host)


def move_layout_clears_depths():
    lay = tpl()
    lay.place_at(62.0, 4.0)
    return all(lay.nodes[k].water_depth_m == 0 for k in subsea(lay))
S.check("moving the whole layout clears the depths of the old site", move_layout_clears_depths)


def fill_keeps_only_typed_depths():
    lay = tpl()
    ids = subsea(lay)
    lay.nodes[ids[0]].attrs["depth_source"] = "manual"
    lay.nodes[ids[0]].water_depth_m = 222.0
    lay.nodes[ids[1]].water_depth_m = 150.0          # a template depth: not yours
    B.fill_node_depths(lay, _Sess(), only_blank=False, keep_manual=True)
    assert lay.nodes[ids[0]].water_depth_m == 222.0, "a typed depth is kept"
    assert lay.nodes[ids[1]].water_depth_m == 321.0 and lay.nodes[ids[1]].attrs["depth_source"] == "emodnet"
    B.fill_node_depths(lay, _Sess(), only_blank=False, keep_manual=False)
    assert lay.nodes[ids[0]].water_depth_m == 321.0, "'All' overwrites everything"
    lay.nodes[ids[1]].water_depth_m = 99.0
    B.fill_node_depths(lay, _Sess(), only_blank=True)
    return lay.nodes[ids[1]].water_depth_m == 99.0
S.check("depth fill: 'all except typed' replaces template depths, keeps yours; 'only empty' keeps all",
        fill_keeps_only_typed_depths)


def grid_fill_keeps_typed():
    import tb_grid
    lay = tpl()
    ids = subsea(lay)
    lay.nodes[ids[0]].attrs["depth_source"] = "manual"
    lay.nodes[ids[0]].water_depth_m = 222.0

    class G:
        name = "g"
        def depth_convention(self): return "positive_down"
        def depth_at(self, lat, lon, conv): return 77.0
    tb_grid.fill_node_depths(lay, G(), only_blank=False, keep_manual=True)
    return lay.nodes[ids[0]].water_depth_m == 222.0 \
        and lay.nodes[ids[1]].water_depth_m == 77.0 and lay.nodes[ids[1]].attrs["depth_source"] == "grid"
S.check("the survey-grid depth fill keeps typed depths too", grid_fill_keeps_typed)


def _fc():
    poly = lambda x: {"type": "Polygon", "coordinates": [[[x, 60], [x, 61], [x + 1, 61], [x, 60]]]}
    return {"type": "FeatureCollection", "title": "lic.geojson", "rev": "abc", "color": "#4A6B82",
            "features": [{"type": "Feature", "geometry": poly(2), "properties": {"licence": "PL001", "op": "A"}},
                         {"type": "Feature", "geometry": poly(4), "properties": {"licence": "PL002", "op": "B"}},
                         {"type": "Feature", "geometry": poly(6), "properties": {"licence": "PL001", "_fill": "#123456"}}]}


def style_single_and_by_attribute():
    fc = _fc()
    assert mx.overlay_properties(fc)[0] == "licence" and "_fill" not in mx.overlay_properties(fc)
    assert mx.property_values(fc, "licence") == ["PL001", "PL002"]
    r0 = fc["rev"]
    mx.style_overlay(fc, fill_color="#EB0037", outline_color="#000000", fill_opacity=0.3, weight=2)
    assert fc["fill_mode"] == "single" and fc["fill_color"] == "#EB0037" and fc["rev"] != r0
    r1 = fc["rev"]
    mx.style_overlay(fc, by="licence", colours={"PL001": "#007079", "PL002": "#FFB000"})
    fills = [f["properties"]["_fill"] for f in fc["features"]]
    assert fills == ["#007079", "#FFB000", "#007079"] and fc["rev"] != r1
    mx.style_overlay(fc, by="op", colours={"A": "#111111"})
    assert [f["properties"]["_fill"] for f in fc["features"]] == ["#111111", "#BFBFBF", "#BFBFBF"]
    mx.reset_overlay_style(fc)
    assert fc["features"][2]["properties"]["_fill"] == "#123456", "the layer's own colour comes back"
    assert "_fill" not in fc["features"][0]["properties"] and "fill_mode" not in fc
    return fc["rev"].startswith("abc:")
S.check("an imported layer takes one colour or a colour per attribute value, and resets", style_single_and_by_attribute)
S.raises("a colour that is not #RRGGBB is refused", ValueError, lambda: mx.style_overlay(_fc(), fill_color="red"))
S.raises("a bad category colour is refused", ValueError,
         lambda: mx.style_overlay(_fc(), by="licence", colours={"PL001": "blue"}))
S.raises("an opacity above 1 is refused", ValueError, lambda: mx.style_overlay(_fc(), fill_opacity=1.5))

sys.exit(0 if S.report() else 1)
