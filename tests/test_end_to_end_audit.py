"""
Full Excel-only run with no LLM involved.

Walks the same objects the API returns — take-off, estimate, quantity schedule,
estimation audit, project summary, client offer — so a regression anywhere in the
chain shows up as a failing assertion about a number a user would actually read.
"""

import asyncio
import unittest

from domain.direct_shades_workbooks import merge_workbook_analyses, parse_workbook_xlsx
from domain.pricing import PricingPolicy, build_estimate_from_takeoff
from domain.workbook_takeoff import should_use_workbook_fast_path
from agents.takeoff_engine import run_takeoff_engine
from pipeline.client_offer import build_client_offer_package
from pipeline.estimation_audit import build_estimation_audit
from pipeline.project_summary import build_project_summary
from pipeline.quantity_schedule import build_quantity_schedule
from pipeline.validation_adjust import adjust_validation_for_workbook
from tests.helpers_workbook import (
    build_bid_summary_xlsx,
    build_material_summary_xlsx,
    build_multi_section_matrix_xlsx,
)

POLICY = PricingPolicy(labor_per_unit=60.0, overhead_pct=10.0, profit_pct=5.0, nominal_sqft=24.0)

FILES = [
    ("301 Madeira Window Matrix.xlsx", build_multi_section_matrix_xlsx),
    ("301 Madeira Bid Summary.xlsx", build_bid_summary_xlsx),
    ("301 Madeira Material Summary.xlsx", build_material_summary_xlsx),
]


class TestExcelOnlyRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workbook = merge_workbook_analyses(
            [parse_workbook_xlsx(name, builder()) for name, builder in FILES]
        )
        cls.takeoff = asyncio.get_event_loop().run_until_complete(
            run_takeoff_engine(
                {"embedded_chunks": [], "workbook": cls.workbook},
                workbook_analysis=cls.workbook,
            )
        )
        cls.estimation = build_estimate_from_takeoff(cls.takeoff, cls.workbook, POLICY)
        cls.schedule = build_quantity_schedule(
            cls.takeoff, cls.estimation, cls.workbook, {}
        )
        cls.audit = build_estimation_audit(
            source_meta={"fileNames": [name for name, _ in FILES]},
            workbook=cls.workbook,
            takeoff=cls.takeoff,
            estimation=cls.estimation,
            context={"projectName": "Madeira Tower"},
            quantity_schedule=cls.schedule,
        )
        cls.validation = adjust_validation_for_workbook(
            {"confidenceScore": 60, "recommendation": "Needs Review"},
            cls.workbook,
            cls.takeoff,
            estimation=cls.estimation,
        )
        cls.summary = build_project_summary(
            source_meta={"fileNames": [name for name, _ in FILES]},
            workbook=cls.workbook,
            takeoff=cls.takeoff,
            estimation=cls.estimation,
            validation=cls.validation,
            quantity_schedule=cls.schedule,
            workbook_fast_path=True,
        )

    # ── Routing ───────────────────────────────────────────────────────────────

    def test_excel_only_upload_takes_the_fast_path(self):
        self.assertTrue(should_use_workbook_fast_path(self.workbook))

    def test_drawings_force_the_full_route(self):
        self.assertFalse(should_use_workbook_fast_path(self.workbook, has_pdf=True))
        self.assertFalse(should_use_workbook_fast_path(self.workbook, has_image=True))

    def test_takeoff_ran_without_an_llm(self):
        self.assertEqual(self.takeoff["dataSource"], "workbook_excel")
        self.assertEqual(self.takeoff["workbookAuthority"], "WINDOW MATRIX TOTAL row")
        self.assertIn("matrix total", self.takeoff["countMethodology"].lower())

    def test_matrix_covered_sheets_are_not_added_on_top(self):
        # The Blind QTY sheet is labelled "UNITS" while the matrix sections are LIVING
        # AREAS / BEDROOMS. Its tags are on the matrix, so it is detail, not extra scope.
        self.assertEqual(self.workbook.get("additionalShadeCount") or 0, 0)
        self.assertEqual(self.takeoff["totalShadeCount"], self.workbook["authoritativeTotalShades"])

    # ── Count ─────────────────────────────────────────────────────────────────

    def test_count_follows_the_matrix_total_row(self):
        self.assertEqual(self.takeoff["totalShadeCount"], 6)
        self.assertEqual(self.summary["matrixTotal"], 6)
        self.assertEqual(self.summary["matchStatus"], "match")

    def test_every_schedule_line_cites_a_sheet_and_row(self):
        self.assertTrue(self.schedule["lines"])
        for line in self.schedule["lines"]:
            self.assertTrue(line["sourceLocation"], line)
            self.assertTrue(line["countBasis"], line)

    # ── Price ─────────────────────────────────────────────────────────────────

    def test_total_is_anchored_to_the_bid_summary(self):
        self.assertEqual(self.estimation["priceSource"], "bid_summary")
        self.assertEqual(self.estimation["totalEstimate"], 3000.0)
        self.assertEqual(self.summary["priceVariance"]["amount"], 0.0)

    def test_every_line_has_a_price_and_a_formula(self):
        for line in self.schedule["lines"]:
            self.assertIsNotNone(line["extendedPrice"], line["windowTag"])
            self.assertTrue(line["pricingFormula"], line["windowTag"])

    def test_line_prices_add_up_to_the_project_total(self):
        line_sum = sum(l["extendedPrice"] for l in self.schedule["lines"])
        self.assertAlmostEqual(line_sum, self.estimation["totalEstimate"], delta=1.0)

    # ── Audit ─────────────────────────────────────────────────────────────────

    def test_audit_names_every_workbook_and_its_role(self):
        roles = {r["role"] for r in self.audit["documentReferences"]}
        self.assertIn("Count authority", roles)
        self.assertIn("Blind QTY take-off", roles)
        self.assertIn("Bid Summary", roles)

    def test_audit_count_steps_show_all_three_counts(self):
        labels = {s["label"] for s in self.audit["countCalculation"]["steps"]}
        self.assertIn("Project total shades", labels)
        self.assertIn("Matrix markings check", labels)
        self.assertIn("Blind QTY rows check", labels)

    def test_audit_price_steps_show_the_bid_calibration(self):
        labels = [s["label"] for s in self.audit["priceCalculation"]["steps"]]
        self.assertIn("Catalogue basis", labels)
        self.assertIn("Bid Summary calibration", labels)
        self.assertIn("Bid Summary cross-check", labels)
        self.assertEqual(self.audit["priceCalculation"]["priceSource"], "bid_summary")

    def test_audit_has_one_row_per_schedule_line(self):
        self.assertEqual(len(self.audit["lineCalculations"]), len(self.schedule["lines"]))
        for row in self.audit["lineCalculations"]:
            self.assertTrue(row["countFormula"])
            self.assertTrue(row["reference"])

    def test_audit_summary_states_both_authorities(self):
        self.assertIn("WINDOW MATRIX TOTAL", self.audit["summary"])
        self.assertIn("Bid Summary", self.audit["summary"])

    # ── Client-facing output ──────────────────────────────────────────────────

    def test_offer_package_matches_the_audited_numbers(self):
        offer = build_client_offer_package(
            self.estimation,
            self.takeoff,
            self.workbook,
            self.validation,
            {},
            quantity_schedule=self.schedule,
        )
        self.assertEqual(offer["totalShades"], 6)
        self.assertEqual(offer["totalPrice"], 3000.0)
        self.assertEqual(offer["pricePerShade"], 500.0)
        self.assertIn("Madeira Tower", offer["headline"])
        self.assertIn("Subject:", offer["emailDraft"])

    def test_validation_cross_checks_all_pass_on_a_clean_set(self):
        matrix_check = next(
            c for c in self.validation["crossChecks"]
            if c["item"] == "Total shade count vs WINDOW MATRIX TOTAL row"
        )
        self.assertTrue(matrix_check["match"])
        self.assertGreaterEqual(self.validation["confidenceScore"], 82)


if __name__ == "__main__":
    unittest.main()
