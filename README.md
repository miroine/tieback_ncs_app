# TieBack Studio

Subsea tie-back concept design for the Norwegian Continental Shelf: drag-and-drop layout on a live
NCS map, equipment catalog with editable costs, design checks, CAPEX with P10/P50/P90, a CPM
schedule with weather windows, and steady-state flow assurance screening on the layout network.

**Created by Merouane Hamdani.** © 2026 Merouane Hamdani. All rights reserved.

> **Prototype — early-phase concept planning and screening only.** It is not engineering software:
> **do not use it on commercial projects**, or as a basis for investment, procurement, design,
> operational or safety decisions. Default catalog rates are **indicative placeholders**, not
> benchmarked data, and the physics is correlation-level screening. Confirm every number with proper
> engineering tools and the operator's own data.
>
> **Licence:** source-available, **attribution required**, non-commercial — see [LICENSE](LICENSE) and
> [NOTICE](NOTICE). Copying, forking or reusing the code, or using its figures and numbers in a
> document, requires crediting *TieBack Studio — created by Merouane Hamdani*; commercial use needs
> the author's written permission.
>
> Independent personal project. Not affiliated with, endorsed by, or representing Equinor, Sodir or
> any other organisation whose public data it reads.

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
| Move | Drag equipment to reposition it. Shift-click (or Ctrl/Cmd-click) several items to move them as a set. A structure carries its jumpered wells and modules unless *with wells* is unticked. |
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

Equipment carries free-text **tags**, set in its properties. The sidebar filters the map on them —
*Show only* and *Hide* — so a phase, an option or a work package can be isolated without deleting
anything. **Undo** and **Redo** sit above the map and cover map edits, form changes and bulk edits,
25 steps deep.

Wells are landed in a template or manifold through *Wells in template slots*, which records the slot and
creates the integral slot tie-in rather than a fabricated spool; wells already jumpered to the structure
are shown as in-slot. A well placed within 250 m of a structure with a free slot is landed automatically.
Landed wells travel with the structure when it is dragged.

