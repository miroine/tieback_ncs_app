"""
tb_costio.py — Spreadsheet import/export for the cost catalog.

Cost input usually lives in Excel, so the catalog can round-trip through a
workbook (sheets `items`, `spreads`, `notes`) or a pair of CSV files.

Unknown columns are ignored, missing ones take the CatalogItem/VesselSpread
defaults, and blank rows are skipped. Validation errors name the sheet and row
so a bad rate is easy to find.
"""
from __future__ import annotations

import csv
import io
from dataclasses import fields
from typing import Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook

from tb_catalog import Catalog, CatalogItem, VesselSpread

ITEM_FIELDS = [f.name for f in fields(CatalogItem) if f.name != "uncertainty"] + ["unc_low", "unc_ml", "unc_high"]
SPREAD_FIELDS = [f.name for f in fields(VesselSpread) if f.name != "uncertainty"] + ["unc_low", "unc_ml", "unc_high"]
NOTES = [
    ("TieBack Studio cost catalog", ""),
    ("", ""),
    ("How to use", "Edit the numbers, keep the header row, then load the file in the Equipment catalog tab."),
    ("item_id", "Keep unchanged so existing projects keep loading."),
    ("cost_basis", "unit | per_m | per_inch_m (per metre × inch of ID)"),
    ("procurement_usd / fabrication_usd", "Per the cost basis above."),
    ("install_days", "Per unit, or per km for linear items."),
    ("lead_time_months", "Award to ready for load-out; drives the schedule."),
    ("unc_low / unc_ml / unc_high", "Triangular multipliers for the Monte Carlo."),
    ("install_spread", "Must match a key on the spreads sheet."),
    ("lift_capacity_te", "Vessel hook limit; 0 disables the check."),
]


def _row_values(obj, names) -> list:
    out = []
    for n in names:
        if n.startswith("unc_"):
            idx = {"unc_low": 0, "unc_ml": 1, "unc_high": 2}[n]
            out.append(obj.uncertainty[idx])
        else:
            out.append(getattr(obj, n))
    return out


def catalog_to_workbook(catalog: Catalog) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "items"
    ws.append(ITEM_FIELDS)
    for it in catalog.items.values():
        ws.append(_row_values(it, ITEM_FIELDS))
    ws.freeze_panes = "A2"
    ws2 = wb.create_sheet("spreads")
    ws2.append(SPREAD_FIELDS)
    for sp in catalog.spreads.values():
        ws2.append(_row_values(sp, SPREAD_FIELDS))
    ws2.freeze_panes = "A2"
    ws3 = wb.create_sheet("notes")
    for a, b in NOTES:
        ws3.append([a, b])
    ws3.column_dimensions["A"].width = 34
    ws3.column_dimensions["B"].width = 90
    for sheet, names in ((ws, ITEM_FIELDS), (ws2, SPREAD_FIELDS)):
        for i, n in enumerate(names, start=1):
            sheet.column_dimensions[sheet.cell(row=1, column=i).column_letter].width = max(12, min(len(n) + 4, 28))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _clean(rows: List[dict], allowed, kind: str, sheet: str):
    known = {f.name for f in fields(allowed)} - {"uncertainty"}
    out = []
    for i, r in enumerate(rows, start=2):
        rec = {k: v for k, v in r.items() if k in known and v not in (None, "")}
        key = rec.get("item_id") or rec.get("key")
        if not key:
            continue
        unc = []
        for name, default in (("unc_low", 0.9), ("unc_ml", 1.0), ("unc_high", 1.3)):
            v = r.get(name)
            unc.append(float(v) if v not in (None, "") else default)
        for f in fields(allowed):                    # coerce by declared type: CSV gives strings
            if f.name not in rec:
                continue
            try:
                if f.type in ("float", float):
                    rec[f.name] = float(rec[f.name])
                elif f.type in ("int", int):
                    rec[f.name] = int(float(rec[f.name]))
                elif f.type in ("bool", bool) and isinstance(rec[f.name], str):
                    rec[f.name] = rec[f.name].strip().lower() in ("1", "true", "yes", "y")
                elif f.type in ("str", str):
                    rec[f.name] = str(rec[f.name])
            except (TypeError, ValueError):
                raise ValueError(f"{sheet} row {i} ({key}): '{f.name}' is not a number: {rec[f.name]!r}") from None
        try:
            out.append(allowed(**rec, uncertainty=tuple(unc)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{sheet} row {i} ({key}): {exc}") from None
    if not out:
        raise ValueError(f"no {kind} rows found on sheet '{sheet}'")
    return out


def _sheet_rows(ws) -> List[dict]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    return [dict(zip(header, r)) for r in rows[1:] if any(v not in (None, "") for v in r)]


def catalog_from_workbook(data: bytes) -> Catalog:
    wb = load_workbook(io.BytesIO(data), data_only=True)
    names = {n.lower(): n for n in wb.sheetnames}
    if "items" not in names:
        raise ValueError(f"workbook has no 'items' sheet (found: {', '.join(wb.sheetnames)})")
    items = _clean(_sheet_rows(wb[names["items"]]), CatalogItem, "equipment", "items")
    if "spreads" in names:
        spreads = _clean(_sheet_rows(wb[names["spreads"]]), VesselSpread, "vessel spread", "spreads")
    else:
        spreads = None
    return Catalog(items, spreads)


def _csv_rows(data: bytes) -> List[dict]:
    text = data.decode("utf-8-sig")
    sample = text.splitlines()[0] if text.splitlines() else ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.DictReader(io.StringIO(text), dialect=dialect) if any(v for v in r.values())]


def catalog_from_csv(items_csv: bytes, spreads_csv: Optional[bytes] = None) -> Catalog:
    items = _clean(_csv_rows(items_csv), CatalogItem, "equipment", "items.csv")
    spreads = _clean(_csv_rows(spreads_csv), VesselSpread, "vessel spread", "spreads.csv") if spreads_csv else None
    return Catalog(items, spreads)


def catalog_to_csv(catalog: Catalog) -> Tuple[str, str]:
    def dump(names, objs):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(names)
        for o in objs:
            w.writerow(_row_values(o, names))
        return buf.getvalue()
    return dump(ITEM_FIELDS, catalog.items.values()), dump(SPREAD_FIELDS, catalog.spreads.values())
