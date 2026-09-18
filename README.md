# TieBack Studio

Subsea tie-back concept design for the Norwegian Continental Shelf: drag-and-drop layout on a live
NCS map, equipment catalog with editable costs, design checks, CAPEX with P10/P50/P90, a CPM
schedule with weather windows, and steady-state flow assurance screening on the layout network.

> Engineering screening tool. Default catalog rates are **indicative placeholders**, not benchmarked
> data — load a project cost library for real work. Not affiliated with or endorsed by Equinor or Sodir.

## Run
```bash
pip install -r requirements.txt
streamlit run tieback_app.py
```
The map loads Leaflet from cdnjs.cloudflare.com and basemap tiles from Esri/OpenStreetMap; NCS layers
come from Sodir FactMaps. All need internet access (Streamlit Community Cloud has it).

## Using the map
| Tool | Action |
|---|---|
| Select | Click to select. `Del` deletes, `Esc` returns to Select. |
| Move | Drag equipment to reposition it. A structure carries its jumpered wells and modules unless *with wells* is unticked. |
| Size / Lines sliders | Scale equipment symbols and line thickness on the map; the setting is saved with the project. |
| Pick point | Click the map to set a placement point: load a concept template there, move the whole layout to it, or send the selected item to it. |
| Layer control (top right) | Basemap, EMODnet bathymetry and depth contours, plus any NCS or imported layer. |
| Place | Choose equipment, then click the map. |
| Connect | Choose a connection type (and ID), click the first item then the second. Invalid pairs are blocked. |
| Edit route | Select a line, click it to add a bend, drag bends, right-click a bend to remove it. *Smooth the route* lays it as a curve through those bends. |

Properties (label, coordinates in lat/lon or UTM, water depth, heading, SITP, HIPPS, phase, diameter,
fixed length, route bends) are edited below the map; bulk edits in *All equipment*. Lines sharing the
same two end points are drawn side by side, and *Copy route from* runs a new line (chemical, gas lift,
fibre) along an existing corridor.

Appearance (sidebar) sets how lines are coloured — by equipment type, by fluid or service
(multiphase, oil, gas, condensate, water injection, gas lift, chemical, control, power), by development
phase, or by design-check severity — with a colour picker per fluid, symbol and line scaling, and an
option to drop the bore-size term from line thickness. Each line's fluid is set in its property editor
and defaults from its equipment type.

Routes are either cornered (straight legs) or smoothed — a centripetal Catmull-Rom curve through the
surveyed bends, which is what the line length, cost and flow assurance then use. Bends tighter than the
minimum lay radius (sidebar, default 400 m) are flagged in the design checks; the radius is always
measured on the laid curve.

Equipment is drawn as plan-view symbols — XT, template with slots, manifold with header, PLET, SSIV,
pump skid, jacket, semi-sub, FPSO — and at zoom 14 and closer each item also shows its true-scale
footprint from the catalog, rotated by its heading.

Utility lines can be strapped to a flowline or riser (*Strapped to (piggyback)* in the line editor):
they follow the carrier's corridor, ride its lay campaign and add only a share of the lay time.

Line types: production flowlines (CS, CRA, PiP, DEH, flexible), risers, jumpers, umbilicals (static,
dynamic, with power cores), power cables, and utility lines — chemical injection, gas lift, water
injection, hydraulic/service and fibre optic. Utility lines carry no production, so they are excluded
from the production-path checks and the flow-assurance network but are costed and scheduled.

## Structure
| File | Scope |
|---|---|
| `tieback_app.py` | Streamlit UI only |
| `tb_map.py`, `tb_map_component/` | Custom bi-directional Leaflet component (Streamlit v1 protocol, no npm build) and the Python event reducer |
| `tb_tiein.py` | Tie-in screening: Sodir facilities or layout hosts as candidate hosts, distance and bearing, trial tie-back costed and solved, and one-click attach to the layout |
| `tb_basis.py` | Design basis checklist in SI units (m, bar, °C, Sm³/d, tonn, MNOK) with entered / default / missing / to-resolve status |
| `tb_cases.py` | Concept cases: snapshot a whole project, compare cases on cost, schedule and flow assurance, save/load case sets |
| `tb_report.py` | DG2/DG3 screening report as a Word document with charts |
| `tb_well.py` | Well IPR (PI, Vogel, gas back-pressure), tubing VLP via Beggs-Brill, and the operating point against network back-pressure |
| `tb_costio.py` | Cost catalog import/export as Excel workbook or CSV pair |
| `tb_bathymetry.py` | EMODnet Bathymetry: WMS layers, `depth_sample` for node depths, route profiles along each line, taut-string free-span screening |
| `tb_ncs.py` | Sodir FactMaps client (fields 502, discoveries 503, facilities 304, pipelines 311, wellbores 204/205, facilities 304 in place / 307 all, discoveries 503 active / 504 all, licences 616, blocks 802, quadrants 803, structural elements 704), pagination past 1000 records, Esri-JSON fallback, layer re-discovery, and the service's own renderer so overlays match FactMaps symbology |
| `tb_import.py` | GeoJSON, KML/KMZ, zipped shapefile (pure Python), CSV points; ED50/WGS84 geographic + UTM; layout → GeoJSON |
| `tb_geo.py` | UTM/TM (Krüger 6th order), ED50↔WGS84 Helmert, Vincenty, route lengths |
| `tb_catalog.py` | 24 equipment items, 6 vessel spreads, overrides, uncertainty, connection rules, YAML library |
| `tb_network.py` | Layout graph, design checks, quantity take-off |
| `tb_schedule.py` | CPM with NCS weather windows, generated tie-back activity network |
| `tb_cost.py` | Cost build-up, correlated Monte Carlo, schedule-driven phasing |
| `tb_multiphase.py` | Beggs & Brill (revised, Payne) gradient and black-oil properties — ported from PVT Studio `nodal.py` with corrections (see `docs/PVT_STUDIO_NODAL_FIXES.md`) |
| `tb_thermal.py` | Analytic pipe heat loss, lumped cool-down, Towler-Mokhatab hydrates, Hammerschmidt inhibition |
| `tb_flowassurance.py` | Network solver: blended streams, coupled temperature (downstream, with Joule-Thomson) and pressure (upstream) passes; deliverability, hydrate, cool-down, erosion, slugging and host-capacity checks; line-size sweep, turndown sensitivity and IPR-coupled nodal solve |
| `tb_project.py` | Project save/load (layout + catalog + cost, schedule and flow assurance settings in one YAML) |

