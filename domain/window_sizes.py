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


_MATERIAL_MARK = {
    "AL",
    "ALUM",
    "BRONZE",
    "FIXED",
    "GL",
    "GLASS",
    "HM",
    "IMPACT",
    "MTL",
    "PTD",
    "SCW",
    "WD",
}


def read_schedule_dictionary(data: bytes, opening_class: str = "WINDOW") -> list[dict[str, Any]]:
    """
    One record per schedule mark, in its own WINDOW, DOOR, or STOREFRONT namespace.

    A blank width or height stays null. Two different sizes for one mark stay
    side by side and neither one is chosen. A value is never copied down from
    the row above. Elevation dimensions that are not in these columns are ignored.
    """
    default = _class_name(opening_class)
    words = _words_from_pdf(data)
    if not words:
        return []
    tables = _tables(words)
    found: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for index, table in enumerate(tables):
        limit = tables[index + 1]["y"] if index + 1 < len(tables) else table["y"] + 900
        klass = _table_class(words, table["y"], default)
        for row in _rows_between(words, table["y"], limit, table):
            mark = _mark_beside_width(row, table)
            if not mark:
                continue
            width = _dim_on_row(row, table["width_x"], table["height_x"])
            height = _dim_on_row(row, table["height_x"], table["width_x"])
            mark_word = min(row, key=lambda word: abs(word["x"] - (table["width_x"] - 40)))
            found.setdefault((klass, mark), []).append(
                {
                    "width": width,
                    "height": height,
                    "restrictions": _restriction_text(row, mark),
                    "glazed": _row_has(row, ("GLASS", "GLAZ", "SIDELITE", "LITE")),
                    "opaque": _row_opaque(row),
                    "bounding_box": [round(mark_word["x"], 1), round(mark_word["y"], 1)],
                }
            )
    records = []
    for (klass, mark), observations in found.items():
        records.append(_collapse_mark(klass, mark, observations))
    records.sort(key=lambda record: (record["opening_class"], record["mark_normalized"]))
    return records


def _class_name(value: str) -> str:
    name = (value or "WINDOW").strip().upper()
    if name not in {"WINDOW", "DOOR", "STOREFRONT"}:
        return "WINDOW"
    return name


def _table_class(words: list[dict[str, Any]], table_y: float, default: str) -> str:
    """The schedule title just above this header, not the sheet number."""
    line_ys = [
        word["y"]
        for word in words
        if table_y - 90 <= word["y"] < table_y - 4 and "SCHEDULE" in word["t"].upper()
    ]
    if not line_ys:
        return default
    title_y = max(line_ys)
    title = " ".join(
        word["t"].upper() for word in words if abs(word["y"] - title_y) < 10
    )
    has_window = "WINDOW" in title
    has_door = "DOOR" in title
    has_storefront = "STOREFRONT" in title
    if has_door and not has_window:
        return "DOOR"
    if has_storefront and not has_window:
        return "STOREFRONT"
    if has_window and not has_storefront:
        return "WINDOW"
    return default


def _mark_beside_width(row: list[dict[str, Any]], table: dict[str, Any]) -> str:
    """The mark in the column beside WIDTH. A note under a far TYPE header is not a mark."""
    found = []
    for word in row:
        gap = table["width_x"] - word["x"]
        if gap < 8 or gap > 160:
            continue
        if not _is_schedule_mark(word["t"]):
            continue
        found.append((gap, word["t"]))
    if not found:
        return ""
    found.sort()
    return found[0][1].strip().upper()


def _is_schedule_mark(token: str) -> bool:
    text = token.strip().upper().strip(".,:;")
    if text in _NOT_A_MARK or text in _MATERIAL_MARK:
        return False
    if not _is_mark(text, under_header=False):
        return False
    if text.isdigit():
        return False
    return True


def _restriction_text(row: list[dict[str, Any]], mark: str) -> str:
    kept = []
    for word in row:
        token = word["t"].strip()
        if token.upper() == mark:
            continue
        if not re.search(r"FLOOR|LEVEL|GROUND|TYPICAL|TERRACE|UNITS?\b|\d(?:ST|ND|RD|TH)\b", token, re.I):
            continue
        kept.append(token)
    return " ".join(kept)


def _row_has(row: list[dict[str, Any]], needles: tuple[str, ...]) -> bool:
    for word in row:
        text = word["t"].upper()
        if any(needle in text for needle in needles):
            return True
    return False


def _row_opaque(row: list[dict[str, Any]]) -> bool:
    if _row_has(row, ("GLASS", "GLAZ", "SIDELITE")):
        return False
    return _row_has(row, ("WOOD", "METAL", "MTL", "HOLLOW", "SCW"))


def _collapse_mark(klass: str, mark: str, observations: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [item for item in observations if item["width"] and item["height"]]
    sizes = {(item["width"][1], item["height"][1]): item for item in complete}
    restrictions = " ".join(dict.fromkeys(item["restrictions"] for item in observations if item["restrictions"]))
    glazed = any(item["glazed"] for item in observations)
    opaque_flags = [item["opaque"] for item in observations if item["width"] or item["height"] or item["opaque"]]
    opaque = bool(opaque_flags) and all(opaque_flags) and not glazed
    conflicts = [
        {
            "width_original": item["width"][0],
            "height_original": item["height"][0],
            "width_inches": item["width"][1],
            "height_inches": item["height"][1],
        }
        for item in sizes.values()
    ]
    record = {
        "opening_class": klass,
        "mark_raw": mark,
        "mark_normalized": mark,
        "width_original": None,
        "height_original": None,
        "width_inches": None,
        "height_inches": None,
        "dimension_basis": None,
        "restrictions": restrictions,
        "glazed": glazed,
        "opaque": opaque,
        "conflicts": [],
        "issue_codes": [],
        "bounding_box": observations[0].get("bounding_box"),
    }
    if len(sizes) > 1:
        record["conflicts"] = conflicts
        record["issue_codes"] = ["DIMENSION_CONFLICT"]
        return record
    if len(sizes) == 1:
        item = next(iter(sizes.values()))
        record["width_original"] = item["width"][0]
        record["height_original"] = item["height"][0]
        record["width_inches"] = item["width"][1]
        record["height_inches"] = item["height"][1]
        record["dimension_basis"] = "schedule"
        return record
    record["issue_codes"] = ["BLANK_DIMENSION"]
    return record


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
