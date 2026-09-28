import io
import unittest

from openpyxl import Workbook

from domain.direct_shades_workbooks import merge_workbook_analyses, parse_workbook_xlsx
from domain.workbook_takeoff import (
    apply_workbook_precision,
    build_takeoff_from_workbook,
    workbook_count_reconciliation,
)
from pipeline.quantity_schedule import build_quantity_schedule
from tests.helpers_workbook import build_multi_section_matrix_xlsx


class TestMultiSectionBlindQty(unittest.TestCase):
    """A Blind QTY sheet that stacks LIVINGS and BEDROOMS with sub-total rows."""

    def setUp(self):
        self.parsed = parse_workbook_xlsx(
            "301 Window Matrix.xlsx", build_multi_section_matrix_xlsx()
        )
        self.analysis = merge_workbook_analyses([self.parsed])

    def test_header_found_below_metadata_block(self):
        self.assertEqual(len(self.parsed.blind_qty_lines), 4)

    def test_subtotal_and_total_rows_are_not_counted_as_lines(self):
        tags = [l["windowTag"] for l in self.parsed.blind_qty_lines]
        self.assertNotIn("SUBTOTAL", tags)
        self.assertNotIn("TOTAL", tags)

    def test_repeated_tag_in_two_areas_is_kept_twice(self):
        a101 = [l for l in self.parsed.blind_qty_lines if l["windowTag"] == "A-101"]
        self.assertEqual(len(a101), 2)
        self.assertEqual({l["section"] for l in a101}, {"LIVINGS", "BEDROOMS"})

    def test_section_labels_are_tracked_down_the_sheet(self):
        by_tag = {l["windowTag"]: l for l in self.parsed.blind_qty_lines}
        self.assertEqual(by_tag["A-102"]["section"], "LIVINGS")
        self.assertEqual(by_tag["A-201"]["section"], "BEDROOMS")

    def test_explicit_inch_headers_resolve_dimensions(self):
        line = next(l for l in self.parsed.blind_qty_lines if l["windowTag"] == "A-102")
        self.assertEqual(line["widthInches"], 36)
        self.assertEqual(line["heightInches"], 60)

    def test_every_line_cites_its_row(self):
        for line in self.parsed.blind_qty_lines:
            self.assertIn("row", line["sourceLocation"])
            self.assertIsInstance(line["row"], int)


class TestDuplicateTagsSurviveTheMerge(unittest.TestCase):
    def setUp(self):
        self.analysis = merge_workbook_analyses(
            [parse_workbook_xlsx("301 Window Matrix.xlsx", build_multi_section_matrix_xlsx())]
        )

    def test_takeoff_keeps_both_instances_of_a_repeated_tag(self):
        takeoff = build_takeoff_from_workbook(self.analysis)
        a101 = [i for i in takeoff["takeoffItems"] if i["windowTag"] == "A-101"]
        self.assertEqual(len(a101), 2)
        self.assertEqual(sum(i["quantity"] for i in a101), 4)

    def test_quantity_schedule_lists_both_instances(self):
        takeoff = build_takeoff_from_workbook(self.analysis)
        qs = build_quantity_schedule(takeoff, {}, self.analysis, {})
        self.assertEqual(len(qs["lines"]), 4)
        self.assertEqual(qs["totals"]["unitQuantity"], 6)

    def test_llm_line_for_a_repeated_tag_does_not_double_count(self):
        merged = apply_workbook_precision(
            self.analysis,
            [{"windowTag": "A-101", "quantity": 99, "dataSource": "ai_takeoff"}],
        )
        a101 = [i for i in merged if i["windowTag"] == "A-101"]
        self.assertEqual(len(a101), 2)
        self.assertEqual(sum(i["quantity"] for i in a101), 4)

    def test_llm_line_for_an_unknown_tag_is_preserved(self):
        merged = apply_workbook_precision(
            self.analysis,
            [{"windowTag": "Z-999", "quantity": 3}],
        )
        z = next(i for i in merged if i["windowTag"] == "Z-999")
        self.assertEqual(z["quantity"], 3)
        self.assertEqual(z["dataSource"], "ai_takeoff")

    def test_reconciliation_exposes_the_gap_between_counts(self):
        counts = workbook_count_reconciliation(self.analysis)
        self.assertEqual(counts["matrixTotal"], 6)
        self.assertEqual(counts["blindQtySum"], 6)
        self.assertEqual(counts["markingSum"], 6)


class TestOffMatrixScope(unittest.TestCase):
    """A quantity sheet for an area the matrix never lists is real extra scope."""

    def test_sheet_with_unknown_tags_and_area_is_added_once(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "WINDOW MATRIX"
        ws.append(["Project Name", "Madeira Tower"])
        ws.append([])
        ws.append(["WINDOW MARKINGS", "SHADES PER OPENING", "TOTAL SHADES", "TOTAL WINDOWS"])
        ws.append(["LIVING AREAS", "", "", ""])
        ws.append(["A-101", 1, 4, 4])
        ws.append(["TOTAL", "", 4, 4])

        lobby = wb.create_sheet("Blind QTY LOBBY")
        lobby.append(["Area", "LOBBY & CLUB ROOM"])
        lobby.append(["Window Tag", "QTY", "WIDTH (IN)", "HEIGHT (IN)", "SYSTEM", "ROOM"])
        lobby.append(["L-01", 2, 60, 96, "Solar Roller", "Lobby"])
        lobby.append(["C-01", 1, 48, 96, "Solar Roller", "Club"])

        buf = io.BytesIO()
        wb.save(buf)
        analysis = merge_workbook_analyses(
            [parse_workbook_xlsx("301 Window Matrix.xlsx", buf.getvalue())]
        )

        self.assertEqual(analysis["authoritativeTotalShades"], 4)
        self.assertEqual(analysis.get("additionalShadeCount"), 3)
        self.assertEqual(analysis.get("projectShadeCount"), 7)

        takeoff = build_takeoff_from_workbook(analysis)
        self.assertEqual(takeoff["totalShadeCount"], 7)

    def test_reused_tags_in_another_area_are_still_added(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "WINDOW MATRIX"
        ws.append(["WINDOW MARKINGS", "SHADES PER OPENING", "TOTAL SHADES", "TOTAL WINDOWS"])
        ws.append(["LIVING AREAS"])
        ws.append(["A", 1, 4, 4])
        ws.append(["TOTAL", "", 4, 4])

        living = wb.create_sheet("Blind QTY LIVINGS")
        living.append(["Area", "LIVING AREAS"])
        living.append(["Window Tag", "QTY", "WIDTH (IN)", "HEIGHT (IN)"])
        living.append(["A", 4, 41, 96])
        living.append(["TOTAL SHADES", 4])

        commons = wb.create_sheet("Blind QTY COMMONS")
        commons.append(["Area", "COMMON AREAS"])
        commons.append(["Window Tag", "QTY", "WIDTH (IN)", "HEIGHT (IN)"])
        commons.append(["A", 2, 36, 72])
        commons.append(["TOTAL SHADES", 2])

        buf = io.BytesIO()
        wb.save(buf)
        analysis = merge_workbook_analyses(
            [parse_workbook_xlsx("Window Matrix.xlsx", buf.getvalue())]
        )
        self.assertEqual(analysis["additionalShadeCount"], 2)
        self.assertEqual(analysis["projectShadeCount"], 6)


if __name__ == "__main__":
    unittest.main()
