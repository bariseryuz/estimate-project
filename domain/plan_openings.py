"""
Count glazed openings on a unit-plan sheet from the marks the sheet draws.

Window tags on these plans sit in an oval or a hexagon. Door tags sit in a
small square. The floor plan is the drawing above the "UNIT TYPE … FLOOR PLAN"
title. Elevations and the reflected ceiling plan are not counted.
"""

from __future__ import annotations

import re
from typing import Any, Optional

_ROOM = re.compile(
    r"^(?:LIVING(?:/DINING)?|DINING|BEDROOM|BATH(?:ROOM)?|DEN|KITCHEN|MASTER)$",
    re.IGNORECASE,
)
_TAG = re.compile(r"^(?=.*[A-Z])(?:[A-Z]{1,3}\d{0,3}|\d{1,3}[A-Z]{1,2})$")
_BLIND = re.compile(r"\bblinds?\b", re.IGNORECASE)


def count_plan_openings(data: bytes) -> Optional[dict[str, Any]]:
    """
    Windows, shades, and blinds printed on one unit plan.

    None means the sheet has no floor-plan title, so the marks were not read.
    A bathroom window counts as a window and not as a shade.
    """
    try:
        import fitz
    except Exception:
        return None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return None
    if doc.page_count < 1:
        doc.close()
        return None
    page = doc[0]
    words = _visual_words(page)
    title = _floor_title(words)
    if title is None:
        doc.close()
        return None
    rooms = _rooms_above_title(words, title)
    if not rooms:
        doc.close()
        return None
    box = _plan_box(rooms, title, page.rect.width)
    window_rects, door_rects = _mark_rects(page)
    window_hits = []
    door_hits = []
    for word in words:
        if not box.contains(fitz.Point(word["x"], word["y"])):
            continue
        token = word["t"].strip().upper()
        if not _TAG.match(token):
            continue
        point = fitz.Point(word["ux"], word["uy"])
        if _closer_to_elevation(word, words, rooms):
            continue
        room = min(rooms, key=lambda item: (item["x"] - word["x"]) ** 2 + (item["y"] - word["y"]) ** 2)
        hit = {
            "tag": token,
            "room": room["t"].upper(),
            "x": word["x"],
            "y": word["y"],
            "ux": word["ux"],
            "uy": word["uy"],
        }
        if any(rect.contains(point) for rect in door_rects):
            door_hits.append(hit)
            continue
        if not any(rect.contains(point) for rect in window_rects):
            continue
        window_hits.append(hit)
    window_hits = _dedupe(window_hits)
    door_hits = _dedupe(door_hits)
    openings = _assemblies(window_hits, window_rects)
    untagged = _untagged(window_rects, window_hits, box, words, rooms, page.rotation_matrix)
    doc.close()
    windows = len(openings) + len(untagged)
    shades = sum(1 for opening in openings if not str(opening["room"]).startswith("BATH"))
    blind_word = any(_BLIND.search(word["t"]) for word in words)
    return {
        "windows": windows,
        "shades": shades,
        "blinds": 0 if not blind_word else None,
        "tags": [{"tag": opening["mark"], "room": opening["room"]} for opening in openings if opening.get("mark")],
        "openings": openings,
        "doors": [{"tag": hit["tag"], "room": hit["room"]} for hit in door_hits],
        "untagged": untagged,
        "notes": (
            "Each window tag on the unit floor plan is one opening. "
            "A reflected ceiling plan, elevation, or detail is not a second opening. "
            "Square door tags are doors. A shade quantity is not assigned here."
        ),
    }


def _visual_words(page) -> list[dict[str, Any]]:
    import fitz

    matrix = page.rotation_matrix
    words = []
    for item in page.get_text("words"):
        p0 = fitz.Point(item[0], item[1]) * matrix
        p1 = fitz.Point(item[2], item[3]) * matrix
        x0, y0 = min(p0.x, p1.x), min(p0.y, p1.y)
        x1, y1 = max(p0.x, p1.x), max(p0.y, p1.y)
        words.append(
            {
                "t": item[4],
                "x": (x0 + x1) / 2,
                "y": (y0 + y1) / 2,
                "ux": (item[0] + item[2]) / 2,
                "uy": (item[1] + item[3]) / 2,
            }
        )
    return words


