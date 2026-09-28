"""
Direct Shades & Blinds workbooks. The same three jobs show up on every project;
the file name, sheet name, and area names change.

  1. Window matrix — TOTAL SHADES for the sections printed on that sheet
  2. Quantity sheets — opening size and quantity, found by their column headers
  3. Bid sheets — Sales Price / Total Price, plus the Total Bid under the product table

A sheet is recognised from its header row, not from a project-specific name.
Madeira is one example of these headers, not a special case. Values are reported
only when the sheet actually says them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from utils.dimensions import format_feet_and_inches, parse_length_inches


class WorkbookKind(str, Enum):
    WINDOW_MATRIX = "window_matrix"
    MATERIAL_SUMMARY = "material_summary"
    BID_SUMMARY = "bid_summary"
    UNKNOWN = "unknown"


@dataclass
class ParsedWorkbook:
    kind: WorkbookKind
    file_name: str
    project_name: Optional[str] = None
    sheets: list[str] = field(default_factory=list)
    matrix_total_shades: Optional[int] = None
    matrix_total_windows: Optional[int] = None
    matrix_rows: list[dict[str, Any]] = field(default_factory=list)
    level_totals: dict[str, int] = field(default_factory=dict)
    blind_qty_sections: list[dict[str, Any]] = field(default_factory=list)
    blind_qty_lines: list[dict[str, Any]] = field(default_factory=list)
    bid_sheets: list[str] = field(default_factory=list)
    bid_tabs: list[dict[str, Any]] = field(default_factory=list)
    bid_lines: list[dict[str, Any]] = field(default_factory=list)
    bid_grand_total: Optional[float] = None
    material_lines: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        lines = [
            f"FILE: {self.file_name}",
            f"TYPE: {self.kind.value}",
            f"Sheets: {', '.join(self.sheets)}",
        ]
        if self.project_name:
            lines.append(f"Project: {self.project_name}")
        if self.matrix_total_shades is not None:
            lines.append(
                f"AUTHORITATIVE TOTAL SHADES (WINDOW MATRIX TOTAL row): {self.matrix_total_shades}"
            )
        if self.matrix_total_windows is not None:
            lines.append(f"TOTAL WINDOWS (WINDOW MATRIX): {self.matrix_total_windows}")
        if self.level_totals:
            lines.append(f"By level: {self.level_totals}")
        if self.matrix_rows:
            lines.append("Sample markings (tag → total shades):")
            for row in self.matrix_rows[:25]:
                lines.append(
                    f"  - {row.get('marking')}: {row.get('totalShades')} shades "
                    f"({row.get('section', '')})"
                )
        if self.blind_qty_sections:
            lines.append("Blind QTY unit take-off sections:")
            for sec in self.blind_qty_sections:
                lines.append(
                    f"  - {sec.get('area')}: unit qty {sec.get('unitQuantity')}, "
                    f"line items {sec.get('lineCount')}, sum QTY column {sec.get('qtySum')}"
                )
        if self.bid_tabs:
            lines.append("Bid Summary tabs (reference pricing — dollars only):")
            for tab in self.bid_tabs:
                rate = tab.get("unitRate")
                lines.append(
                    f"  - {tab.get('sheet')}: total {_money(tab.get('total'))}, "
                    f"{tab.get('lineCount')} priced lines"
                    + (f", ≈{_money(rate)}/unit" if rate else "")
                )
        if self.bid_grand_total is not None:
            lines.append(f"BID SUMMARY GRAND TOTAL: {_money(self.bid_grand_total)}")
        if self.material_lines:
            lines.append("Material Summary lines (fabric / system by tab):")
            for row in self.material_lines[:20]:
                lines.append(
                    f"  - {row.get('sheet')}: {row.get('description')}"
                    + (f" — {row.get('detail')}" if row.get("detail") else "")
                )
        if self.notes:
            lines.extend(self.notes)
        return "\n".join(lines)


def detect_workbook_kind(file_name: str, sheet_names: list[str]) -> WorkbookKind:
    """Classify a workbook by file name first, then by its sheet tab names."""
    name = file_name.lower()
    sheets_lower = [s.strip().lower() for s in sheet_names]

    if "window matrix" in name or any("window matrix" in s for s in sheets_lower):
        return WorkbookKind.WINDOW_MATRIX
    if "material summary" in name:
        return WorkbookKind.MATERIAL_SUMMARY
    if "bid summary" in name or "bid sheet" in name:
        return WorkbookKind.BID_SUMMARY

    if any("blind qty" in s for s in sheets_lower):
        return WorkbookKind.WINDOW_MATRIX
    if any("bid" in s for s in sheets_lower):
        return WorkbookKind.BID_SUMMARY
    if any(tok in name for tok in ("bid", "pricing", "proposal")):
        return WorkbookKind.BID_SUMMARY
    # A price tab is often named for the fabric (Dark Fabric, Solar). That is not a
    # material workbook. Material workbooks say so in the file name; the header
    # row ("Type of Product" / "Materials Summary") is the other key.
    if "material" in name:
        return WorkbookKind.MATERIAL_SUMMARY
    if set(sheets_lower) >= {"units", "blk"} or any("com-" in s for s in sheets_lower):
        # Same tab naming is shared by Material Summary and Bid Summary; without a
        # name hint we cannot tell them apart, so stay UNKNOWN and ingest as text.
        return WorkbookKind.UNKNOWN

    return WorkbookKind.UNKNOWN


def parse_workbook_xlsx(file_name: str, contents: bytes) -> ParsedWorkbook:
    import io

    import openpyxl

    # data_only so formula cells return the cached number. Not read_only: each sheet
    # is classified from its header and then parsed, which needs a second pass.
    wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
    sheet_names = list(wb.sheetnames)
    kind = detect_workbook_kind(file_name, sheet_names)
    parsed = ParsedWorkbook(kind=kind, file_name=file_name, sheets=sheet_names)
    roles = {name: _header_roles(wb[name]) for name in sheet_names}

    has_matrix = any("matrix" in role or "blind_qty" in role for role in roles.values())
    bid_sheets = [name for name, role in roles.items() if "bid" in role]
    material_sheets = [name for name, role in roles.items() if "material" in role]

    if kind == WorkbookKind.MATERIAL_SUMMARY:
        parsed.bid_sheets = sheet_names
        _parse_project_header(wb, parsed)
        _parse_material_summary(wb, parsed)
        parsed.notes.append(
            f"Material Summary tabs ({', '.join(sheet_names)}) supply fabric/system lines."
        )
    else:
        if kind == WorkbookKind.WINDOW_MATRIX or has_matrix:
            _parse_window_matrix(wb, parsed, roles)
        if kind == WorkbookKind.BID_SUMMARY:
            parsed.bid_sheets = sheet_names
            _parse_project_header(wb, parsed)
            _parse_bid_summary(wb, parsed)
        elif bid_sheets:
            parsed.bid_sheets = bid_sheets
            _parse_project_header(wb, parsed)
            _parse_bid_summary(wb, parsed, bid_sheets)
        elif kind == WorkbookKind.UNKNOWN and material_sheets:
            parsed.bid_sheets = material_sheets
            _parse_project_header(wb, parsed)
            _parse_material_summary(wb, parsed)
            parsed.notes.append(
                f"Material sheets ({', '.join(material_sheets)}) supply fabric/system lines."
            )
        elif kind == WorkbookKind.UNKNOWN and not has_matrix:
            parsed.notes.append("Generic Excel — all sheets ingested as text.")

    if parsed.kind == WorkbookKind.UNKNOWN:
        parsed.kind = _kind_from_content(parsed)

    wb.close()
    return parsed


def _kind_from_content(parsed: ParsedWorkbook) -> WorkbookKind:
    """When the file name does not say what the workbook is, the headers do."""
    if parsed.matrix_total_shades is not None or parsed.matrix_rows or parsed.blind_qty_lines:
        return WorkbookKind.WINDOW_MATRIX
    if parsed.bid_tabs or parsed.bid_lines:
        return WorkbookKind.BID_SUMMARY
    if parsed.material_lines:
        return WorkbookKind.MATERIAL_SUMMARY
    return WorkbookKind.UNKNOWN


def _header_roles(ws) -> set[str]:
    """
    What a sheet is, from the words in its header row.

    File names and tab names differ by project. WINDOW MARKINGS + TOTAL SHADES is a
    matrix. A tag column plus a quantity column is a quantity sheet. Sales Price or
    Total Price is a bid sheet, even when that row also has a quantity column.
    """
    found: set[str] = set()
    for row in ws.iter_rows(max_row=50, values_only=True):
        if not row:
            continue
        cells = _str_cells(row)
        if not any(cells):
            continue
        joined = " ".join(cells).upper()
        if "WINDOW MARKING" in joined and ("TOTAL SHADE" in joined or "TOTAL WINDOW" in joined):
            found.add("matrix")
        if "TYPE OF PRODUCT" in joined or "MATERIALS SUMMARY" in joined:
            found.add("material")
        bid_map = _map_bid_columns(cells)
        is_bid = ("salesPrice" in bid_map or "totalPrice" in bid_map) and (
            "description" in bid_map or "qty" in bid_map
        )
        if is_bid:
            found.add("bid")
            continue
        blind_map = _map_blind_qty_columns(cells)
        score = sum(_HEADER_WEIGHTS.get(key, 0) for key in blind_map if key in _HEADER_WEIGHTS)
        if score >= 3 and "tag" in blind_map and "qty" in blind_map:
            found.add("blind_qty")
    return found


def _parse_project_header(wb, parsed: ParsedWorkbook) -> None:
    """Project name is the cell after a 'Project Name' label, wherever that label sits."""
    if parsed.project_name:
        return
    for sn in wb.sheetnames:
        ws = wb[sn]
        for row in ws.iter_rows(max_row=25, values_only=True):
            if not row:
                continue
            cells = _str_cells(row)
            for index, cell in enumerate(cells):
                if cell.lower() != "project name":
                    continue
                for follower in cells[index + 1 :]:
                    if follower:
                        parsed.project_name = follower
                        return


def _parse_window_matrix(wb, parsed: ParsedWorkbook, roles: Optional[dict[str, set[str]]] = None) -> None:
    """Read every matrix and quantity sheet. The header decides, then the tab name."""
    roles = roles or {name: _header_roles(wb[name]) for name in wb.sheetnames}
    for sn in wb.sheetnames:
        role = roles.get(sn) or set()
        sl = sn.strip().lower()
        if "matrix" in role or "window matrix" in sl:
            _parse_matrix_sheet(wb[sn], parsed)
        elif "bid" in role:
            continue
        elif "blind_qty" in role or "blind qty" in sl:
            _parse_blind_qty_sheet(wb[sn], sn, parsed)

    _parse_project_header(wb, parsed)


def _parse_matrix_sheet(ws, parsed: ParsedWorkbook) -> None:
    rows = list(ws.iter_rows(values_only=True))
    header_idx = None
    col_map: dict[str, int] = {}

    for i, row in enumerate(rows):
        cells = [str(c).strip() if c is not None else "" for c in row]
        joined = " ".join(cells).upper()
        if "WINDOW MARKINGS" in joined and "TOTAL SHADES" in joined:
            header_idx = i
            for j, c in enumerate(cells):
                cu = c.upper()
                if "MARKING" in cu or c == "WINDOW MARKINGS":
                    col_map["marking"] = j
                elif "SHADES PER OPENING" in cu:
                    col_map["spo"] = j
                elif cu.startswith("LEVEL-"):
                    col_map.setdefault("levels", []).append((cu, j))
                elif "TOTAL WINDOWS" in cu:
                    col_map["total_windows"] = j
                elif "TOTAL SHADES" in cu:
                    col_map["total_shades"] = j
            break

    if header_idx is None:
        parsed.notes.append("WINDOW MATRIX sheet found but header row not detected.")
        return

    section = ""
    for row in rows[header_idx + 1 :]:
        cells = list(row)
        marking_col = col_map.get("marking", 1)
        marking = _cell(cells, marking_col)
        if not marking:
            continue
        mu = marking.upper()
        total_shades = _int_cell(cells, col_map.get("total_shades"))
        total_windows = _int_cell(cells, col_map.get("total_windows"))
        if mu == "TOTAL" or mu.startswith("TOTAL ") or mu.startswith("GRAND TOTAL"):
            if total_windows is not None:
                parsed.matrix_total_windows = total_windows
            if total_shades is not None:
                parsed.matrix_total_shades = total_shades
            for level_name, j in col_map.get("levels", []):
                val = _int_cell(cells, j)
                if val is not None:
                    parsed.level_totals[level_name] = val
            continue

        # A label with no counts is an area header. The name is whatever this
        # project printed (Living Areas, Tower A, Amenities) — it is not a fixed list.
        if total_shades is None and total_windows is None:
            other_numbers = any(
                _to_float(cell) is not None
                for index, cell in enumerate(cells)
                if index != marking_col
            )
            if not other_numbers:
                section = marking
            continue

        parsed.matrix_rows.append(
            {
                "marking": marking,
                "section": section,
                "shadesPerOpening": _int_cell(cells, col_map.get("spo")),
                "totalShades": total_shades,
                "totalWindows": total_windows,
            }
        )

    if parsed.matrix_total_shades is not None:
        parsed.notes.append(
            "Count rule: TOTAL row, TOTAL SHADES column, counts the sections listed on this matrix."
        )


def _label_cell(row: tuple) -> tuple[str, str]:
    """First two non-empty cells as (label, value) for metadata rows."""
    cells = [str(c).strip() if c is not None else "" for c in row]
    non_empty = [(i, c) for i, c in enumerate(cells) if c]
    if not non_empty:
        return "", ""
    label = non_empty[0][1]
    value = non_empty[1][1] if len(non_empty) > 1 else ""
    return label, value


#: Weight per recognised column — a candidate header row must score >= 3.
_HEADER_WEIGHTS = {"tag": 2, "qty": 2, "width": 1, "height": 1, "system": 1, "room": 1}


def _find_blind_qty_header_row(rows: list[tuple]) -> Optional[int]:
    """
    Score every row by how many take-off columns it names and return the best one.

    Scoring beats keyword matching on real Direct Shades sheets because the header
    sits an unpredictable number of rows below the AREA / UNIT QUANTITY metadata
    block, and the column labels vary ("Window Tag" / "TAG" / "MARKING", "QTY" /
    "QUANTITY", "W" / "WIDTH (IN)"). Metadata rows such as "Unit Quantity | 10"
    score at most 2 and are therefore never mistaken for the header.
    """
    best_idx: Optional[int] = None
    best_score = 0
    for i, row in enumerate(rows[:120]):
        if not row:
            continue
        cells = _str_cells(row)
        if not any(cells):
            continue
        col_map = _map_blind_qty_columns(cells)
        score = sum(_HEADER_WEIGHTS.get(key, 0) for key in col_map if key in _HEADER_WEIGHTS)
        if score > best_score and score >= 3:
            best_idx, best_score = i, score
    return best_idx


def _map_blind_qty_columns(header_cells: list[str]) -> dict[str, int]:
    """Map take-off column names to indices. Keys: tag, qty, width, height, system, room."""
    col_map: dict[str, int] = {}
    for j, c in enumerate(header_cells):
        cu = c.upper().strip()
        if not cu:
            continue
        if cu in ("#", "NO", "NO."):
            col_map.setdefault("tag", j)
        elif any(tok in cu for tok in ("WINDOW TAG", "WIN TAG", "WINDOW MARK", "WINDOW MARKING")):
            col_map["tag"] = j
        elif cu in ("TAG", "MARK", "MARKING", "WINDOW", "WIN"):
            col_map.setdefault("tag", j)
        elif "MARKING" in cu or ("WINDOW" in cu and "QTY" not in cu and "QUANTITY" not in cu):
            col_map.setdefault("tag", j)
        elif (
            "QTY" in cu
            or "QUANTITY" in cu
            or cu in ("COUNT", "PCS", "PIECES")
            or "SHADE COUNT" in cu
            or "NO OF SHADE" in cu
            or "NO. OF SHADE" in cu
        ):
            col_map.setdefault("qty", j)
        elif any(tok in cu for tok in ("WIDTH", "WD", "DIM W")) or _bare_dim_label(cu, "W"):
            col_map.setdefault("width", j)
        elif any(tok in cu for tok in ("HEIGHT", "HT", "DIM H")) or _bare_dim_label(cu, "H"):
            col_map.setdefault("height", j)
        elif any(tok in cu for tok in ("SYSTEM", "SHADE", "PRODUCT", "TYPE", "SYS", "FABRIC")):
            col_map.setdefault("system", j)
        elif _is_room_header(cu):
            col_map.setdefault("room", j)
        if _axis_of_header(cu):
            # Scoring only — finished size is resolved from every width/height column.
            axis = _axis_of_header(cu)
            col_map.setdefault("width" if axis == "width" else "height", j)
    return col_map


def _is_room_header(cell_upper: str) -> bool:
    """Room/location labels. Measurement headers such as AREA (SQYD) are not rooms."""
    if any(tok in cell_upper for tok in ("SQ", "YD", "WIDTH", "HEIGHT", "INCH", "QTY", "COST", "PRICE")):
        return False
    if any(tok in cell_upper for tok in ("ROOM", "LOCATION", "SPACE")):
        return True
    if "AREA" in cell_upper and "FT" not in cell_upper:
        return True
    return False


def _axis_of_header(cell_upper: str) -> Optional[str]:
    """Width or height column, ignoring tags, quantities, and area totals."""
    if any(tok in cell_upper for tok in ("SQ", "QTY", "COST", "PRICE", "SYSTEM", "ROOM", "TAG", "MARK", "DOOR")):
        if "WIDTH" not in cell_upper and "HEIGHT" not in cell_upper:
            return None
    if "HEIGHT" in cell_upper or _bare_dim_label(cell_upper, "H"):
        return "height"
    if "WIDTH" in cell_upper or _bare_dim_label(cell_upper, "W"):
        return "width"
    return None


def _dim_kind(cell_upper: str, unit: Optional[str]) -> str:
    """
    feet / remainder / finished / plain.

    Sheets often split a size into WIDTH (FT) + WIDTH (IN) and also print the
    finished total as W (INCH). The short W/H header is the finished size.
    The long WIDTH/HEIGHT header next to a feet column is only the leftover inches.
    """
    short = _bare_dim_label(cell_upper, "W") or _bare_dim_label(cell_upper, "H")
    if unit == "ft":
        return "feet"
    if unit == "in" and short:
        return "finished"
    if unit == "in":
        return "remainder"
    return "plain"


def _collect_dim_columns(header_cells: list[str]) -> dict[str, list[dict[str, Any]]]:
    axes: dict[str, list[dict[str, Any]]] = {"width": [], "height": []}
    for j, raw in enumerate(header_cells):
        cu = raw.upper().strip()
        axis = _axis_of_header(cu)
        if not axis:
            continue
        unit = _dimension_unit_hint(raw)
        axes[axis].append(
            {"index": j, "kind": _dim_kind(cu, unit), "unit": unit, "header": raw}
        )
    return axes


def _numeric_cell(cells: list, idx: Optional[int]) -> Optional[float]:
    if idx is None or idx >= len(cells):
        return None
    value = cells[idx]
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        parsed = parse_length_inches(value)
        return float(parsed) if parsed is not None else None


def _resolve_axis_size(cols: list[dict[str, Any]], cells: list) -> tuple[Optional[str], Optional[float]]:
    """Return (display, inches) for one axis. Prefer a finished inch column that agrees with feet+inches."""
    if not cols:
        return None, None

    feet_col = next((c for c in cols if c["kind"] == "feet"), None)
    remainder_col = next((c for c in cols if c["kind"] == "remainder"), None)
    finished_cols = [c for c in cols if c["kind"] == "finished"]

    composed: Optional[float] = None
    if feet_col is not None:
        feet = _numeric_cell(cells, feet_col["index"])
        remainder = _numeric_cell(cells, remainder_col["index"]) if remainder_col else 0.0
        if feet is not None:
            composed = feet * 12 + (remainder or 0)

    finished: Optional[float] = None
    if finished_cols:
        finished = _numeric_cell(cells, finished_cols[-1]["index"])

    inches: Optional[float] = None
    if finished is not None and composed is not None:
        inches = finished if abs(finished - composed) <= 1.0 else finished
    elif finished is not None:
        inches = finished
    elif composed is not None:
        inches = composed
    else:
        plain = next((c for c in cols if c["kind"] == "plain"), cols[0])
        raw = _cell(cells, plain["index"])
        return _dimension_value(raw, plain.get("unit"))

    if inches is None:
        return None, None
    return format_feet_and_inches(inches), round(inches, 2)


def _axis_reference(cols: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Which header on this sheet is the size, in the words the sheet printed."""
    if not cols:
        return None
    finished = [c for c in cols if c["kind"] == "finished"]
    feet = next((c for c in cols if c["kind"] == "feet"), None)
    remainder = next((c for c in cols if c["kind"] == "remainder"), None)
    if finished:
        checked = [c["header"] for c in (feet, remainder) if c]
        return {
            "used": _clean_header(finished[-1]["header"]),
            "method": "finished inches",
            "checkedAgainst": [_clean_header(h) for h in checked],
        }
    if feet:
        headers = [feet["header"]] + ([remainder["header"]] if remainder else [])
        return {
            "used": " + ".join(_clean_header(h) for h in headers),
            "method": "feet plus leftover inches",
            "checkedAgainst": [],
        }
    plain = next((c for c in cols if c["kind"] == "plain"), cols[0])
    unit = plain.get("unit")
    if unit == "in":
        method = "inches, named on the column"
    elif unit == "ft":
        method = "feet, named on the column"
    else:
        method = "as written; the column does not name a unit"
    return {"used": _clean_header(plain["header"]), "method": method, "checkedAgainst": []}