With concepts saved as cases, the sidebar picks the **active concept** — cost, schedule, flow assurance,
viability and the report all follow it, and edits are saved back when you switch away. Other concepts can
be drawn on the map behind the active one for comparison.

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
| `tb_mapextras.py` | Sketches on the map (true-ground circles, polygons, lines, geodesic lengths and areas, what equipment falls inside), bookmarks, and map layers by URL — ArcGIS tiled, map and image services, ArcGIS feature layers, WMS and XYZ, recognised by their shape |
| `tb_share.py` | Share links: the whole design compressed into the address (`?design=`), or a short link to a stored copy (`?share=`, on the server's disk or in a secret GitHub gist); protected links (`2.`) encrypted with AES-256-GCM under an scrypt-derived access code or password; catalogue sent as a diff to the default; damaged and oversized links refused in words |
| `tb_production.py` | Volumetrics, recovery-factor guidance by drainage strategy, wells needed for a plateau, and the plateau-then-decline profile per reservoir, capped by host capacity |
| `tb_economics.py` | OPEX, host tariff, abandonment and tax on top of the CAPEX profile: NPV, IRR, payback, break-even oil price, unit technical cost, and a tornado sensitivity |
| `tb_optimise.py` | Builds the variants around the concept on screen (wells, line size, loop, boosting), scores each one and keeps the ones nothing beats on both cost and value |
| `tb_theme.py` | The whole look in one place: the Equinor-derived palette, the stylesheet, the chart frame, and the author, disclaimer and licence strings the app and the report show |
| `tb_shutdown.py` | Planned-shutdown sequence (inhibit or displace, close in, depressurise) and blowdown: hydrate-free pressure, liquid-head floor in the riser, venting time through the host restriction |
| `tb_fluids.py` | Reservoirs and each well's main fluid (oil, gas, gas condensate, water or gas injector): stated on the well, inherited from its reservoir, or classified from GOR by McCain's ranges — always reported with which of the three it came from; lines take the fluid of the wells upstream; reservoir PVT copied into the well streams; gas wells by gas rate and CGR |
| `tb_chemistry.py` | Production chemistry: MEG vs methanol sizing and recommendation (Hammerschmidt inverted, Nielsen-Bucklin cross-check, regeneration credit, life cost), and a screen for wax, asphaltenes, scale, emulsions, corrosion, souring, sand and naphthenates that names the test when the data is missing |
| `tb_viability.py` | Concept viability checklist: layout integrity, deliverability, hydrate margin at design rate and turndown, cool-down, erosion, slugging, spans, schedule float and cost spread, each with a target and what to do if it is not met |
| `tb_cases.py` | Concept cases: snapshot a whole project, compare cases on cost, schedule and flow assurance, save/load case sets |
| `tb_report.py` | DG2/DG3 screening report as a Word document with charts |
| `tb_well.py` | Well IPR (PI, Vogel, gas back-pressure), tubing VLP via Beggs-Brill, and the operating point against network back-pressure |
| `tb_costio.py` | Cost catalog import/export as Excel workbook or CSV pair |
| `tb_bathymetry.py` | EMODnet Bathymetry: WMS layers, `depth_sample` for node depths, route profiles along each line, taut-string free-span screening |
| `tb_ncs.py` | Sodir FactMaps client (fields 502, discoveries 503, facilities 304, pipelines 311, wellbores 204/205, facilities 304 in place / 307 all, discoveries 503 active / 504 all, licences 616, blocks 802, quadrants 803, structural elements 704), pagination past 1000 records, Esri-JSON fallback, layer re-discovery, and the service's own renderer so overlays match FactMaps symbology |
| `tb_import.py` | GeoJSON, KML/KMZ, shapefile — zipped or as loose `.shp`/`.dbf`/`.prj` — CSV points; multi-file selections grouped by stem; ED50/WGS84 geographic + UTM; layout → GeoJSON |
| `tb_grid.py` | Grid surfaces (`.grd` and friends): Surfer ASCII/6/7, IRAP classic ASCII, ZMAP+, ESRI ASCII — sniffed by content; bilinear sampling, marching-squares contours, colour image overlay resampled into Web Mercator, and the grid as a depth source in place of EMODnet |
| `tb_geo.py` | UTM/TM (Krüger 6th order), ED50↔WGS84 Helmert, Vincenty, route lengths |
| `tb_catalog.py` | 34 equipment items, 6 vessel spreads, overrides, uncertainty, connection rules, YAML library |
| `tb_network.py` | Layout graph, design checks, quantity take-off |
| `tb_schedule.py` | CPM with NCS weather windows, generated tie-back activity network |
| `tb_cost.py` | Cost build-up, correlated Monte Carlo, schedule-driven phasing |
| `tb_multiphase.py` | Beggs & Brill (revised, Payne) gradient and black-oil properties — ported from PVT Studio `nodal.py` with corrections (see `docs/PVT_STUDIO_NODAL_FIXES.md`) |
| `tb_thermal.py` | Analytic pipe heat loss, lumped cool-down, Towler-Mokhatab hydrates, Hammerschmidt inhibition |
| `tb_flowassurance.py` | Network solver: blended streams, coupled temperature (downstream, with Joule-Thomson) and pressure (upstream) passes; deliverability, hydrate, cool-down, erosion, slugging and host-capacity checks; line-size sweep, turndown sensitivity and IPR-coupled nodal solve |
| `tb_project.py` | Project save/load (layout + catalog + cost, schedule and flow assurance settings in one YAML) |

Internal units: metres, inches (ID), psi, days, USD.

## Starting points
The app opens on an **empty map** framing the whole shelf. *Go to* above the map jumps to the **North
Sea**, **Norwegian Sea** or **Barents Sea** (or back to the whole NCS); *Load demo* in the sidebar opens
the example field.

- `templates/` — five concept templates (satellite, daisy chain, dual flowline loop, phased with
  boosting, deepwater FPSO cluster). Load one from the sidebar at the point you picked on the map, at the
  centre of the view, or at its own coordinates. The **field** — its first template, manifold or well —
  goes to that point. **Tie back to** lists the hosts within 150 km of it, nearest first: Sodir
  facilities loaded on the map, hosts in the layout, and hosts in saved concepts (*Find hosts near this
  point* loads Sodir's facilities round it if none are loaded). The template's host, riser base and
  host-end PLET move onto the chosen host, taking its name and water depth; the lines between field and
  host are redrawn straight at the new distance. *Template's own host* keeps the template's tie-back
  distance instead. Placement keeps the field's shape in metres, so a concept moved from 60°N to 71°N
  holds its line lengths.
- `library/cost_library_template.yaml` and `.xlsx` — cost catalog to fill in with your own rates
  (YAML, Excel or CSV all import from the Equipment catalog tab).
- `tools/pvt_studio_selftest.py` — run against PVT Studio's `nodal.py` to check the Beggs-Brill fixes.
- `docs/ROADMAP.md` — the improvement plan, easiest first.

## Map tools
The toolbar has **Measure**, **Circle**, **Polygon** and **Line**. Measure is a scratch tool: click along
the way, double-click to finish, Esc to clear — nothing is saved. Circle, Polygon and Line are
*sketches*: saved with the project, its concepts and a shared link, drawn above the routes and below the
equipment, and listed under *Map tools* below the map with their size and the equipment that falls
inside them. A circle is a true circle on the ground (at 60°N it is twice as wide in longitude as in
latitude on the map); lengths and areas are geodesic. *Circle round selected* puts, say, a 500 m safety
zone round a template in one click. Clicking a sketch selects it; Del removes it.

**Bookmark** saves the view you are looking at, with its base map; *Go* in the Bookmarks tab returns to
it. The map's layer control now offers Esri Ocean, Satellite, Topographic, Light grey and Dark grey,
OpenStreetMap, and Kartverket's **Sjøkart**, Topografisk and Gråtone, plus GEBCO bathymetry and
OpenSeaMap sea marks as overlays. The base map you choose is remembered with the project.

**Add a map by URL** takes an ArcGIS Online / ArcGIS Server address — a tiled service
(`…/MapServer/tile`), a map or image service (`…/MapServer`, drawn tile by tile through `export`), or a
feature layer (`…/FeatureServer/3`, fetched as vectors in view) — a WMS, or XYZ tiles. The address is
recognised by its shape and an unrecognised one is refused with the accepted forms. `http://` services
are refused because the browser blocks them on an https page. Services that need a login will not draw.

## Sharing a design
Streamlit keeps everything in the browser session of whoever is using it, so a colleague opening the
app's address gets an empty app. *Share this design* in the sidebar makes a link that opens yours: the
layout, reservoirs, sketches, bookmarks, base map, colour mode and settings — optionally the saved
concepts too — at the view you were looking at. The design travels **inside the link** (`?design=`),
compressed, with the equipment catalogue sent only as its differences from the default; the demo field
comes to about 2 000 characters. Nothing is stored anywhere. Past 6 000 characters some mail and chat
tools cut addresses, so the app warns you and offers **Short link** (`?share=`), which stores the design
and links to it by id — on the server's disk, which Streamlit Community Cloud wipes on restart, or in a
secret GitHub gist if a `github_gist_token` (gist scope) is set in the app's secrets.

Whoever opens a link gets **their own copy**: their edits stay with them, yours are unchanged, and a
later change of yours needs a new link. A damaged or truncated link is reported and the app starts
normally.

### Protecting a link with a code
By default *Share this design* makes a **protected link** (`?design=2.…`). The design is encrypted with
AES-256-GCM under a key derived from an access code by scrypt, so the link — and a short link's stored
copy on the server or in a gist — holds only ciphertext; not even the project name is readable. The
colleague who opens it is asked for the code; a wrong code is refused (and a link altered in any way,
header included, fails the same check), a correct one opens the design as usual.

* **Generate a code** (default): 80 random bits shown as `K7QM-9XRT-4HPW-2DNC`. Case, spaces and dashes
  do not matter when it is typed, and O/0, I/L/1 are read the same. *New code* makes another.
* **Choose my own password**: at least 10 characters, case-sensitive; a few unrelated words works well.

Send the code **by a different channel** than the link (link by e-mail, code by Teams, SMS or phone).
What the protection rests on:

* The code's strength. Whoever holds the link can try codes offline, at their own speed; scrypt only
  makes each guess slow. A generated code is beyond that; a short password is not — hence the minimum.
* The two channels staying separate. Link and code in the same message is a plain link.
* The server. The app decrypts on its server while the design is open, so whoever runs it (on Streamlit
  Community Cloud, Streamlit) could in principle see it. For data that must stay inside the company,
  host the app on the company network.

Protected links need the `cryptography` package (in `requirements.txt`). **No code — anonymised data
only** still makes a plain link: anyone who has it can open the design, and "secret" gists are
unlisted, not private — keep real field data out of plain links.

## Duplicating
*Duplicate* on the map toolbar copies whatever is selected — one item, or a shift-click selection —
and there is the same button in the selected-item panel. With *with wells* ticked a structure brings
the wells and modules jumpered to it or landed in its slots, so duplicating a template gives a
template with its wells. Every line **between** copied items comes too; lines to anything outside the
copy do not, so a duplicated cluster is not wired to the original host until you connect it. The copy
lands just east of the original, clear of it, and arrives selected so you can drag it straight into
place. It carries equipment, fluid, reservoir, flow-assurance inputs and tags, independently of the
original; labels become *A-1 (2)*, *A-1 (3)*; slot assignments follow the copied structure; stored
seabed profiles are dropped because they describe the old route. Duplicate is undoable.

## Reservoirs and well fluids
Every well has a main fluid — oil, gas, gas condensate, water injector or gas injector — decided in
three layers, most specific first: **set on the well**, **inherited from its reservoir**, or
**classified from its own GOR** using McCain's producing-GOR ranges. The app always shows which layer
the answer came from, so an inference is never presented as a decision; a well with none of the three
is marked *not assigned* and left uncoloured rather than being drawn as oil by default.

Reservoirs live under *Reservoirs and well fluids* below the map. A new one starts from typical NCS
values for its fluid type, to be replaced from the PVT report, and a reservoir whose stated fluid the
GOR contradicts (a "black oil" at 5 000 Sm³/Sm³) is flagged — that is nearly always a typo, and it
would drive every flow-assurance number the wrong way.

On the map each well gets a ring in its fluid colour — oil green and gas red as on Sodir's maps,
condensate amber, water injection blue, gas injection purple — and in *fluid* colour mode every
production line takes the fluid of the wells upstream of it: all gas draws red, all oil green, oil and
gas commingled draws as multiphase. Injectors are taken out of the production solve and are not
flagged for having no production path, since they are fed from the host.

*Copy reservoir PVT to well streams* sets each well's GOR, API, gas gravity, water cut and salinity
from its reservoir and keeps the well's own rate — unless the well changes between oil and gas, in
which case the rate is reset and the app says so, because a liquid rate carried across with a gas GOR
would be out by an order of magnitude. Gas and condensate wells can be entered by **gas rate and
CGR** in the Flow assurance tab. Reservoir CO₂, H₂S and salinity seed the production-chemistry screen.

## Production chemistry
The Flow assurance tab ends with a production-chemistry screen. It sizes **MEG and methanol side by
side** for the subcooling you need — required concentration, injection rate, annual make-up with a
regeneration credit, and life cost including the MEG plant — and says which way the numbers point, with
the argument for each written out so it can be disagreed with. Methanol is cross-checked against
Nielsen-Bucklin, because Hammerschmidt stops being valid above about 25 wt %; where a concentration
runs past the correlation's range the row says so instead of quietly using it.

Beyond hydrates it screens **wax, gelling on shutdown, asphaltenes, scale, emulsions, internal
corrosion, sour service, reservoir souring, sand erosion, under-deposit corrosion and naphthenates**.
Where the fluid data is missing the row comes back *unknown* and names the test that would settle it —
WAT by cross-polar microscopy, SARA and onset pressure, a full water analysis — rather than guessing.
Wax appearance temperature in particular cannot be inferred from API gravity, and the screen does not
pretend otherwise.

## Editing the schedule
The activity network is generated from the layout, so it is rebuilt whenever the layout changes. The
Schedule tab's *Edit the plan* table lets you override any activity's duration, hold one back with a
*start no earlier than* date, and add your own milestones (rig contract, partner approval) on fixed
dates. Edits are stored as overrides keyed by activity id, so they survive the rebuild; an override on
an activity that no longer exists is ignored rather than breaking the schedule. Everything saves with
the project.

## Comparing concepts on the map
The sidebar has a **Concepts** section: a dropdown for the concept being edited — cost, schedule, flow
assurance, viability and the report all follow it — an *Also draw on the map* list for the others, and
*Save as concept* / *Delete concept*. A legend above the map keys the colours.

Concepts are built either by saving what is on screen, or straight from a template: *Load template*
replaces the layout, while **Add as new concept** keeps the current one as a concept and loads the
template alongside it, so a set of alternatives can be built up without losing anything. Names never
collide — loading the same template twice gives two concepts. Each concept keeps a colour of its own for as long as it is in the set, shown in a
legend under the controls, and is drawn dashed in that colour: above the routes of the concept you are
editing so it is not buried, below its equipment symbols so the one you are working on still reads as
the primary layout. Every concept is a separate entry in the map's layer control, so you can also
switch them on and off there.

Each concept keeps a colour for as long as it is in the set and is drawn dashed in that colour, above
the routes of the concept being edited and below its equipment. Concepts that overlap almost exactly
will still overlap on the map — that is the geometry, not the drawing. The **Cases** tab is where the comparison is quantitative: CAPEX, first production,
deliverability and flow-assurance margins side by side, with deltas against a baseline.

## Map layers and grid surfaces
*NCS map layers* load **around** a spot you choose: the point picked on the map (or, if none, the
selected item), the selected item, the whole layout, or the current map view. The radius is the distance
on the ground from that spot, so at 71°N the box is correctly wider in longitude.

*Import map layer* in the sidebar takes several files at once. A shapefile can be a `.zip` or the loose
parts — select `blocks.shp` together with its `.dbf` and `.prj` and they are matched by stem into one
layer. A shapefile carries no coordinate system of its own, so without a `.prj` you must choose the
source CRS (WGS84/ED50, geographic or UTM); nothing is guessed.

Grid surfaces load from the same uploader. `.grd` says nothing about what is inside it, so the file is
recognised by content, not extension: **Surfer** ASCII (`DSAA`), Surfer 6 (`DSBB`) and Surfer 7
(`DSRB`), **IRAP classic ASCII** (`-996`), **ZMAP+** and **ESRI ASCII** (`ncols`/`nrows`). A grid is
drawn two ways — a colour image and contour lines, both switchable in the map's layer control — and the
image is resampled pixel by pixel into Web Mercator, so a UTM grid lands where it belongs instead of
being stretched over its lat/lon box (at 60°N that error runs to hundreds of metres across a field).

A loaded grid is also a **depth source**. *Set element depths* fills `water_depth_m` on every subsea
element from the surface, and *Seabed profiles from grid* stores a profile along each line, which the
free-span and cool-down screening then uses instead of the regional EMODnet DTM. Say whether the values
are depths (positive down) or elevations (negative below sea level) — the app reads the sign and
proposes one, and you confirm it. A line that leaves the grid stores nothing rather than a half-covered
profile that would quietly bias the checks, and the panel reports how much of the layout the grid covers.

Two things the reader will not do: rotated grids are refused rather than drawn in the wrong place, and
IRAP classic ASCII does not record which axis cycles fastest — if a surface comes out with its axes
swapped, tick *Swap grid axes*.

## Tie-in screening
Load the Sodir facility layers, select a template or manifold, and screen it: every candidate host within
the search radius is ranked by distance, with bearing, line length, a costed trial tie-back and — when the
structure has wells — required wellhead pressure, arrival temperature and hydrate margin. *Add to layout*
builds the chosen tie-back (host, riser base, PLETs, riser, flowline, umbilical) into the project.

Candidates are filtered and de-duplicated before they are offered: facilities that are removed, shut
down or still planned are dropped (Sodir keeps decommissioned structures in the "in place" layer until
they are physically removed), mobile units are dropped, and the same facility loaded from several
layers is merged on its NPDID — or on name and position where no NPDID is published. Each row shows the
status the decision was based on, and the filters can be switched off to see everything.

**Saved concepts are candidates too** (*Include saved concepts*). Each one offers its host, and its
templates, manifolds and PLEMs as *subsea tie-in* points. A subsea tie-in shares that concept's line,
riser and host: the trial ties the structure into the other concept's structure with a new flowline and
solves both concepts' wells together, so back-pressure and host capacity reflect the shared system,
while the capex counts only the new flowline, PLETs, jumpers and umbilical. *Add to layout* copies that
concept's path to its host into the working layout (so the wells have somewhere to flow) and prices the
copied items at zero here, since they are costed in their own concept.

If an NCS layer comes back empty the app now says so: a layout near the median line often has no
Norwegian facility inside the default 40 km radius.

## Deploying — upload the whole folder
`tieback_app.py` calls into the `tb_*.py` modules, so a partial upload leaves a current app calling a
function an older module does not have. That used to crash the page with an `AttributeError` that read
like an application bug. Every cross-module call is now declared in `REQUIRED_API` in `tieback_app.py`
and checked once at start-up: an out-of-date file raises a banner naming the file and the missing
functions, the feature that needs it is turned off, and the rest of the app carries on. `ui_test/` is
part of the app too — its stub Streamlit has to mirror the API the app calls, so it moves in the same
commit.

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
| geo / catalog / network / schedule / cost | 29 / 19 / 55 / 35 / 22 |
| well (IPR/VLP) / cost spreadsheet IO / cases / report | 22 / 12 / 19 / 7 |
| production chemistry / reservoirs and well fluids / map tools and sharing | 52 / 52 / 66 |
| tie-in screening / design basis / viability | 21 / 21 / 14 |
| theme, author and licence notices | 14 |
| production, economics and the optimiser | 27 |
| share links (incl. code protection) / regions, templates-to-host, concept tie-ins, new equipment | 27 / 26 |
| shutdown and blowdown, contaminants, pressure protection, safety zones, trees and risers | 22 |
| map bridge (incl. duplicate) | 52 |
| ncs / import / grid surfaces / flow assurance | 23 / 43 / 83 / 43 |
| multiphase / thermal / bathymetry | 31 / 25 / 37 |
| JS core logic / component protocol simulation | 61 / 66 |
| Headless UI (stub Streamlit, scripted interactions) | 112 |

The protocol test runs the real component script against a fake DOM and fake Leaflet; the UI test
executes `tieback_app.py` with a stub Streamlit. Neither replaces a check in a real browser.

`ui_test/stubs.py` is part of the app, not scaffolding around it: it has to mirror the Streamlit API the
app actually calls. When you update the app, update `ui_test/` in the same commit — a stub that is a
version behind fails the build with an error in the app's own code rather than in the harness.

## Well deliverability (IPR)
In *Flow assurance → Well deliverability*, every producing well has an IPR row, ticked **Use IPR** by
default. A well with no saved IPR starts from its reservoir's pressure and temperature. *Save well IPR
data* stores the table on the wells (it travels in the project file and in share links); *Solve rates
from IPR* saves it too, then solves each well's rate from its inflow, tubing lift and the network
back-pressure and shows the result: oil and liquid rate before and after, flowing bottom-hole pressure,
drawdown and the wellhead pressure the network needs, with the IPR curves and operating points. The
design basis counts a well as having an IPR once its data is saved, and flags rows still on the
default 350 bara / 12 Sm³/d/bar.

## Pressure protection: HIPPS or fully rated
Shut-in pressure is entered in **bara** (stored internally in psi, as the catalogue ratings are). The
*Pressure protection* panel on the Layout tab walks each producing well's path to the host and compares
its shut-in pressure with the rating of every item on the way (hosts excluded — their receiving
facilities are rated by the host). The verdict per well is **fully rated**, **HIPPS in place**, or
**HIPPS or fully rated** — in which case it names what a fully rated design would have to upgrade and
where HIPPS would protect the most (the first structure the well produces into), with a *Fit HIPPS at …*
button. A HIPPS ticked on a structure is costed as the catalogue's HIPPS module. The same verdict is a
line in the design basis.

## Planned shutdown and blowdown
In the Flow assurance tab. From the solved line — volume, liquid and water inventory, settle-out
pressure — the app sizes a planned-shutdown sequence: inhibit the standing water (MEG or methanol, to
the concentration the subcooling at seabed temperature needs, at the umbilical's injection rate), or
displace an oil line with dead oil / diesel; close in; and depressurise if the stop outlasts the
cool-down time. The **blowdown** check finds the hydrate-free pressure at seabed temperature (with a
margin), the lowest pressure the host can actually reach at the seabed — flare back-pressure plus the
liquid head standing in the riser, often the show stopper on a deep or liquid-rich tie-back — and the
venting time through the host's blowdown restriction (choked, then subsonic, capped by the flare). The
line is one lumped isothermal volume and gas coming out of solution is not counted, so the time is a
lower bound: confirm it with a transient simulation.

## Contaminants and production chemistry limits
The production-chemistry table now shows the **limit** each threat was judged against. CO₂, H₂S and
mercury are checked against their limits at the highest shut-in pressure — CO₂ and H₂S by partial
pressure (pCO₂ below 0.03 bar low, 0.03–2 bar corrosive, above 2 bar severe; ISO 15156 sour service
from 0.3 kPa H₂S), mercury by content — in the flow-assurance tab and as a *Contaminants* section of the
design basis. Reservoirs carry pressure, temperature, CO₂, H₂S and mercury (*Reservoirs and well fluids*
under the map); the design basis reports reservoir pressure and temperature.

## Host safety zones
Every host is drawn with its 500 m safety zone (dashed red), switchable in the sidebar display settings
and in the map's layer control. Subsea items inside it get an information note: installation and
intervention there need the host operator's consent and simultaneous-operations planning.

## Viability: the turndown case
A tie-back is sized for its design (plateau) rate, but runs slower in late life, during well tests or
when the host cuts back. Slower flow spends longer in the cold line and arrives colder, so a line that
is clear of hydrates at plateau can fall into the hydrate region at low rate. The *Turndown case* slider
sets that lower rate as a fraction of design (0.5 = half rate); the check re-solves the layout with every
well scaled by it and reports the hydrate margin at the coldest point: **≥ 3 °C** passes, **0–3 °C** is
to resolve, **below 0 °C** blocks. The fix is a minimum operating rate, insulation, heating or
continuous inhibition. The *Flow assurance* tab runs the full sensitivity (1.0, 0.7, 0.5, 0.3).

## Equipment catalogue
Besides trees, templates, manifolds, PLET/PLEM, in-line tees, SSIVs, riser bases, hosts and the linear
items, the catalogue has: trees rated 5k, 10k (vertical and horizontal), 15k (vertical and horizontal)
and 20k psi; eight riser types — flexible, flexible lazy-wave, steel catenary (SCR), steel lazy-wave
(SLWR), top-tensioned (TTR), hybrid riser tower / free-standing, rigid riser clamped to a fixed platform,
and J-tube pull-in; PLET with isolation valve, PLET with subsea pig launcher/receiver, piggable
wye, hot-tap tie-in to an operating pipeline, subsea HIPPS module (placing it sets the node's HIPPS
flag); single-phase booster pump, single multiphase pump module, the 2-pump multiphase station, subsea
raw-seawater injection pump; wet-gas compressor module and the full compression station; gas–liquid
separator with liquid pump, compact in-line separator / de-watering unit, and separation with water
reinjection; and a new **control** group — subsea distribution unit (SDU), umbilical termination
assembly (UTA), subsea power distribution (transformer + VSD) and subsea chemical storage & injection —
which connect by umbilical, power cable or utility line, not by flowline. Also a thermoplastic composite
(TCP) flowline and a flexible jumper. Rates are indicative placeholders like the rest. A project saved by
an older version lacks the new items; the Equipment catalog tab offers to add them without touching the
project's own rates.

## When a line is too small
The upstream pressure march stops at 1 500 bara. A line that would need more is simply too small for
the rate: the flow-assurance findings say so, the size sweep marks that size *too small at this rate*
and its pressures are reported as a floor rather than a result. Correlations (Beggs & Brill, Brill &
Beggs Z) are clamped to the range they were fitted over, so an extreme case gives a boundary value
instead of an arithmetic failure.

## Production, economics and the optimiser
**Production & economics** turns a layout into a profile and a value.

* Give a reservoir its volumetrics (area, net thickness, NTG, porosity, Sw, Bo/Bg) and a drainage
  strategy under *Reservoirs and well fluids*. The app suggests a recovery-factor range for that
  strategy — solution-gas drive is not water injection — explains the number, and lets you take it or
  enter your own. In-place volume and EUR follow.
* The profile is plateau-then-decline per reservoir, from the wells assigned to it and their design
  rates, capped by the host's liquid and gas capacity. It never produces more than the EUR, and it
  says so when the cut-off rate or the horizon leaves some of it behind.
* *How many wells?* takes the plateau you want, the rate a well delivers and the area to drain, and
  reports which of the two is binding.
* Economics: oil and gas prices, fixed and variable OPEX, the host tariff, intervention days, the
  chemical bill (pre-filled from the MEG/methanol sizing), an abandonment provision and, optionally,
  the NCS petroleum tax. Out come NPV, IRR, payback, break-even oil price, unit technical cost and
  CAPEX per barrel, with the cash flow year by year.
* *Run sensitivity* moves each input to its low and high value and ranks them by how far NPV travels —
  the tornado that says which assumption is worth chasing.

**Optimise** takes the concept on screen as the base case and builds the variants around it: more or
fewer wells (cloned into the structure, with the template upgraded when it runs out of slots), a
different line size, a looped line, and a boosting station spliced into the main line with power from
the host. Every variant is costed, scheduled, flow-solved and valued with the same models, and the
ones nothing beats on both CAPEX and NPV are drawn as a front. A variant whose wells cannot deliver, or
whose line is too small for the rate, stays in the table with the reason and never reaches the front.
You can load one into the layout, or save the best three as concepts and compare them in the Cases tab.

Boosting is now in the hydraulics as well as in the cost: a boosting or compression station lifts the
pressure by `boost_dp_bar` (80 bar by default, per-node override in `attrs`), so everything upstream of
it only has to reach its suction pressure.

## Look and feel
`tb_theme.py` holds the whole visual language in one place: an Equinor-derived palette (energy red as
the accent, moss green as the working colour, slate blue for headings), the `Equinor` typeface asked
for first with a system fallback, one stylesheet built from those tokens, and one chart frame so every
figure matches. `.streamlit/config.toml` gives Streamlit's own widgets the same colours. No Equinor
logo or wordmark is used, and the app states on every page that it is an independent prototype.

## Known limits
- Flow assurance draws a longitudinal section (seabed, line and riser with pressure/temperature) and a
  pipe cross-section build-up from the catalog (bore, wall or armour, insulation, coating, carrier pipe).
  The cross-section is schematic: wall thickness is a catalog input, not a pressure-containment calculation.
- Bathymetry: the two EMODnet endpoints answer in different shapes — `depth_sample` returns an object,
  `depth_profile` a bare array of elevations with no positions — and both sign conventions occur. The
  batch helpers swallow failures so one bad point cannot stop a run, so *Test EMODnet connection* is
  the button that tells a network problem from genuinely absent data.
- Bathymetry (EMODnet DTM, ~115 m grid, LAT datum) is indicative: use the project survey for design —
  loading it as a grid surface and pressing *Set element depths* is the way to do that.
- An imported grid is read as north-up in the CRS you give it; the app has no way to check that CRS
  against the file, so a grid placed with the wrong zone will look plausible and be wrong.
  Free spans use a taut-string model with no pipe stiffness or weight — survey candidates, not design spans.
- Sodir's oil/gas and gas/condensate classes use picture fills; the map approximates them with hatch
  patterns. Solid classes (oil green, gas red) come straight from the service renderer.
- Drilling & completion cost excluded (FieldVista owns well costs).
- Datum shift uses EPSG:1133 (~10 m). Sodir's WGS84 layers use ESRI ED_1950_To_WGS_1984_18, so ED50
  layouts can sit a few metres off the NCS overlays — fine for screening, not for survey work.
- ED50 layouts are converted to WGS84 for the map and back on every map edit.
- Map tools are checked headlessly against a fake Leaflet, not in a real browser: the new base maps and
  URL layers depend on the services being reachable from your network, and a corporate proxy may block
  some of them.
- A tag filter belongs to the layout it was built on and is cleared when another project, template or
  concept is loaded. A filter that matches nothing shows a *Clear tag filter* button rather than
  silently leaving the map blank.
- Backward pass ignores weather windows → float on windowed activities is indicative.
- Production chemistry is a screen, not a study: Hammerschmidt for concentration, an assumed fraction
  for methanol lost to the gas and condensate, and placeholder chemical prices. Confirm with a
  thermodynamic flash on the real fluid and the operator's chemical contracts.
- Flow assurance is steady-state screening: Beggs & Brill only (no Hagedorn-Brown/Gray yet), black-oil
  fluid, tree networks only (no loops or choke modelling), one host arrival pressure. Joule-Thomson
  cooling is included via a real-gas coefficient from the Z-factor derivative. Slugging is an indicator
  (riser flow pattern and gas velocity), not a transient simulation, and turndown is a set of steady
  states rather than a ramp. Use OLGA/LedaFlow/PIPESIM for design.
- Nodal deliverability linearises the network around the current rates between iterations; check that
  the reported solve converged before trusting the rates.
