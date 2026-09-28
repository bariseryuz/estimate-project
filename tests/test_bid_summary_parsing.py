import unittest

from domain.direct_shades_workbooks import (
    WorkbookKind,
    merge_workbook_analyses,
    parse_workbook_xlsx,
)
from tests.helpers_workbook import (
    build_bid_summary_xlsx,
    build_material_summary_xlsx,
    build_window_matrix_xlsx,
)


class TestBidSummaryParsing(unittest.TestCase):
    def test_detects_bid_summary_by_file_name(self):
        parsed = parse_workbook_xlsx("301 Bid Summary.xlsx", build_bid_summary_xlsx())
        self.assertEqual(parsed.kind, WorkbookKind.BID_SUMMARY)

    def test_parses_tab_totals_and_lines(self):
        parsed = parse_workbook_xlsx("301 Bid Summary.xlsx", build_bid_summary_xlsx())
        self.assertEqual(len(parsed.bid_tabs), 2)
        self.assertEqual(len(parsed.bid_lines), 3)

        units = next(t for t in parsed.bid_tabs if t["sheet"] == "UNITS")
        self.assertEqual(units["total"], 2000.0)          # 4×300 + 2×400
        self.assertEqual(units["quantity"], 6)
        self.assertEqual(units["unitRate"], round(2000.0 / 6, 2))
        self.assertEqual(units["totalFrom"], "TOTAL row")

        self.assertEqual(parsed.bid_grand_total, 3000.0)  # + 2×500 common area

    def test_falls_back_to_summing_lines_without_total_row(self):
        parsed = parse_workbook_xlsx(
            "301 Bid Summary.xlsx",
            build_bid_summary_xlsx(include_total_row=False),
        )
        units = next(t for t in parsed.bid_tabs if t["sheet"] == "UNITS")
        self.assertEqual(units["total"], 2000.0)
        self.assertEqual(units["totalFrom"], "sum of line amounts")

    def test_line_carries_source_location(self):
        parsed = parse_workbook_xlsx("301 Bid Summary.xlsx", build_bid_summary_xlsx())
        line = parsed.bid_lines[0]
        self.assertIn("301 Bid Summary.xlsx", line["sourceLocation"])
        self.assertIn("sheet 'UNITS'", line["sourceLocation"])
        self.assertEqual(line["unitPrice"], 300.0)
        self.assertEqual(line["amount"], 1200.0)


class TestReferencePricingMerge(unittest.TestCase):
    def setUp(self):
        self.analysis = merge_workbook_analyses(
            [
                parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx()),
                parse_workbook_xlsx("301 Bid Summary.xlsx", build_bid_summary_xlsx()),
                parse_workbook_xlsx("301 Material Summary.xlsx", build_material_summary_xlsx()),
            ]
        )

    def test_unit_rate_uses_matrix_total(self):
        reference = self.analysis["referencePricing"]
        self.assertEqual(reference["grandTotal"], 3000.0)
        # 3000 / 5 shades from the Window Matrix TOTAL row
        self.assertEqual(reference["unitRate"], 600.0)
        self.assertIn("WINDOW MATRIX TOTAL row", reference["unitRateBasis"])

    def test_bid_dollars_do_not_change_the_count(self):
        self.assertEqual(self.analysis["authoritativeTotalShades"], 5)

    def test_material_lines_are_collected(self):
        lines = self.analysis["materialLines"]
        self.assertTrue(lines)
        self.assertTrue(any("SheerWeave" in (l.get("detail") or "") for l in lines))

    def test_prompt_block_mentions_reference_pricing(self):
        self.assertIn("BID SUMMARY GRAND TOTAL", self.analysis["promptBlock"])

    def test_no_reference_pricing_without_bid_workbook(self):
        analysis = merge_workbook_analyses(
            [parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx())]
        )
        self.assertEqual(analysis["referencePricing"], {})


if __name__ == "__main__":
    unittest.main()