def _floor_title(words: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    best = None
    for word in words:
        if word["t"].upper() != "FLOOR":
            continue
        near = [other for other in words if abs(other["y"] - word["y"]) < 16 and abs(other["x"] - word["x"]) < 400]
        labels = {other["t"].upper() for other in near}
        if "PLAN" not in labels or "UNIT" not in labels:
            continue
        if "REFLECTED" in labels:
            continue
        if best is None or word["x"] > best["x"]:
            best = word
    return best


def _rooms_above_title(words: list[dict[str, Any]], title: dict[str, Any]) -> list[dict[str, Any]]:
    rooms = []
    for word in words:
        if not _ROOM.match(word["t"]):
            continue
        if word["y"] > title["y"] - 16:
            continue
        if abs(word["x"] - title["x"]) > 1200:
            continue
        if word["y"] < title["y"] - 1400:
            continue
        rooms.append(word)
    return rooms


def _plan_box(rooms: list[dict[str, Any]], title: dict[str, Any], page_width: float):
    import fitz

    pad = 480
    x0 = min(room["x"] for room in rooms) - pad
    y0 = min(room["y"] for room in rooms) - pad
    x1 = min(page_width - 40, max(room["x"] for room in rooms) + pad)
    y1 = title["y"] - 8
    return fitz.Rect(x0, y0, x1, y1)


def _mark_rects(page) -> tuple[list[Any], list[Any]]:
    windows = []
    doors = []
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        kind = _shape_kind(rect.width, rect.height, drawing.get("items") or [])
        if kind == "window":
            windows.append(rect)
        elif kind == "door":
            doors.append(rect)
    return windows, doors


def _shape_kind(width: float, height: float, items: list) -> Optional[str]:
    if width > 52 or height > 52 or width < 8 or height < 8:
        return None
    kinds = "".join(item[0] for item in items)
    if kinds in {"l", "ll"} and max(width, height) <= 14:
        return None
    if abs(width - height) <= 3.5 and 8 <= width <= 22 and (kinds.startswith("q") or kinds == "re"):
        return "door"
    aspect = max(width, height) / max(min(width, height), 0.1)
    if aspect >= 1.35 and max(width, height) <= 48:
        return "window"
    if kinds.count("l") >= 6 and 12 <= max(width, height) <= 40:
        return "window"
    return None


def _closer_to_elevation(word: dict[str, Any], words: list[dict[str, Any]], rooms: list[dict[str, Any]]) -> bool:
    room_distance = min((room["x"] - word["x"]) ** 2 + (room["y"] - word["y"]) ** 2 for room in rooms)
    for other in words:
        if other["t"].upper() not in {"ELEVATION", "ENLARGED"}:
            continue
        if (other["x"] - word["x"]) ** 2 + (other["y"] - word["y"]) ** 2 < room_distance:
            return True
    return False


def _assemblies(hits: list[dict[str, Any]], window_rects: list[Any]) -> list[dict[str, Any]]:
    """Tags inside one window outline are one opening. The outline is not counted again."""
    import fitz

    groups: dict[int, list[dict[str, Any]]] = {}
    for hit in hits:
        point = fitz.Point(hit["ux"], hit["uy"])
        containers = [rect for rect in window_rects if rect.contains(point)]
        if not containers:
            groups.setdefault(id(hit), [hit])
            continue
        rect = min(containers, key=lambda item: item.width * item.height)
        groups.setdefault(id(rect), []).append(hit)
    openings = []
    for group in groups.values():
        marks = []
        for hit in group:
            if hit["tag"] not in marks:
                marks.append(hit["tag"])
        primary = group[0]
        openings.append(
            {
                "opening_class": "WINDOW",
                "mark": primary["tag"] if len(marks) == 1 else "",
                "components": marks,
                "assembly": len(marks) > 1,
                "room": primary["room"],
                "bounding_box": [round(primary["x"], 1), round(primary["y"], 1)],
            }
        )
    return openings


def _untagged(window_rects, hits, box, words, rooms, matrix) -> list[dict[str, Any]]:
    """An empty tag bubble is an opening with no mark. Other geometry is not a guess."""
    import fitz

    tagged = []
    for rect in window_rects:
        if any(rect.contains(fitz.Point(hit["ux"], hit["uy"])) for hit in hits):
            tagged.append(rect)
    if not tagged:
        return []
    found = []
    for rect in window_rects:
        if any(rect.contains(fitz.Point(hit["ux"], hit["uy"])) for hit in hits):
            continue
        if not any(abs(rect.width - known.width) <= 4 and abs(rect.height - known.height) <= 4 for known in tagged):
            continue
        center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2) * matrix
        if not box.contains(center):
            continue
        room_distance = min((center.x - room["x"]) ** 2 + (center.y - room["y"]) ** 2 for room in rooms) ** 0.5
        if room_distance > 250:
            continue
        word = {"x": center.x, "y": center.y}
        if _closer_to_elevation(word, words, rooms):
            continue
        if any(abs(item["bounding_box"][0] - rect.x0) < 8 and abs(item["bounding_box"][1] - rect.y0) < 8 for item in found):
            continue
        found.append(
            {
                "opening_class": "WINDOW",
                "bounding_box": [round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1)],
            }
        )
    return found


def _dedupe(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for tag in tags:
        if any(
            tag["tag"] == other["tag"] and abs(tag["x"] - other["x"]) < 18 and abs(tag["y"] - other["y"]) < 18
            for other in kept
        ):
            continue
        kept.append(tag)
    return kept