Internal units: metres, inches (ID), psi, days, USD.

## Starting points
- `templates/` — five concept templates (satellite, daisy chain, dual flowline loop, phased with
  boosting, deepwater FPSO cluster). Load one from the sidebar, placed at the point you picked on the
  map, at the centre of the current view, or at its own coordinates. Placement keeps distances, so a
  concept moved from 60°N to 71°N holds its line lengths.
- `library/cost_library_template.yaml` and `.xlsx` — cost catalog to fill in with your own rates
  (YAML, Excel or CSV all import from the Equipment catalog tab).
- `tools/pvt_studio_selftest.py` — run against PVT Studio's `nodal.py` to check the Beggs-Brill fixes.
- `docs/ROADMAP.md` — the improvement plan, easiest first.

## Tie-in screening
Load the Sodir facility layers, select a template or manifold, and screen it: every candidate host within
the search radius is ranked by distance, with bearing, line length, a costed trial tie-back and — when the
structure has wells — required wellhead pressure, arrival temperature and hydrate margin. *Add to layout*
builds the chosen tie-back (host, riser base, PLETs, riser, flowline, umbilical) into the project.

If an NCS layer comes back empty the app now says so: a layout near the median line often has no
Norwegian facility inside the default 40 km radius.

## If the app fails to start
The sidebar has a **Diagnostics** panel: app version, Python and Streamlit versions, whether the demo
file is present and what its first line is, how many templates were found, and whether any module is
older than the app. A demo file that cannot be parsed no longer stops start-up — the app opens with an
empty layout and explains why. Project, layout, catalog and case-set files load even if the `schema:`
line has been lost, if the file has a byte-order mark, or if it uses Windows line endings.

When deploying to Streamlit Community Cloud, upload the whole folder — `test_fixtures/`, `templates/`,
`library/` and `tb_map_component/` included — and make sure `requirements.txt` is the current one
(openpyxl, python-docx and matplotlib were added in v0.7).

## Tests
`python run_tests.py` (needs `scipy` for reference integrals and Node ≥ 18 for the JS suites)

| Suite | Checks |
|---|---|
| geo / catalog / network / schedule / cost | 29 / 19 / 43 / 25 / 22 |
| well (IPR/VLP) / cost spreadsheet IO / cases / report | 22 / 12 / 11 / 7 |
| tie-in screening / design basis | 13 / 21 |
| map bridge | 33 |
| ncs / import / flow assurance | 23 / 27 / 43 |
| multiphase / thermal / bathymetry | 31 / 25 / 22 |
| JS core logic / component protocol simulation | 41 / 34 |
| Headless UI (stub Streamlit, scripted interactions) | 44 |

The protocol test runs the real component script against a fake DOM and fake Leaflet; the UI test
executes `tieback_app.py` with a stub Streamlit. Neither replaces a check in a real browser.

## Known limits
- Flow assurance draws a longitudinal section (seabed, line and riser with pressure/temperature) and a
  pipe cross-section build-up from the catalog (bore, wall or armour, insulation, coating, carrier pipe).
  The cross-section is schematic: wall thickness is a catalog input, not a pressure-containment calculation.
- Bathymetry (EMODnet DTM, ~115 m grid, LAT datum) is indicative: use the project survey for design.
  Free spans use a taut-string model with no pipe stiffness or weight — survey candidates, not design spans.
- Sodir's oil/gas and gas/condensate classes use picture fills; the map approximates them with hatch
  patterns. Solid classes (oil green, gas red) come straight from the service renderer.
- Drilling & completion cost excluded (FieldVista owns well costs).
- Datum shift uses EPSG:1133 (~10 m). Sodir's WGS84 layers use ESRI ED_1950_To_WGS_1984_18, so ED50
  layouts can sit a few metres off the NCS overlays — fine for screening, not for survey work.
- ED50 layouts are converted to WGS84 for the map and back on every map edit.
- Backward pass ignores weather windows → float on windowed activities is indicative.
- Flow assurance is steady-state screening: Beggs & Brill only (no Hagedorn-Brown/Gray yet), black-oil
  fluid, tree networks only (no loops or choke modelling), one host arrival pressure. Joule-Thomson
  cooling is included via a real-gas coefficient from the Z-factor derivative. Slugging is an indicator
  (riser flow pattern and gas velocity), not a transient simulation, and turndown is a set of steady
  states rather than a ramp. Use OLGA/LedaFlow/PIPESIM for design.
- Nodal deliverability linearises the network around the current rates between iterations; check that
  the reported solve converged before trusting the rates.
