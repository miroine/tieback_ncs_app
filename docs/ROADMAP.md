# TieBack Studio — improvement plan

Ordered easiest first. Effort is my build-and-test time, not yours. "Blocked" items need a file or a
decision from you before they can start.

## Batch 1 — done in v0.5.0

| # | Item | What it gives you |
|---|---|---|
| 1 | PVT Studio self-test (`tools/pvt_studio_selftest.py`) | Run it against your own `nodal.py`; it reports the six suspect patterns by line number and goes silent once the documented fixes are in. Verified against a buggy and a fixed reconstruction. |
| 2 | Cost library template (`library/cost_library_template.yaml`) | A commented starting point with every rate, lead time and uncertainty in one file, with notes on which fields move the total most. |
| 3 | Five concept templates (`templates/`) | 4-well satellite, daisy chain, dual flowline loop with SSIV, two-phase development with boosting, deepwater HPHT cluster to an FPSO. Each loads clean and costs/schedules end to end. |
| 4 | Lift-capacity check | Warns when a structure is heavier than the hook of the vessel that installs it. |

## Batch 4 — done in v0.6.0 (taken first, by request)

| # | Item | What it gives you |
|---|---|---|
| 9 | Joule-Thomson cooling | Real-gas JT coefficient from the Z-factor derivative, coupled with the pressure march over three passes. Costs about 1.6 °C of arrival temperature on the demo flowline. |
| 10 | Turndown and slugging | Solve at any set of rate fractions: arrival temperature, hydrate margin, back-pressure, liquid inventory and ramp-up surge volume, plus a riser severe-slugging flag from flow pattern and gas velocity. |
| 11 | Well deliverability (IPR) | PI, Vogel and gas back-pressure inflow, tubing VLP through the same Beggs-Brill gradient, and a damped coupled solve where rates come from the intersection with network back-pressure. |
| — | Cost catalog import | Excel workbook (items/spreads/notes) or CSV pair, on top of the existing YAML. Round-trips exactly. |

## Batches 3 and 2 — done in v0.7.0

| # | Item | What it gives you |
|---|---|---|
| 7 | Multi-case comparison | A Cases tab: snapshot whole projects, compare them on CAPEX, first production, wellhead margin, arrival temperature and hydrate margin, with differences against a baseline and a shareable case-set file. |
| 8 | Bathymetry-aware routing | Seabed sampled along every route and stored with the project; hydraulics then follow the real terrain station by station, and a taut-string check flags free spans. On the demo, undulating terrain adds 1.3 bar of back-pressure. |
| 5 | Piggyback bundles | Strap a utility line to a flowline or riser: it follows the carrier corridor, rides its campaign and adds a share of the lay time instead of its own mobilisation. |
| 6 | DG2/DG3 report | One-click Word report: summary, design checks, quantities, CAPEX with charts, schedule milestones and critical path, flow assurance tables and route section, basis and limits. |

## Still open

| # | Item | Effort | Why it matters |
|---|---|---|---|
| 12 | FieldVista economics link | Small once unblocked | Needs `fp_economics.py` and one case YAML from you. Turns the CAPEX hand-off into a real NPV/break-even run instead of a draft format. |

## Standing constraints

- Default cost rates are indicative placeholders until item 2 is filled in with your own data.
- Flow assurance is steady-state screening. OLGA/LedaFlow/PIPESIM stays the design tool.
- Nothing here has been run in a real browser by me; UI checks are headless.