def _clean_header(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _bare_dim_label(cell_upper: str, letter: str) -> bool:
    """True for terse dimension headers: W, H, W., W" or W (IN)."""
    stripped = cell_upper.replace(".", "").replace('"', "").strip()
    if stripped == letter:
        return True
    return bool(re.fullmatch(rf"{letter}\s*\((?:IN|INCH|INCHES|FT|FEET)\)", stripped))


_UNIT_HINT_RE = re.compile(r"\b(IN|INCH|INCHES|FT|FEET)\b|(\")|(')")


def _dimension_unit_hint(header_cell: str) -> Optional[str]:
    """Read an explicit unit out of a dimension header, e.g. 'WIDTH (IN)' → 'in'."""
    m = _UNIT_HINT_RE.search(header_cell.upper())
    if not m:
        return None
    token = m.group(0)
    if token in ("FT", "FEET", "'"):
        return "ft"
    return "in"


def _dimension_value(raw: str, unit_hint: Optional[str]) -> tuple[Optional[str], Optional[float]]:
    """
    Normalise one width/height cell into (display string, inches).

    Bare numbers are ambiguous on shade sheets (48 means inches, 4 usually means
    feet), so inches are only resolved when the header states the unit or the cell
    carries its own unit marks. Otherwise the raw text is kept and the shared
    heuristic in utils.dimensions decides later.
    """
    text = (raw or "").strip()
    if not text:
        return None, None

    plain = text.replace(",", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", plain):
        number = float(plain)
        if unit_hint == "ft":
            return f"{plain}'", number * 12
        if unit_hint == "in":
            return f'{plain}"', number
        return text, None

    return text, parse_length_inches(text)


_BLIND_QTY_TOTAL_LABELS = ("TOTAL", "SUBTOTAL", "SUB TOTAL", "GRAND TOTAL", "SUM")


def _looks_motorized(tag: str, system: Optional[str]) -> bool:
    blob = f"{tag} {system or ''}".upper()
    return "MOTOR" in blob


def _dimension_warning(width_in: Optional[float], height_in: Optional[float]) -> Optional[str]:
    """Flag sizes that are present but too small to be a real opening."""
    if width_in is not None and 0 < width_in < 12:
        return f"Width {width_in:g} in is under 12 in — confirm this cell before pricing."
    if height_in is not None and 0 < height_in < 12:
        return f"Height {height_in:g} in is under 12 in — confirm this cell before pricing."
    return None


def _blind_qty_row_role(tag: str, has_metrics: bool) -> str:
    """Classify a Blind QTY row: 'data', 'total', 'section', or 'skip'."""
    tu = tag.upper().strip()
    if not tu:
        return "skip"
    if tu in _BLIND_QTY_TOTAL_LABELS or tu.startswith("TOTAL ") or tu.startswith("SUB "):
        return "total"
    if tu in ("NOTES", "NOTE", "LEGEND"):
        return "skip"
    if not has_metrics:
        return "section"
    return "data"


def _parse_blind_qty_sheet(ws, sheet_name: str, parsed: ParsedWorkbook) -> None:
    """
    Parse one 'Blind QTY UNITS …' sheet into individual take-off lines.

    Handles sheets that stack several areas (LIVINGS, BEDROOMS, COMMONS) with their
    own sub-total rows, repeated window tags across sections, and header rows placed
    below a metadata block. Every emitted line carries file → sheet → row so the
    audit can point back at the exact cell range.
    """
    rows = list(ws.iter_rows(values_only=True))
    sheet = sheet_name.strip()
    area = None
    unit_qty = None
    qty_sum = 0
    line_count = 0

    for row in rows[:40]:
        if not row:
            continue
        label, value = _label_cell(row)
        lu = label.lower()
        if lu == "area" or lu.startswith("area "):
            area = value or area
        elif "unit" in lu and "quant" in lu:
            unit_qty = value if value != "" else row[1] if len(row) > 1 else unit_qty

    header_idx = _find_blind_qty_header_row(rows)
    declared_total: Optional[int] = None
    measurement = None

    if header_idx is None:
        parsed.notes.append(
            f"Blind QTY sheet '{sheet}' — header row not detected (expected TAG/QTY/W/H columns)."
        )
    else:
        header_cells = _str_cells(rows[header_idx])
        col_map = _map_blind_qty_columns(header_cells)
        dim_cols = _collect_dim_columns(header_cells)
        measurement = {
            "width": _axis_reference(dim_cols["width"]),
            "height": _axis_reference(dim_cols["height"]),
        }
        tag_col = col_map.get("tag", 0)
        qty_col = col_map.get("qty")
        section = area or ""

        for offset, row in enumerate(rows[header_idx + 1 :]):
            if not row:
                continue
            cells = list(row)
            excel_row = header_idx + 2 + offset
            tag = _cell(cells, tag_col)
            qty = _int_cell(cells, qty_col)
            width, width_in = _resolve_axis_size(dim_cols["width"], cells)
            height, height_in = _resolve_axis_size(dim_cols["height"], cells)
            has_metrics = bool(qty is not None or width_in is not None or height_in is not None or width or height)

            role = _blind_qty_row_role(tag, has_metrics)
            if role == "total":
                if declared_total is None and qty and ("SHADE" in tag.upper() or tag.upper().strip() in ("TOTAL", "TOTAL QTY")):
                    declared_total = qty
                continue
            if role == "skip":
                continue
            if role == "section":
                section = tag
                continue

            if qty is None:
                qty = 1 if (width_in or height_in or width or height) else None
            if qty is None or qty <= 0:
                continue

            system = _cell(cells, col_map.get("system")) or None
            room = _cell(cells, col_map.get("room")) or None
            # A measurement that landed in the room column (sq yd, sq ft) is not a room.
            if room and _numeric_cell([room], 0) is not None and not re.search(r"[A-Za-z]", room):
                room = None

            qty_sum += qty
            line_count += 1
            parsed.blind_qty_lines.append(
                {
                    "windowTag": tag,
                    "quantity": qty,
                    "width": width,
                    "height": height,
                    "widthInches": width_in,
                    "heightInches": height_in,
                    "dimensionWarning": _dimension_warning(width_in, height_in),
                    "systemType": system,
                    "motorized": _looks_motorized(tag, system),
                    "room": room,
                    "area": area or sheet,
                    "section": section or area or "",
                    "sheet": sheet,
                    "row": excel_row,
                    "file": parsed.file_name,
                    "sourceLocation": (
                        f"{parsed.file_name} → sheet '{sheet}' → row {excel_row} (tag '{tag}')"
                    ),
                }
            )

    parsed.blind_qty_sections.append(
        {
            "sheet": sheet,
            "area": area or sheet_name,
            "unitQuantity": unit_qty,
            "qtySum": qty_sum,
            "declaredTotal": declared_total if header_idx is not None else None,
            "lineCount": line_count,
            "measurement": measurement if header_idx is not None else None,
        }
    )


_PRICE_GROUP_TOKENS = ("TUBE", "FASCIA", "FABRIC", "VTX", "ROLLER", "BLACKOUT", "DUAL", "CASSETTE")
_CHARGE_TOKENS = ("INSTALL", "TRIP", "TAX", "FREIGHT", "SURCHARGE", "PERMIT", "BOND")


def _map_bid_columns(header_cells: list[str]) -> dict[str, int]:
    """
    Map a bid header to description, qty, sales, and cost columns.

    Sell columns (Sales Price, Total Price) are kept apart from cost columns
    (Cost Sqft, Total Cost) so a rate-per-foot is never treated as the line total.
    """
    col_map: dict[str, int] = {}
    for j, c in enumerate(header_cells):
        cu = c.upper().strip()
        if not cu:
            continue
        if any(tok in cu for tok in ("WINDOW MARK", "MARKING", "DESCRIPTION", "ITEM", "TAG")) and "QTY" not in cu:
            col_map.setdefault("description", j)
        elif cu in ("W", "W.") or (cu.startswith("WIDTH") and "MARK" not in cu):
            col_map.setdefault("width", j)
        elif cu in ("H", "H.") or cu.startswith("HEIGHT"):
            col_map.setdefault("height", j)
        elif cu in ("SQF", "SQ FT", "SF") or "SQUARE" in cu:
            col_map.setdefault("sqft", j)
        elif "COST" in cu and "SQ" in cu:
            col_map.setdefault("costPerSqft", j)
        elif "COST PER" in cu or cu in ("COST EACH", "UNIT COST"):
            col_map.setdefault("costEach", j)
        elif "TOTAL COST" in cu:
            col_map.setdefault("totalCost", j)
        elif "SALES PRICE" in cu or cu in ("SELL", "SELL PRICE", "UNIT PRICE", "PRICE EACH"):
            col_map.setdefault("salesPrice", j)
        elif "TOTAL PRICE" in cu or "EXT PRICE" in cu or "EXTENDED" in cu or cu == "AMOUNT":
            col_map.setdefault("totalPrice", j)
        elif "PRICE" in cu and "SQ" in cu:
            col_map.setdefault("pricePerSqft", j)
        elif "QTY" in cu or cu == "QUANTITY":
            col_map.setdefault("qty", j)
    return col_map


def _find_bid_header_row(rows: list[tuple]) -> Optional[int]:
    """Best row that names a sell/cost column plus a description or quantity column."""
    best_idx: Optional[int] = None
    best_score = 0
    money_keys = ("totalPrice", "salesPrice", "totalCost", "costEach")
    for i, row in enumerate(rows[:120]):
        if not row:
            continue
        col_map = _map_bid_columns(_str_cells(row))
        if not any(key in col_map for key in money_keys):
            continue
        score = len(col_map) + (2 if "description" in col_map else 0) + (2 if "totalPrice" in col_map else 0)
        if score > best_score and score >= 2:
            best_idx, best_score = i, score
    return best_idx


def _parse_bid_summary(wb, parsed: ParsedWorkbook, sheet_names: Optional[list[str]] = None) -> None:
    """Read bid tabs. Tabs that price the same markings are alternates."""
    for sheet_name in sheet_names if sheet_names is not None else list(wb.sheetnames):
        _parse_bid_sheet(wb[sheet_name], sheet_name, parsed)

    _mark_bid_roles(parsed)
    base_totals = [
        t.get("productTotal") or t.get("total")
        for t in parsed.bid_tabs
        if t.get("role") == "base" and (t.get("productTotal") or t.get("total"))
    ]
    if base_totals:
        parsed.bid_grand_total = round(sum(base_totals), 2)
    if parsed.bid_lines:
        base_names = [t["sheet"] for t in parsed.bid_tabs if t.get("role") == "base"]
        alt_names = [t["sheet"] for t in parsed.bid_tabs if t.get("role") == "alternate"]
        note = (
            f"Bid Summary: {len(parsed.bid_lines)} priced lines. "
            f"Quote uses {', '.join(base_names) or 'no tab'}."
        )
        if alt_names:
            note += (
                f" Not added (same markings, alternate price): {', '.join(alt_names)}."
            )
        parsed.notes.append(note)
    else:
        parsed.notes.append(
            f"Bid Summary tabs ({', '.join(parsed.sheets)}) — no priced rows detected; "
            "ingested as text for the pricing context."
        )


def _parse_bid_sheet(ws, sheet_name: str, parsed: ParsedWorkbook) -> None:
    rows = list(ws.iter_rows(values_only=True))
    sheet = sheet_name.strip()
    header_idx = _find_bid_header_row(rows)
    if header_idx is None:
        return

    header_cells = _str_cells(rows[header_idx])
    col_map = _map_bid_columns(header_cells)
    desc_col = col_map.get("description", 0)
    product_total: Optional[float] = None
    #: A generic "TOTAL" row, used only when there is no "Total Product" row.
    declared_total: Optional[float] = None
    product_qty: Optional[int] = None
    line_count = 0
    sell_sum = 0.0
    qty_sum = 0
    section = ""
    price_group = ""

    for offset, row in enumerate(rows[header_idx + 1 :]):
        if not row:
            continue
        cells = list(row)
        excel_row = header_idx + 2 + offset
        description = _cell(cells, desc_col)
        if not description:
            continue
        label = description.upper().strip()
        qty = _int_cell(cells, col_map.get("qty"))
        sales = _float_cell(cells, col_map.get("salesPrice"))
        total_price = _float_cell(cells, col_map.get("totalPrice"))
        cost_each = _float_cell(cells, col_map.get("costEach"))
        total_cost = _float_cell(cells, col_map.get("totalCost"))

        if _is_bid_total_label(label):
            if "PRODUCT" in label:
                if total_price is not None:
                    product_total = total_price
                if qty:
                    product_qty = qty
            elif declared_total is None:
                # A generic "TOTAL" row on a simpler bid sheet: keep it as a fallback
                # below the Total Product row but above summing the lines ourselves.
                declared_total = (
                    total_price
                    if total_price is not None
                    else total_cost
                    if total_cost is not None
                    else _last_money_in_row(cells)
                )
            continue
        if any(tok in label for tok in _CHARGE_TOKENS):
            continue
        if not qty:
            if any(tok in label for tok in _PRICE_GROUP_TOKENS):
                price_group = description
            else:
                section = description
            continue

        if total_price is None and sales is not None:
            total_price = round(sales * qty, 2)
        if sales is None and total_price is not None:
            sales = round(total_price / qty, 2)
        if total_price is None and total_cost is None and sales is None:
            continue

        line_count += 1
        qty_sum += qty
        if total_price is not None:
            sell_sum += total_price
        parsed.bid_lines.append(
            {
                "sheet": sheet,
                "row": excel_row,
                "description": description,
                "section": section,
                "priceGroup": price_group,
                "quantity": qty,
                "width": _cell(cells, col_map.get("width")) or None,
                "height": _cell(cells, col_map.get("height")) or None,
                "squareFeet": _float_cell(cells, col_map.get("sqft")),
                "salesPrice": sales,
                "totalPrice": total_price,
                "costEach": cost_each,
                "totalCost": total_cost,
                "unitPrice": sales if sales is not None else cost_each,
                "amount": total_price if total_price is not None else total_cost,
                "file": parsed.file_name,
                "sourceLocation": (
                    f"{parsed.file_name} → sheet '{sheet}' → row {excel_row} ({description[:40]})"
                ),
            }
        )

    if not line_count and product_total is None and declared_total is None:
        return

    commercial = _commercial_stack(rows)
    line_sum = round(sell_sum, 2) or None
    total = product_total
    if total is None:
        total = declared_total if declared_total is not None else line_sum
    unit_rate = None
    if total and (product_qty or qty_sum):
        unit_rate = round(total / (product_qty or qty_sum), 2)

    parsed.bid_tabs.append(
        {
            "sheet": sheet,
            "total": total,
            "productTotal": product_total if product_total is not None else (round(sell_sum, 2) or None),
            "productQuantity": product_qty or qty_sum or None,
            "lineCount": line_count,
            "quantity": qty_sum or None,
            "unitRate": unit_rate,
            "totalFrom": (
                "TOTAL row"
                if product_total is not None or declared_total is not None
                else "sum of line amounts"
            ),
            "commercial": commercial,
            "widthHeader": _clean_header(header_cells[col_map["width"]]) if "width" in col_map else None,
            "heightHeader": _clean_header(header_cells[col_map["height"]]) if "height" in col_map else None,
            "file": parsed.file_name,
        }
    )


def _commercial_stack(rows: list[tuple]) -> dict[str, Any]:
    """
    Sell-side totals under the shade table: installation, named charges, tax, Total Bid.

    The accounting GRAND TOTAL at the bottom of these sheets is cost plus profit.
    The client number is the row labeled Total Bid, or the motorization GRAND TOTAL
    when that block actually has motor quantities.
    """
    installation = _installation_charge(rows)
    charges = _named_charges(rows)
    tax = _labeled_amount(rows, "TAX")
    total_bid = _labeled_amount(rows, "TOTAL BID")
    motor_qty, motor_total = _motor_grand_total(rows)
    return {
        "installation": installation,
        "charges": charges,
        "tax": tax,
        "totalBid": total_bid,
        "motorQuantity": motor_qty or None,
        "motorizedTotal": motor_total if motor_qty else None,
        "clientTotal": (motor_total if motor_qty else None) or total_bid,
    }


def _row_text(row: tuple) -> list[str]:
    return [str(c).strip() if c is not None else "" for c in row]


def _installation_charge(rows: list[tuple]) -> Optional[float]:
    """Extended install sell price: the number after the Charge Per Blind column."""
    for index, row in enumerate(rows):
        texts = _row_text(row)
        charge_at = next(
            (i for i, text in enumerate(texts) if "CHARGE PER BLIND" in text.upper()),
            None,
        )
        if charge_at is None:
            continue
        for follower in rows[index + 1 : index + 4]:
            cells = list(follower)
            if charge_at >= len(cells) or _to_float(cells[charge_at]) is None:
                continue
            for follower_cell in cells[charge_at + 1 :]:
                number = _to_float(follower_cell)
                if number is not None:
                    return round(number, 2)
    return None


def _named_charges(rows: list[tuple]) -> list[dict[str, Any]]:
    """Named sell charges under the installation block. Zero-price rows are skipped."""
    start_search = None
    for index, row in enumerate(rows):
        texts = [c.upper() for c in _row_text(row)]
        if any(text == "INSTALLATION" or "CHARGE PER BLIND" in text for text in texts):
            start_search = index
            break
    if start_search is None:
        return []

    header_at = None
    price_at = None
    for index, row in enumerate(rows[start_search:], start_search):
        cells = _row_text(row)
        if not cells or cells[0].upper() != "DESCRIPTION":
            continue
        price_indexes = [i for i, cell in enumerate(cells) if cell.upper() == "PRICE"]
        if not price_indexes:
            continue
        header_at = index
        price_at = price_indexes[-1]
        break
    if header_at is None or price_at is None:
        return []

    charges: list[dict[str, Any]] = []
    for row in rows[header_at + 1 :]:
        cells = _row_text(row)
        label = cells[0].upper() if cells else ""
        if not label:
            continue
        if label.startswith(("SUB TOTAL", "SUBTOTAL", "MOTOR")):
            break
        amount = _to_float(cells[price_at]) if price_at < len(cells) else None
        if amount is None or amount == 0:
            continue
        charges.append({"name": cells[0].strip(), "amount": round(amount, 2)})
    return charges


def _labeled_amount(rows: list[tuple], label: str) -> Optional[float]:
    """Money amount after an exact label such as Tax or Total Bid. A rate like 0.07 is skipped."""
    for row in rows:
        cells = list(row)
        for index, cell in enumerate(cells):
            text = str(cell).strip().upper() if cell is not None else ""
            if text != label:
                continue
            numbers = [
                number
                for follower in cells[index + 1 :]
                if (number := _to_float(follower)) is not None
            ]
            money = [number for number in numbers if number > 1]
            if money:
                return round(money[-1], 2)
            if numbers and label != "TAX":
                return round(numbers[-1], 2)
    return None


def _motor_grand_total(rows: list[tuple]) -> tuple[int, Optional[float]]:
    """
    Motor quantity and the GRAND TOTAL printed beside the motorization block.

    The Grand Total row in the first column is the motor price sum. The accounting
    GRAND TOTAL further down is cost plus profit. Neither of those is this figure.
    """
    start = None
    for index, row in enumerate(rows):
        cells = _row_text(row)
        if cells and cells[0].upper() == "MOTORIZATION":
            start = index
            break
    if start is None:
        return 0, None

    skip = ("DESCRIPTION", "QTY", "LABOR", "TAX", "SUB TOTAL", "SUBTOTAL", "GRAND TOTAL")
    qty = 0
    total = None
    for row in rows[start : start + 16]:
        cells = list(row)
        texts = _row_text(row)
        if texts and texts[0].upper().startswith("ACCOUNTING"):
            break
        name = texts[0].upper() if texts else ""
        if len(cells) > 1 and name and not name.startswith(skip):
            count = _to_float(cells[1])
            if count:
                qty += int(count)
        for index, text in enumerate(texts):
            if index == 0 or "GRAND TOTAL" not in text.upper():
                continue
            for follower in cells[index + 1 :]:
                number = _to_float(follower)
                if number:
                    total = round(number, 2)
                    break
    return qty, total


def _mark_bid_roles(parsed: ParsedWorkbook) -> None:
    """Tabs that list the same window markings are alternate prices, not extra shades."""
    by_sheet: dict[str, set[str]] = {}
    for line in parsed.bid_lines:
        by_sheet.setdefault(line["sheet"], set()).add(_norm_tag(line.get("description") or ""))
    sheets = [t["sheet"] for t in parsed.bid_tabs if t["sheet"] in by_sheet]
    parent = {name: name for name in sheets}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for i, left in enumerate(sheets):
        for right in sheets[i + 1 :]:
            a, b = by_sheet.get(left) or set(), by_sheet.get(right) or set()
            overlap = a & b
            smaller = min(len(a), len(b))
            # A short tab that repeats the same markings is an alternate price.
            # Large tabs need several shared markings so one shared label does not merge them.
            if smaller and len(overlap) / smaller >= 0.6 and (len(overlap) >= 3 or smaller <= 3):
                parent[find(right)] = find(left)

    base_sheets: set[str] = set()
    seen_groups: set[str] = set()
    for name in sheets:
        group = find(name)
        if group not in seen_groups:
            seen_groups.add(group)
            base_sheets.add(name)

    for tab in parsed.bid_tabs:
        tab["role"] = "base" if tab["sheet"] in base_sheets or tab["sheet"] not in by_sheet else "alternate"
    for line in parsed.bid_lines:
        line["role"] = "alternate" if line["sheet"] not in base_sheets and line["sheet"] in by_sheet else "base"


def _norm_tag(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").upper()).strip()


def _is_bid_total_label(label: str) -> bool:
    if not label:
        return False
    for token in ("GRAND TOTAL", "SUBTOTAL", "SUB TOTAL", "TOTAL"):
        if label == token or label.startswith(token + " ") or label.endswith(" " + token):
            return True
    return False


def _last_money_in_row(cells: list) -> Optional[float]:
    for value in reversed(cells):
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _parse_material_summary(wb, parsed: ParsedWorkbook) -> None:
    """Collect fabric / system description lines per Material Summary tab."""
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        sheet = sheet_name.strip()
        for offset, row in enumerate(ws.iter_rows(max_row=200, values_only=True)):
            if not row:
                continue
            cells = _str_cells(row)
            non_empty = [c for c in cells if c]
            if len(non_empty) < 2:
                continue
            label = non_empty[0]
            if label.upper() in ("AMOUNT", "TOTAL", "EXTENDED", "COST") or label.upper().startswith("TOTAL"):
                continue
            if not _looks_like_material_row(non_empty):
                continue
            parsed.material_lines.append(
                {
                    "sheet": sheet,
                    "row": offset + 1,
                    "description": label,
                    "detail": " · ".join(non_empty[1:4]),
                    "file": parsed.file_name,
                    "sourceLocation": f"{parsed.file_name} → sheet '{sheet}' → row {offset + 1}",
                }
            )
        if len(parsed.material_lines) >= 400:
            break


_MATERIAL_TOKENS = (
    "FABRIC",
    "SHADE",
    "ROLLER",
    "SOLAR",
    "BLACKOUT",
    "DUAL",
    "FASCIA",
    "SYSTEM",
    "MOTOR",
    "BRACKET",
    "TUBE",
    "HEMBAR",
    "CASSETTE",
    "POCKET",
    "CELLULAR",
    "SCREEN",
)


def _looks_like_material_row(non_empty: list[str]) -> bool:
    joined = " ".join(non_empty).upper()
    return any(tok in joined for tok in _MATERIAL_TOKENS)


def _str_cells(row: tuple | list) -> list[str]:
    return [str(c).strip() if c is not None else "" for c in row]


def _cell(cells: list, idx: Optional[int]) -> str:
    if idx is None or idx >= len(cells):
        return ""
    v = cells[idx]
    return str(v).strip() if v is not None else ""


def _int_cell(cells: list, idx: Optional[int]) -> Optional[int]:
    if idx is None or idx >= len(cells):
        return None
    v = cells[idx]
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


_MONEY_CLEAN_RE = re.compile(r"[$,\s]")


def _to_float(value: Any) -> Optional[float]:
    """Parse a money/number cell. Accepts 1234, '$1,234.00', '(500)' for negatives."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = _MONEY_CLEAN_RE.sub("", text.strip("()"))
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        return None
    number = float(cleaned)
    return -number if negative else number


def _float_cell(cells: list, idx: Optional[int]) -> Optional[float]:
    if idx is None or idx >= len(cells):
        return None
    return _to_float(cells[idx])


def _money(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "—"
    return f"${number:,.2f}"


def _norm_area(value: str) -> str:
    text = re.sub(r"[^A-Z0-9 ]+", " ", (value or "").upper())
    text = re.sub(r"\bUNITS?\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _area_tokens(value: str) -> set[str]:
    stop = {"AREA", "AREAS", "ROOM", "ROOMS", "LEVEL", "FLOOR", "QTY", "BLIND", "SHEET"}
    tokens = set()
    for word in _norm_area(value).split():
        if word in stop or len(word) <= 2 or word.isdigit():
            continue
        tokens.add(word[:-1] if word.endswith("S") and len(word) > 4 else word)
    return tokens


def areas_match(left: str, right: str) -> bool:
    """True when two area labels name the same part of a building (any project)."""
    a, b = _norm_area(left), _norm_area(right)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ta, tb = _area_tokens(left), _area_tokens(right)
    return bool(ta and tb and (ta & tb))


#: Share of a sheet's window tags that must already appear on the matrix for the sheet
#: to count as detail behind the TOTAL row rather than extra scope.
_TAG_OVERLAP_THRESHOLD = 0.5


def _sheet_is_on_matrix(
    area: str,
    lines: list[dict[str, Any]],
    matrix_sections: list[str],
    matrix_tags: set[str],
    matrix_total: Optional[int],
) -> bool:
    """
    Decide whether a Blind QTY sheet is already inside the matrix TOTAL.

    A sheet whose area matches a matrix section is detail behind that total.
    A named area that matches nothing is extra scope and is added once, even when
    some window tags are reused — the same tag in another room is a different opening.
    Tag overlap is only a fallback when the sheet has no area name at all.
    """
    if matrix_sections:
        if area and any(areas_match(area, section) for section in matrix_sections):
            return True
        row_match = any(
            areas_match(ln.get("section") or "", section)
            for ln in lines
            for section in matrix_sections
            if ln.get("section")
        )
        if row_match:
            return True
        if area or any(ln.get("section") for ln in lines):
            return False

    sheet_tags = {_norm_tag(ln.get("windowTag") or "") for ln in lines}
    sheet_tags.discard("")
    if sheet_tags and matrix_tags:
        overlap = len(sheet_tags & matrix_tags) / len(sheet_tags)
        if overlap >= _TAG_OVERLAP_THRESHOLD:
            return True

    # No named sections: the quantity sheet is the detail behind the matrix, not extra scope.
    return matrix_total is not None


def project_shade_scope(
    matrix_rows: list[dict[str, Any]],
    matrix_total: Optional[int],
    blind_lines: list[dict[str, Any]],
    blind_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Matrix TOTAL covers only the sections printed on the matrix.
    Quantity-sheet areas that are not those sections are added once.
    """
    matrix_sections = []
    for row in matrix_rows:
        section = (row.get("section") or "").strip()
        if section and section not in matrix_sections:
            matrix_sections.append(section)
    matrix_tags = {_norm_tag(row.get("marking") or "") for row in matrix_rows}
    matrix_tags.discard("")

    by_sheet: dict[str, list[dict[str, Any]]] = {}
    for line in blind_lines:
        by_sheet.setdefault(line.get("sheet") or "", []).append(line)

    section_area = {s.get("sheet"): s.get("area") or "" for s in blind_sections}
    declared = {s.get("sheet"): s.get("declaredTotal") for s in blind_sections}

    additional = 0
    additional_areas: list[str] = []
    covered = 0
    for sheet, lines in by_sheet.items():
        area = section_area.get(sheet) or ""
        on_matrix = _sheet_is_on_matrix(
            area, lines, matrix_sections, matrix_tags, matrix_total
        )
        qty = declared.get(sheet) or sum(int(ln.get("quantity") or 0) for ln in lines)
        if on_matrix:
            covered += qty
        else:
            additional += qty
            additional_areas.append(area or sheet)

    if matrix_total is not None:
        project_total = matrix_total + additional
    else:
        project_total = covered + additional

    return {
        "projectShadeCount": project_total,
        "additionalShadeCount": additional,
        "additionalAreas": additional_areas,
        "matrixCoveredShadeCount": covered,
    }


def _combined_bid(workbooks: list[ParsedWorkbook]) -> Optional[ParsedWorkbook]:
    """Bid tabs from every file. A price sheet that rides along in the matrix file still counts."""
    bids = [w for w in workbooks if w.bid_tabs or w.bid_lines]
    if not bids:
        return None
    if len(bids) == 1:
        return bids[0]
    combined = ParsedWorkbook(
        kind=WorkbookKind.BID_SUMMARY,
        file_name=", ".join(w.file_name for w in bids),
        bid_sheets=[name for w in bids for name in (w.bid_sheets or w.sheets)],
    )
    for workbook in bids:
        combined.bid_tabs.extend(workbook.bid_tabs)
        combined.bid_lines.extend(workbook.bid_lines)
    _mark_bid_roles(combined)
    base_totals = [
        tab.get("productTotal") or tab.get("total")
        for tab in combined.bid_tabs
        if tab.get("role") == "base" and (tab.get("productTotal") or tab.get("total"))
    ]
    if base_totals:
        combined.bid_grand_total = round(sum(base_totals), 2)
    return combined


def _measurement_reference(
    sections: list[dict[str, Any]],
    blind_lines: list[dict[str, Any]],
    bid_lines: list[dict[str, Any]],
    bid_tabs: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Say, in the words of this upload, which column is the opening size and
    whether the bid sheet's width and height are that opening or a different size.
    """
    groups: dict[tuple, list[str]] = {}
    points: list[dict[str, Any]] = []
    for section in sections:
        measure = section.get("measurement") or {}
        width = measure.get("width")
        height = measure.get("height")
        if not width and not height:
            continue
        key = (
            (width or {}).get("used"),
            (width or {}).get("method"),
            tuple((width or {}).get("checkedAgainst") or []),
            (height or {}).get("used"),
            (height or {}).get("method"),
            tuple((height or {}).get("checkedAgainst") or []),
        )
        groups.setdefault(key, []).append(section.get("sheet") or "")
        points.append({"sheet": section.get("sheet"), "width": width, "height": height})

    sentences: list[str] = []
    for key, names in groups.items():
        w_used, w_method, w_check, h_used, h_method, h_check = key
        where = ", ".join(name for name in names if name)
        parts = []
        if w_used:
            parts.append(f"width from “{w_used}” ({w_method})")
        if h_used:
            parts.append(f"height from “{h_used}” ({h_method})")
        sentence = f"On {where}, the opening size uses " + " and ".join(parts) + "."
        checks = []
        if w_check:
            checks.append("width columns " + " + ".join(w_check))
        if h_check:
            checks.append("height columns " + " + ".join(h_check))
        if checks:
            sentence += " Checked against " + " and ".join(checks) + "."
        sentences.append(sentence)

    offset = _bid_size_offset(blind_lines, bid_lines)
    if offset:
        width_name = _quoted_headers(bid_tabs, "widthHeader") or "the width column"
        height_name = _quoted_headers(bid_tabs, "heightHeader") or "the height column"
        sentences.append(_offset_sentence(offset, width_name, height_name))

    return {"note": " ".join(sentences), "points": points, "bidOffset": offset}


def _quoted_headers(tabs: list[dict[str, Any]], key: str) -> str:
    names = []
    for tab in tabs:
        if tab.get("role") == "alternate":
            continue
        name = tab.get(key)
        if name and name not in names:
            names.append(name)
    return " and ".join(f"“{name}”" for name in names)


def _bid_size_offset(
    blind_lines: list[dict[str, Any]],
    bid_lines: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    openings: dict[str, tuple[float, float]] = {}
    for line in blind_lines:
        tag = _norm_tag(line.get("windowTag") or "")
        width = _to_float(line.get("widthInches"))
        height = _to_float(line.get("heightInches"))
        if tag and width and height:
            openings.setdefault(tag, (width, height))
    width_deltas: list[float] = []
    height_deltas: list[float] = []
    for line in bid_lines:
        if line.get("role") == "alternate":
            continue
        opening = openings.get(_norm_tag(line.get("description") or ""))
        width = _to_float(line.get("width"))
        height = _to_float(line.get("height"))
        if not opening or width is None or height is None:
            continue
        width_deltas.append(round(width - opening[0], 1))
        height_deltas.append(round(height - opening[1], 1))
    if len(height_deltas) < 2:
        return None
    width_value, width_share = _mode_share(width_deltas)
    height_value, height_share = _mode_share(height_deltas)
    return {
        "compared": len(height_deltas),
        "widthDelta": width_value,
        "widthShare": round(width_share, 2),
        "heightDelta": height_value,
        "heightShare": round(height_share, 2),
    }


def _mode_share(values: list[float]) -> tuple[float, float]:
    counts: dict[float, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    chosen = max(counts, key=lambda item: counts[item])
    return chosen, counts[chosen] / len(values)


def _offset_sentence(offset: dict[str, Any], width_name: str, height_name: str) -> str:
    if offset["widthShare"] < 0.8 or offset["heightShare"] < 0.8:
        return (
            "The bid sheet’s width and height do not follow one difference from the opening. "
            "The shade list uses the opening size."
        )
    width_delta = offset["widthDelta"]
    height_delta = offset["heightDelta"]
    if width_delta == 0 and height_delta == 0:
        return f"The bid columns {width_name} and {height_name} match the opening size."

    def _delta(name: str, delta: float) -> str:
        if delta == 0:
            return f"{name} matches the opening"
        direction = "greater" if delta > 0 else "less"
        return f"{name} is {abs(delta):g} in {direction} than the opening"

    return (
        f"The bid columns {width_name} and {height_name} are the priced size, not the opening. "
        f"On {offset['compared']} matched lines, {_delta(width_name, width_delta)} and "
        f"{_delta(height_name, height_delta)}. The shade list uses the opening."
    )


def merge_workbook_analyses(workbooks: list[ParsedWorkbook]) -> dict[str, Any]:
    """Single structure for pipeline + UI."""
    matrix = next(
        (w for w in workbooks if w.matrix_total_shades is not None or w.matrix_rows),
        None,
    )
    material_lines = [line for w in workbooks for line in w.material_lines]
    bid = _combined_bid(workbooks)

    names = []
    for w in workbooks:
        if w.project_name and w.project_name not in names:
            names.append(w.project_name)
    project = names[0] if names else None
    conflict = ""
    if len(names) > 1:
        conflict = (
            "These files name more than one project ("
            + ", ".join(names)
            + "). They were read as one job. Upload one project at a time."
        )

    prompt_parts = [w.to_prompt_block() for w in workbooks]
    authoritative_shades = matrix.matrix_total_shades if matrix else None
    blind_lines = [line for w in workbooks for line in w.blind_qty_lines]
    blind_sections = [sec for w in workbooks for sec in w.blind_qty_sections]
    matrix_rows = matrix.matrix_rows if matrix else []
    scope = project_shade_scope(matrix_rows, authoritative_shades, blind_lines, blind_sections)
    measurement = _measurement_reference(
        blind_sections,
        blind_lines,
        bid.bid_lines if bid else [],
        bid.bid_tabs if bid else [],
    )

    return {
        "projectName": project,
        "files": [{"name": w.file_name, "kind": w.kind.value, "sheets": w.sheets} for w in workbooks],
        "authoritativeTotalShades": authoritative_shades,
        "projectShadeCount": scope["projectShadeCount"],
        "additionalShadeCount": scope["additionalShadeCount"],
        "additionalAreas": scope["additionalAreas"],
        "authoritativeTotalWindows": matrix.matrix_total_windows if matrix else None,
        "windowMatrixMarkings": matrix_rows,
        "levelTotals": matrix.level_totals if matrix else {},
        "blindQtySections": blind_sections,
        "blindQtyLines": blind_lines,
        "measurementReference": measurement,
        "materialSheets": [w.file_name for w in workbooks if w.material_lines],
        "materialLines": material_lines,
        "bidSheets": bid.bid_sheets if bid else [],
        "referencePricing": _reference_pricing(
            bid, scope["projectShadeCount"], authoritative_shades
        ),
        "guidance": _workbook_guidance(),
        "promptBlock": "\n\n---\n\n".join(prompt_parts),
        "notes": ([conflict] if conflict else []) + [n for w in workbooks for n in w.notes],
    }


def _reference_pricing(
    bid: Optional[ParsedWorkbook],
    shade_count: Optional[int],
    matrix_total: Optional[int] = None,
) -> dict[str, Any]:
    """
    Sell prices from the bid workbook.

    Tabs that repeat the same window markings are alternates. The quoted total adds
    one tab from each group (the first tab in the file) and leaves the others out.
    """
    if bid is None or (not bid.bid_tabs and not bid.bid_lines):
        return {}

    grand_total = bid.bid_grand_total
    base_tabs = [t for t in bid.bid_tabs if t.get("role") != "alternate"]
    alt_tabs = [t for t in bid.bid_tabs if t.get("role") == "alternate"]
    base_lines = [ln for ln in bid.bid_lines if ln.get("role") != "alternate"]
    unit_rate: Optional[float] = None
    basis = ""
    divisor = shade_count or matrix_total
    if grand_total and divisor:
        unit_rate = round(grand_total / divisor, 2)
        if matrix_total and divisor == matrix_total:
            basis = (
                f"{_money(grand_total)} quoted bid total (alternate tabs excluded) "
                f"÷ {divisor} shades (WINDOW MATRIX TOTAL row)"
            )
        else:
            basis = (
                f"{_money(grand_total)} quoted bid total (alternate tabs excluded) "
                f"÷ {divisor} project shades"
            )
    elif grand_total:
        qty = sum(int(t.get("productQuantity") or 0) for t in base_tabs)
        if qty:
            unit_rate = round(grand_total / qty, 2)
            basis = f"{_money(grand_total)} ÷ {qty} shades on the quoted bid tabs"

    client_values = [
        (t.get("commercial") or {}).get("clientTotal")
        for t in base_tabs
        if (t.get("commercial") or {}).get("clientTotal")
    ]
    client_total = (
        round(sum(float(value) for value in client_values), 2)
        if client_values and len(client_values) == len(base_tabs)
        else None
    )

    return {
        "source": bid.file_name,
        "grandTotal": grand_total,
        "clientTotal": client_total,
        "grandTotalBasis": (
            "Sum of Total Price on quoted tabs. Tabs that price the same markings are alternates and are not added."
            if grand_total
            else ""
        ),
        "priceStack": [_price_stack_entry(t) for t in base_tabs],
        "alternateOptions": [_price_stack_entry(t) for t in alt_tabs],
        "tabs": bid.bid_tabs,
        "quotedSheets": [t["sheet"] for t in base_tabs],
        "alternateSheets": [t["sheet"] for t in alt_tabs],
        "lines": base_lines,
        "alternateLines": [ln for ln in bid.bid_lines if ln.get("role") == "alternate"],
        "lineCount": len(base_lines),
        "unitRate": unit_rate,
        "unitRateBasis": basis,
    }


def _price_stack_entry(tab: dict[str, Any]) -> dict[str, Any]:
    commercial = tab.get("commercial") or {}
    return {
        "sheet": tab.get("sheet"),
        "product": tab.get("productTotal") if tab.get("productTotal") is not None else tab.get("total"),
        "installation": commercial.get("installation"),
        "charges": commercial.get("charges") or [],
        "tax": commercial.get("tax"),
        "totalBid": commercial.get("totalBid"),
        "motorQuantity": commercial.get("motorQuantity"),
        "motorizedTotal": commercial.get("motorizedTotal"),
        "clientTotal": commercial.get("clientTotal"),
    }


def _workbook_guidance() -> str:
    return """
Workbook set (read the header row; file names and area names change per project):
  1. A sheet headed WINDOW MARKINGS and TOTAL SHADES — that TOTAL is the count for the sections on that sheet.
     A sheet headed with a window tag, a quantity, and width/height is the opening size.
     Use the finished inch size, or feet plus leftover inches.
     The same window tag can appear in more than one area. Keep those as separate lines.
     An area that is not on the matrix is extra scope and is added once.
  2. MATERIAL SUMMARY — fabric and system descriptions.
  3. A sheet headed Sales Price or Total Price — that is the sell price. Tabs that repeat the same markings are alternate prices. Do not add them together, and do not use them as the shade count. The client total is the Total Bid row (or the motorization grand total when motors are quantified), not the accounting grand total.
""".strip()
