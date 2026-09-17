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
| Select | Click to select; drag equipment to move it. `Del` deletes, `Esc` returns to Select. |
| Place | Choose equipment, then click the map. |
| Connect | Choose a connection type (and ID), click the first item then the second. Invalid pairs are blocked. |
| Edit route | Select a line, click it to add a bend, drag bends, right-click a bend to remove it. |

Properties (label, coordinates in lat/lon or UTM, SITP, HIPPS, phase, diameter, fixed length, route
bends) are edited below the map; bulk edits in *All equipment*.

## Structure
| File | Scope |
|---|---|
| `tieback_app.py` | Streamlit UI only |
| `tb_map.py`, `tb_map_component/` | Custom bi-directional Leaflet component (Streamlit v1 protocol, no npm build) and the Python event reducer |
| `tb_ncs.py` | Sodir FactMaps client (fields 502, discoveries 503, facilities 304, pipelines 311, wellbores 204/205, licences 616, blocks 802, quadrants 803, structural elements 704), pagination past 1000 records, Esri-JSON fallback, layer re-discovery |
| `tb_import.py` | GeoJSON, KML/KMZ, zipped shapefile (pure Python), CSV points; ED50/WGS84 geographic + UTM; layout → GeoJSON |
| `tb_geo.py` | UTM/TM (Krüger 6th order), ED50↔WGS84 Helmert, Vincenty, route lengths |
| `tb_catalog.py` | 24 equipment items, 6 vessel spreads, overrides, uncertainty, connection rules, YAML library |
| `tb_network.py` | Layout graph, design checks, quantity take-off |
| `tb_schedule.py` | CPM with NCS weather windows, generated tie-back activity network |
| `tb_cost.py` | Cost build-up, correlated Monte Carlo, schedule-driven phasing |
| `tb_multiphase.py` | Beggs & Brill (revised, Payne) gradient and black-oil properties — ported from PVT Studio `nodal.py` with corrections (see `docs/PVT_STUDIO_NODAL_FIXES.md`) |
| `tb_thermal.py` | Analytic pipe heat loss, lumped cool-down, Towler-Mokhatab hydrates, Hammerschmidt inhibition |
| `tb_flowassurance.py` | Network solver on the layout: blended streams, T marched downstream, P marched upstream from host arrival; deliverability, hydrate, cool-down, erosion and host-capacity checks; line-size sweep |
| `tb_project.py` | Project save/load (layout + catalog + cost, schedule and flow assurance settings in one YAML) |

Internal units: metres, inches (ID), psi, days, USD.

## Tests
`python run_tests.py` (needs `scipy` for reference integrals and Node ≥ 18 for the JS suites)

| Suite | Checks |
|---|---|
| geo / catalog / network / schedule / cost | 22 / 19 / 25 / 25 / 18 |
| ncs / import / map + project | 14 / 27 / 19 |
| multiphase / thermal / flow assurance | 31 / 20 / 25 |
| JS core logic / component protocol simulation | 26 / 21 |
| Headless UI (stub Streamlit, scripted interactions) | 26 |

The protocol test runs the real component script against a fake DOM and fake Leaflet; the UI test
executes `tieback_app.py` with a stub Streamlit. Neither replaces a check in a real browser.

## Known limits
- Drilling & completion cost excluded (FieldVista owns well costs).
- Datum shift uses EPSG:1133 (~10 m). Sodir's WGS84 layers use ESRI ED_1950_To_WGS_1984_18, so ED50
  layouts can sit a few metres off the NCS overlays — fine for screening, not for survey work.
- ED50 layouts are converted to WGS84 for the map and back on every map edit.
- Backward pass ignores weather windows → float on windowed activities is indicative.
- Flow assurance is steady-state screening: Beggs & Brill only (no Hagedorn-Brown/Gray yet), black-oil
  fluid, no Joule-Thomson cooling, no slugging/transient analysis, tree networks only (no loops or
  choke modelling), one host arrival pressure. Use OLGA/LedaFlow/PIPESIM for design.
