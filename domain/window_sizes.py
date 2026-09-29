"""
Width and height for each window mark, taken from the schedule's own columns.

The sheet decides the columns. A header such as WIDTH, W, HEIGHT, H, TYPE,
MARK, or WINDOW NO. names them. A size is kept only when that mark's row
prints both a width cell and a height cell in those columns. Dimensions drawn
on an elevation are not in those columns, so they are not used.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from domain.direct_shades_workbooks import _bare_dim_label
from utils.dimensions import parse_length_inches

_NOT_A_MARK = {
    "ALL",
    "AND",
    "FOR",
    "HEIGHT",
    "NEW",
    "NO",
    "NOT",
    "NOTE",
    "NOTES",
    "PER",
    "SEE",
    "THE",
    "TYPE",
    "WIDTH",
    "YES",
}
_MARK_TEXT = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,11}$")


def read_window_sizes(data: bytes) -> dict[str, dict[str, Any]]:
    """
    Map each window mark to the width and height printed on its schedule row.

    The same mark with two different sizes is dropped. The sheet did not agree
    with itself, and a guessed size would be wrong on the next project too.
    """
    words = _words_from_pdf(data)
    if not words:
        return {}
    tables = _tables(words)
    found: dict[str, Optional[dict[str, Any]]] = {}
    for index, table in enumerate(tables):
        limit = tables[index + 1]["y"] if index + 1 < len(tables) else table["y"] + 700
        for row in _rows_between(words, table["y"], limit, table):
            mark = _mark_on_row(row, table)
            width = _dim_on_row(row, table["width_x"], table["height_x"])
            height = _dim_on_row(row, table["height_x"], table["width_x"])
            if not mark or not width or not height:
                continue
            size = {
                "width": width[0],
                "height": height[0],
                "widthInches": width[1],
                "heightInches": height[1],
            }
            previous = found.get(mark, ...)
            if previous is ...:
                found[mark] = size
            elif previous is None or _differs(previous, size):
                found[mark] = None
    return {mark: size for mark, size in found.items() if size}


def _tables(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tables = []
    for row in _cluster(words, 12):
        widths = [word for word in row if _is_width_header(word["t"])]
        heights = [word for word in row if _is_height_header(word["t"])]
        if not widths or not heights:
            continue
        width, height = min(
            ((w, h) for w in widths for h in heights if abs(w["x"] - h["x"]) < 140),
            key=lambda pair: abs(pair[0]["x"] - pair[1]["x"]),
            default=(None, None),
        )
        if width is None or height is None:
            continue
        span = (min(width["x"], height["x"]), max(width["x"], height["x"]))
        mark_headers = [
            word
            for word in row
            if _is_mark_header(word["t"]) and abs(word["x"] - width["x"]) < 260
        ]
        tables.append(
            {
                "y": width["y"],
                "width_x": width["x"],
                "height_x": height["x"],
                "mark_x": mark_headers[0]["x"] if mark_headers else None,
                "left": span[0],
                "right": span[1],
            }
        )
    tables.sort(key=lambda table: table["y"])
    return tables


def _rows_between(
    words: list[dict[str, Any]],
    top: float,
    bottom: float,
    table: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    band_left = min(table["left"], table["mark_x"] or table["left"]) - 80
    band_right = max(table["right"], table["mark_x"] or table["right"]) + 80
    picked = [
        word
        for word in words
        if top + 8 < word["y"] < bottom - 4 and band_left <= word["x"] <= band_right
    ]
    return _cluster(picked, 8)


def _mark_on_row(row: list[dict[str, Any]], table: dict[str, Any]) -> str:
    if table["mark_x"] is not None:
        aligned = [
            word["t"]
            for word in row
            if _is_mark(word["t"], under_header=True) and abs(word["x"] - table["mark_x"]) < 24
        ]
        if len(aligned) == 1:
            return aligned[0].upper()
    beside = []
    for word in row:
        if not _is_mark(word["t"], under_header=False):
            continue
        gap_left = table["left"] - word["x"]
        gap_right = word["x"] - table["right"]
        if 6 < gap_left < 60 or 6 < gap_right < 60:
            beside.append(word["t"])
    if len(beside) == 1:
        return beside[0].upper()
    return ""


def _dim_on_row(
    row: list[dict[str, Any]],
    column_x: float,
    other_x: Optional[float],
) -> Optional[tuple[str, float]]:
    """The cell in this column. A value closer to the other header belongs there."""
    reach = 40.0
    if other_x is not None:
        reach = max(14.0, min(40.0, abs(column_x - other_x) / 2))
    chosen = None
    for word in row:
        distance = abs(word["x"] - column_x)
        if distance > reach:
            continue
        if other_x is not None and abs(word["x"] - other_x) < distance:
            continue
        parsed = _as_length(word["t"])
        if parsed is None:
            continue
        if chosen is not None:
            return None
        chosen = parsed
    return chosen


def _is_width_header(token: str) -> bool:
    label = _header_label(token)
    if "HEIGHT" in label:
        return False
    return "WIDTH" in label or label in {"WD", "DIM W"} or _bare_dim_label(label, "W")


def _is_height_header(token: str) -> bool:
    label = _header_label(token)
    if "WIDTH" in label:
        return False
    return "HEIGHT" in label or label in {"HT", "DIM H"} or _bare_dim_label(label, "H")


def _is_mark_header(token: str) -> bool:
    label = _header_label(token)
    if label in {"TYPE", "MARK", "TAG", "ID", "MARKING"}:
        return True
    if "WINDOW" in label and any(part in label for part in ("NO", "NUMBER", "#", "MARK", "TYPE", "TAG")):
        return True
    return False


def _is_mark(token: str, *, under_header: bool) -> bool:
    text = token.strip().upper().strip(".,:;")
    if text in _NOT_A_MARK or not _MARK_TEXT.match(text):
        return False
    if _as_length(text) is not None and ("'" in text or '"' in text):
        return False
    if text.isdigit():
        return under_header and len(text) <= 4
    letters = re.sub(r"[^A-Z]", "", text)
    if letters.isalpha() and len(letters) > 3 and not re.search(r"\d", text):
        return False
    return True


def _as_length(token: str) -> Optional[tuple[str, float]]:
    inches = parse_length_inches(token)
    if inches is None or inches <= 0 or inches > 480:
        return None
    if not re.search(r"\d", token):
        return None
    feet = int(inches // 12)
    rest = inches - feet * 12
    if "'" in token or '"' in token:
        if abs(rest - round(rest)) < 0.05:
            rest_text = str(int(round(rest)))
        else:
            rest_text = f"{rest:.1f}".rstrip("0").rstrip(".")
        display = f"{feet}'-{rest_text}\""
    else:
        display = f"{inches:g}\""
    return display, round(inches, 2)


def _differs(previous: dict[str, Any], size: dict[str, Any]) -> bool:
    return previous["widthInches"] != size["widthInches"] or previous["heightInches"] != size["heightInches"]


def _header_label(token: str) -> str:
    return re.sub(r"\s+", " ", token.upper().strip(" .:"))


def _cluster(words: list[dict[str, Any]], tolerance: float) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (item["y"], item["x"])):
        if rows and abs(word["y"] - rows[-1][0]["y"]) <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    return rows


def _words_from_pdf(data: bytes) -> list[dict[str, Any]]:
    try:
        import fitz
    except Exception:
        return []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return []
    if doc.page_count < 1:
        doc.close()
        return []
    matrix = doc[0].rotation_matrix
    words = []
    for item in doc[0].get_text("words"):
        p0 = fitz.Point(item[0], item[1]) * matrix
        p1 = fitz.Point(item[2], item[3]) * matrix
        x0, y0 = min(p0.x, p1.x), min(p0.y, p1.y)
        x1, y1 = max(p0.x, p1.x), max(p0.y, p1.y)
        words.append({"t": item[4], "x": (x0 + x1) / 2, "y": (y0 + y1) / 2})
    doc.close()
    return words
