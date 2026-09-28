"""Detect and parse Direct Shades standard Excel workbooks."""

from __future__ import annotations

from domain.direct_shades_workbooks import (
    ParsedWorkbook,
    WorkbookKind,
    merge_workbook_analyses,
    parse_workbook_xlsx,
)

PARSEABLE_EXTENSIONS = (".xlsx", ".xlsm")


def analyze_upload(filename: str, contents: bytes, source_format: str) -> dict | None:
    if source_format not in PARSEABLE_EXTENSIONS:
        return None
    return analyze_multiple([(filename, contents, source_format)])


def analyze_multiple(files: list[tuple[str, bytes, str]]) -> dict | None:
    """
    Parse every Excel upload and merge the recognised workbooks into one analysis.

    Unknown workbooks are dropped from the structured analysis (they still reach the
    agents as extracted text) unless the file name hints at one of the three Direct
    Shades workbook types. A single unreadable file never fails the whole upload —
    it is reported as a parser note instead.
    """
    workbooks: list[ParsedWorkbook] = []
    notes: list[str] = []

    for name, contents, ext in files:
        if ext not in PARSEABLE_EXTENSIONS:
            continue
        try:
            parsed = parse_workbook_xlsx(name, contents)
        except Exception as exc:  # corrupt / password-protected / unusual xlsx
            notes.append(f"{name}: could not parse as Excel ({exc}). Using text extraction only.")
            continue
        if parsed.kind != WorkbookKind.UNKNOWN or _name_hints_workbook(name):
            workbooks.append(parsed)

    if not workbooks:
        return None

    analysis = merge_workbook_analyses(workbooks)
    if notes:
        analysis["notes"] = [*analysis.get("notes", []), *notes]
    return analysis


def _name_hints_workbook(name: str) -> bool:
    lower = name.lower()
    return any(tok in lower for tok in ("matrix", "summary", "bid", "takeoff", "take-off"))
