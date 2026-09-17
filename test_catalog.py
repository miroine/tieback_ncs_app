import sys
import tb_catalog as c
from _harness import Suite
S = Suite("test_catalog")
cat = c.Catalog()

S.check("defaults load (>= 20 items)", lambda: len(cat.items) >= 20)
S.check("every node & edge kind except none has >=1 item",
        lambda: all(cat.by_category(k) for k in c.NODE_KINDS + c.EDGE_KINDS))
S.check("all item spreads defined", lambda: all(i.install_spread in cat.spreads for i in cat.items.values()))
def yaml_rt():
    c2 = c.Catalog.from_yaml(cat.to_yaml())
    assert c2.to_dict() == cat.to_dict()
S.check("YAML round-trip identical", yaml_rt)
def override():
    c2 = c.Catalog()
    it = c2.override("fl_rigid_cs", procurement_usd=120.0, uncertainty=(0.8, 1.0, 1.5))
    assert it.procurement_usd == 120.0 and c2.get("fl_rigid_cs").uncertainty == (0.8, 1.0, 1.5)
    assert cat.get("fl_rigid_cs").procurement_usd == 95  # defaults untouched
S.check("override applies and does not mutate defaults", override)
S.raises("override negative cost raises", ValueError, lambda: c.Catalog().override("plet_std", procurement_usd=-1))
S.raises("override unknown field raises", ValueError, lambda: c.Catalog().override("plet_std", colour="red"))
S.raises("override item_id raises", ValueError, lambda: c.Catalog().override("plet_std", item_id="x"))
S.raises("bad uncertainty raises", ValueError,
         lambda: c.CatalogItem("x", "x", "plet", uncertainty=(1.2, 1.0, 1.3)))
S.raises("linear item on unit basis raises", ValueError,
         lambda: c.CatalogItem("x", "x", "flowline", cost_basis="unit"))
S.raises("unknown category raises", ValueError, lambda: c.CatalogItem("x", "x", "spaceship"))
S.raises("duplicate id raises", ValueError, lambda: c.Catalog().add(c.CatalogItem("plet_std", "dup", "plet")))
S.raises("undefined spread raises", ValueError,
         lambda: c.Catalog(items=[c.CatalogItem("x", "x", "plet", install_spread="nope")]))
S.raises("missing item raises KeyError", KeyError, lambda: cat.get("nope"))
S.check("jumper well↔template allowed", lambda: c.edge_allowed("jumper", "well", "template"))
S.check("jumper symmetric", lambda: c.edge_allowed("jumper", "template", "well"))
S.check("riser well↔host not allowed", lambda: not c.edge_allowed("riser", "well", "host"))
S.check("power cable to PLET not allowed", lambda: not c.edge_allowed("power_cable", "plet", "host"))
S.raises("unsupported schema raises", ValueError, lambda: c.Catalog.from_dict({"schema": "x"}))
sys.exit(0 if S.report() else 1)
