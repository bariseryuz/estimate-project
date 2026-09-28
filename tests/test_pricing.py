import unittest

from domain.catalogue import load_catalogue
from domain.direct_shades_workbooks import merge_workbook_analyses, parse_workbook_xlsx
from domain.pricing import (
    _CATEGORY_KEYWORDS,
    _absorb_rounding,
    PricingPolicy,
    build_estimate_from_takeoff,
    catalogue_match_quality,
    category_for_system,
    looks_motorized,
    price_line,
    price_takeoff,
    select_catalogue_item,
    size_factor,
)
from domain.workbook_takeoff import build_takeoff_from_workbook
from tests.helpers_workbook import build_bid_summary_xlsx, build_window_matrix_xlsx

POLICY = PricingPolicy(labor_per_unit=50.0, overhead_pct=10.0, profit_pct=10.0, nominal_sqft=24.0)


class TestCatalogueSelection(unittest.TestCase):
    def test_category_mapping(self):
        self.assertEqual(category_for_system("Blackout roller"), "Blackout")
        self.assertEqual(category_for_system("Dual day/night"), "Dual")
        self.assertEqual(category_for_system("Honeycomb cellular"), "Cellular")
        self.assertEqual(category_for_system("Solar screen 5%"), "Solar / Screen")
        self.assertEqual(category_for_system(None), "Solar / Screen")

    def test_motorization_detection(self):
        self.assertTrue(looks_motorized("Blackout Motorized"))
        self.assertTrue(looks_motorized(None, "Somfy RTS"))
        self.assertTrue(looks_motorized(True))
        self.assertFalse(looks_motorized("Manual chain roller"))

    def test_selects_motorized_sku_for_motorized_line(self):
        item, reason = select_catalogue_item(
            system_type="Blackout", motorized=True, width_in=48, height_in=72
        )
        self.assertTrue(item["motorized"])
        self.assertEqual(item["category"], "Blackout")
        self.assertIn("motorized", reason)

    def test_flags_size_outside_catalogue_range(self):
        _, reason = select_catalogue_item(
            system_type="Cellular", motorized=False, width_in=300, height_in=300
        )
        self.assertIn("field verify", reason)


class TestCatalogueCoverage(unittest.TestCase):
    """
    Every category the mapper can return must be buyable.

    A category with no SKU behind it does not raise — it quietly reprices the line as
    something else, which is how a Dual shade once went out priced as a solar roller.
    """

    def test_active_catalogue_covers_every_mapped_category(self):
        available = {str(i.get("category") or "") for i in load_catalogue()}
        mapped = {category_for_system(kw) for _, kws in _CATEGORY_KEYWORDS for kw in kws}
        self.assertEqual(mapped - available, set())

    def test_every_category_has_a_manual_and_a_motorized_sku(self):
        combos = {
            (str(i.get("category") or ""), bool(i.get("motorized"))) for i in load_catalogue()
        }
        for category in {c for c, _ in combos}:
            self.assertIn((category, False), combos, f"{category} has no manual SKU")
            self.assertIn((category, True), combos, f"{category} has no motorized SKU")

    def test_dual_shade_prices_as_a_dual_sku(self):
        item, reason = select_catalogue_item(
            system_type="Dual", motorized=False, width_in=48, height_in=72
        )
        self.assertEqual(item["category"], "Dual")
        self.assertEqual(catalogue_match_quality(item, system_type="Dual", motorized=False), "exact")
        self.assertIn("Dual", reason)

    def test_match_quality_travels_with_the_line_estimate(self):
        wb = merge_workbook_analyses(
            [parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx())]
        )
        estimate = build_estimate_from_takeoff(
            build_takeoff_from_workbook(wb), workbook=wb, policy=POLICY
        )
        for est in estimate["estimates"]:
            self.assertIn(est.get("catalogueMatch"), ("exact", "substituted"))
            self.assertTrue(est.get("catalogueReason"))

    def test_substitution_is_stated_not_hidden(self):
        # A category with no SKU must produce a reason that admits the swap.
        item, reason = select_catalogue_item(
            system_type="Roman Fabric Drapery", motorized=False, width_in=48, height_in=72
        )
        quality = catalogue_match_quality(
            item, system_type="Roman Fabric Drapery", motorized=False
        )
        if quality == "substituted":
            self.assertIn("substituted", reason)
            self.assertIn("confirm the product", reason)


class TestLinePricing(unittest.TestCase):
    def test_nominal_size_has_no_size_factor(self):
        self.assertEqual(size_factor(24.0, POLICY), 1.0)
        self.assertEqual(size_factor(12.0, POLICY), 1.0)
        self.assertEqual(size_factor(48.0, POLICY), 2.0)
        self.assertEqual(size_factor(None, POLICY), 1.0)

    def test_formula_shows_every_factor(self):
        priced = price_line(
            {"quantity": 2, "widthInches": 48, "heightInches": 72, "systemType": "Solar roller"},
            POLICY,
        )
        # 48 × 72 = 3456 sq in = 24 sq ft → factor 1.0
        self.assertEqual(priced["sizeFactor"], 1.0)
        self.assertEqual(priced["unitLabor"], 50.0)
        self.assertEqual(priced["unitPrice"], priced["unitMaterial"] + 50.0)
        self.assertEqual(priced["extendedPrice"], round(priced["unitPrice"] * 2, 2))
        self.assertIn("2 EA ×", priced["pricingFormula"])
        self.assertIn("labor", priced["pricingFormula"])
        self.assertEqual(priced["priceSource"], "catalogue")

    def test_large_opening_scales_material_only(self):
        small = price_line({"quantity": 1, "widthInches": 48, "heightInches": 72}, POLICY)
        large = price_line({"quantity": 1, "widthInches": 96, "heightInches": 72}, POLICY)
        self.assertEqual(large["sizeFactor"], 2.0)
        self.assertEqual(large["unitMaterial"], round(small["unitMaterial"] * 2, 2))
        self.assertEqual(large["unitLabor"], small["unitLabor"])

    def test_missing_dimensions_still_price(self):
        priced = price_line({"quantity": 1, "systemType": "Roller"}, POLICY)
        self.assertGreater(priced["extendedPrice"], 0)
        self.assertEqual(priced["sizeFactor"], 1.0)


class TestProjectRollup(unittest.TestCase):
    def test_catalogue_rollup_adds_overhead_and_profit(self):
        result = price_takeoff(
            [{"quantity": 1, "widthInches": 48, "heightInches": 72}] * 2,
            policy=POLICY,
        )
        self.assertEqual(result["priceSource"], "catalogue")
        self.assertEqual(result["overhead"], round(result["subtotal"] * 0.10, 2))
        self.assertEqual(
            result["profit"], round((result["subtotal"] + result["overhead"]) * 0.10, 2)
        )
        self.assertEqual(
            result["total"],
            round(result["subtotal"] + result["overhead"] + result["profit"], 2),
        )
        labels = [s["label"] for s in result["steps"]]
        self.assertIn("Catalogue basis", labels)
        self.assertIn("Project total", labels)

    def test_bid_summary_anchors_the_total(self):
        wb = merge_workbook_analyses(
            [
                parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx()),
                parse_workbook_xlsx("301 Bid Summary.xlsx", build_bid_summary_xlsx()),
            ]
        )
        result = price_takeoff(
            [{"quantity": 1, "widthInches": 48, "heightInches": 72}] * 5,
            workbook=wb,
            policy=POLICY,
        )
        self.assertEqual(result["priceSource"], "bid_summary")
        self.assertEqual(result["total"], 3000.0)
        self.assertEqual(result["overhead"], 0.0)
        self.assertEqual(result["profit"], 0.0)
        self.assertIsNotNone(result["calibrationFactor"])
        # Calibrated line prices add back up to the bid total (within rounding).
        line_sum = sum(l["extendedPrice"] for l in result["lines"])
        self.assertAlmostEqual(line_sum, 3000.0, delta=1.0)
        for line in result["lines"]:
            self.assertIn("Bid Summary calibration", line["pricingFormula"])


class TestScheduleFootsToTotal(unittest.TestCase):
    def test_calibrated_lines_sum_to_the_bid_total_to_the_cent(self):
        wb = merge_workbook_analyses(
            [
                parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx()),
                parse_workbook_xlsx("301 Bid Summary.xlsx", build_bid_summary_xlsx()),
            ]
        )
        estimate = build_estimate_from_takeoff(
            build_takeoff_from_workbook(wb), workbook=wb, policy=POLICY
        )
        line_sum = round(sum(e["totalCost"] for e in estimate["estimates"]), 2)
        self.assertEqual(line_sum, estimate["totalEstimate"])

    def test_rounding_adjustment_is_disclosed_on_the_line_it_lands_on(self):
        lines = [
            {"extendedPrice": 100.00, "pricingFormula": "a"},
            {"extendedPrice": 50.00, "pricingFormula": "b"},
        ]
        _absorb_rounding(lines, 150.07)
        self.assertEqual(round(sum(l["extendedPrice"] for l in lines), 2), 150.07)
        self.assertEqual(lines[0]["extendedPrice"], 100.07)
        self.assertIn("rounding", lines[0]["pricingFormula"])
        self.assertEqual(lines[1]["pricingFormula"], "b")


class TestEstimateFromTakeoff(unittest.TestCase):
    def setUp(self):
        self.wb = merge_workbook_analyses(
            [parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx())]
        )
        self.takeoff = build_takeoff_from_workbook(self.wb)

    def test_one_estimate_per_takeoff_line(self):
        estimate = build_estimate_from_takeoff(self.takeoff, self.wb, POLICY)
        self.assertEqual(len(estimate["estimates"]), len(self.takeoff["takeoffItems"]))
        for line in estimate["estimates"]:
            self.assertIsNotNone(line["totalCost"])
            self.assertTrue(line["calculationFormula"])
            self.assertTrue(line["catalogueSku"])

    def test_count_comes_from_the_matrix_not_the_pricing(self):
        estimate = build_estimate_from_takeoff(self.takeoff, self.wb, POLICY)
        self.assertEqual(estimate["shadeSummary"]["totalShades"], 5)
        self.assertEqual(estimate["currency"], "USD")
        self.assertTrue(estimate["pricingSteps"])
        self.assertTrue(estimate["executiveSummary"])

    def test_totals_are_internally_consistent(self):
        estimate = build_estimate_from_takeoff(self.takeoff, self.wb, POLICY)
        line_sum = round(sum(l["totalCost"] for l in estimate["estimates"]), 2)
        self.assertAlmostEqual(estimate["subtotal"], line_sum, delta=0.05)
        self.assertAlmostEqual(
            estimate["totalEstimate"],
            round(estimate["subtotal"] + estimate["overhead"] + estimate["profit"], 2),
            delta=0.05,
        )


if __name__ == "__main__":
    unittest.main()
