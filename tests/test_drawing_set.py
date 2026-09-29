"""Architectural drawing-set counts, without calling a model."""

import unittest

from agents.estimation_agent import _unpriced_drawing_estimate
from agents.validation import _drawing_set_validation

from domain.drawing_set import (
    apply_printed_unit_types,
    classify_sheet,
    combine_counts,
    filenames_needing_text,
    merge_unit_rows,
    norm_type,
    type_from_filename,
    unit_types_from_floor_plans,
)


class TestDrawingSet(unittest.TestCase):
    def test_sheet_roles_follow_the_title(self):
        self.assertEqual(classify_sheet("A-0.04.1-UPDATED-UNIT-MATRIX.pdf", ""), "unit_matrix")
        self.assertEqual(classify_sheet("A-4.00-UNIT-A-1-a-Rev.Permit.pdf", ""), "unit_plan")
        self.assertEqual(classify_sheet("A-6.02-STOREFRONT-WINDOW-SCHEDULE.pdf", "WINDOW SCHEDULE"), "window_schedule")
        self.assertEqual(classify_sheet("A-1.01-GROUND-FLOOR-AND-SECOND-FLOOR.pdf", ""), "floor_plan")
        self.assertEqual(classify_sheet("A-1.22-PARTIAL-GROUND-FLOOR-RCP-1.pdf", ""), "other")

    def test_unit_code_from_the_file_name(self):
        self.assertEqual(norm_type(type_from_filename("A-4.00-UNIT-A-1-a-Rev.Permit.pdf")), "a1a")
        self.assertEqual(norm_type(type_from_filename("A-4.09A-UNIT-B-1-S-Rev.Permit.pdf")), "b1s")
        self.assertEqual(norm_type(type_from_filename("A-4.09-UNIT-B-1-Rev.GMP.pdf")), "b1")

    def test_overlapping_tiles_count_a_unit_once(self):
        merged = merge_unit_rows(
            [
                {
                    "units": [
                        {"unit": "201", "type": "B-4", "qty": 1},
                        {"unit": "LW-102", "type": "NOT USED", "qty": 1},
                    ],
                    "printedTotals": [15],
                },
                {
                    "units": [
                        {"unit": "201", "type": "B-4", "qty": 1},
                        {"unit": "202", "type": "A-5", "qty": 1},
                    ],
                    "printedTotals": [144],
                },
            ]
        )
        self.assertEqual(merged["transcribedUnits"], 2)
        self.assertEqual(merged["printedTotal"], 144)
        types = {row["type"]: row["count"] for row in merged["types"]}
        self.assertEqual(types, {"B-4": 1, "A-5": 1})
        rejected = merge_unit_rows(
            [{"units": [{"unit": "401", "type": "3BR+2BATH", "qty": 1}, {"unit": "99999", "type": "A-1", "qty": 1}]}]
        )
        self.assertEqual(rejected["transcribedUnits"], 0)

    def test_shade_count_is_units_times_openings_on_that_plan(self):
        combined = combine_counts(
            [{"type": "A-1a", "count": 10}, {"type": "B-1", "count": 4}, {"type": "C-9", "count": 2}],
            [
                {"file": "A-4.00-UNIT-A-1-a-Rev.Permit.pdf", "unitType": "A-1a", "shadeOpenings": 3},
                {"file": "A-4.09A-UNIT-B-1-S-Rev.Permit.pdf", "unitType": "B-1S", "shadeOpenings": 9},
                {"file": "A-4.09-UNIT-B-1-Rev.GMP.pdf", "unitType": "B-1", "shadeOpenings": 4},
            ],
        )
        self.assertEqual(combined["total"], 10 * 3 + 4 * 4)
        self.assertEqual(combined["unmatched"], ["C-9"])
        b1 = next(line for line in combined["lines"] if line.get("unitType") == "B-1")
        self.assertEqual(b1["quantity"], 16)
        self.assertEqual(b1["apartmentCount"], 4)
        self.assertNotEqual(b1["windowTag"], "B-1")
        self.assertIn("UNIT-B-1-Rev", b1["sourceLocation"])

    def test_apartment_count_is_not_used_as_the_shade_count(self):
        combined = combine_counts(
            [{"type": "A-1", "count": 10}, {"type": "B-1", "count": 4}],
            [
                {
                    "unitType": "A-1",
                    "shadeOpenings": 1,
                    "tags": [{"tag": "U2", "room": "LIVING"}],
                    "file": "UNIT-A-1.pdf",
                },
                {
                    "unitType": "B-1",
                    "shadeOpenings": 1,
                    "tags": [{"tag": "U2", "room": "BEDROOM"}],
                    "file": "UNIT-B-1.pdf",
                },
            ],
        )
        self.assertEqual(combined["total"], 14)
        self.assertEqual(len(combined["lines"]), 1)
        line = combined["lines"][0]
        self.assertEqual(line["windowTag"], "U2")
        self.assertEqual(line["quantity"], 14)
        self.assertNotEqual(line["quantity"], 10)
        none = combine_counts(
            [{"type": "A-1", "count": 10}],
            [{"unitType": "A-1", "shadeOpenings": 0, "file": "UNIT-A-1.pdf"}],
        )
        self.assertEqual(none["total"], 0)
        self.assertEqual(none["lines"], [])

    def test_a_tied_matrix_reading_picks_one_type_every_time(self):
        merged = merge_unit_rows(
            [
                {"units": [{"unit": "301", "type": "B-4", "qty": 1}], "printedTotals": [2]},
                {"units": [{"unit": "301", "type": "A-5", "qty": 1}], "printedTotals": []},
            ]
        )
        self.assertEqual(merged["units"][0]["type"], "A-5")

    def test_floor_plan_text_replaces_a_wrong_matrix_type(self):
        typed = unit_types_from_floor_plans(
            [
                {
                    "file": "A-1.01-GROUND-FLOOR.pdf",
                    "role": "floor_plan",
                    "text": "UNIT 201\nB-4\n850 SF\nUNIT 202\nA-5\n900 SF",
                },
                {
                    "file": "A-1.10-PARTIAL-THIRD-FLOOR.pdf",
                    "role": "floor_plan",
                    "text": "UNIT 601\nC-1\n1400 SF\nUNIT 201\nA-9\n850 SF",
                },
                {
                    "file": "A-0.12-F.A.R.-GROUND-FLOOR.pdf",
                    "role": "floor_plan",
                    "text": "UNIT 201\nB-9\n850 SF",
                },
            ]
        )
        self.assertEqual(typed["201"], "B-4")
        self.assertEqual(typed["601"], "C-1")
        merged = apply_printed_unit_types(
            {
                "units": [
                    {"unit": "201", "type": "A-1", "qty": 1},
                    {"unit": "601", "type": "C-1", "qty": 1},
                ],
                "types": [],
                "transcribedUnits": 2,
                "printedTotal": 2,
            },
            typed,
        )
        types = {row["type"]: row["count"] for row in merged["types"]}
        self.assertEqual(types, {"B-4": 1, "C-1": 1})
        self.assertEqual(merged["typesCorrected"], 1)
        self.assertEqual(merged["typesFromFloorPlans"], 2)

    def test_a_drawing_set_is_not_given_a_made_up_price(self):
        estimate = _unpriced_drawing_estimate(
            {
                "totalShadeCount": 10,
                "summary": "10 window shades.",
                "takeoffItems": [{"windowTag": "A-1", "quantity": 10, "item": "Shades for unit A-1"}],
            }
        )
        self.assertIsNone(estimate["totalEstimate"])
        self.assertEqual(estimate["priceSource"], "not_on_drawings")
        self.assertEqual(estimate["estimates"][0]["quantity"], 10)
        self.assertIsNone(estimate["estimates"][0]["totalCost"])

    def test_named_plans_skip_text_on_notes_and_elevations(self):
        names = [
            "A-0.04.1-UPDATED-UNIT-MATRIX-Rev.Permit.pdf",
            "A-4.00-UNIT-A-1-a-Rev.Permit.pdf",
            "A-1.01-GROUND-FLOOR-AND-SECOND-FLOOR.pdf",
            "A-2.01-BUILDING-ELEVATION.pdf",
            "A-0.02-NOTES-AND-ABBREVIATIONS.pdf",
        ]
        self.assertEqual(
            filenames_needing_text(names),
            ["A-1.01-GROUND-FLOOR-AND-SECOND-FLOOR.pdf"],
        )

    def test_a_missing_matrix_name_still_reads_the_other_sheets(self):
        names = [
            "A-4.00-UNIT-A-1-a-Rev.Permit.pdf",
            "A-2.01-BUILDING-ELEVATION.pdf",
        ]
        self.assertIn("A-2.01-BUILDING-ELEVATION.pdf", filenames_needing_text(names))

    def test_drawing_validation_does_not_invent_a_price_check(self):
        result = _drawing_set_validation(
            {
                "dataSource": "drawing_set",
                "matrixUnitsPrinted": 144,
                "matrixUnitsRead": 144,
                "summary": "470 window shades.",
            },
            {"totalEstimate": None},
        )
        self.assertEqual(result["recommendation"], "Approved")
        self.assertFalse(result["readyToSendOffer"])
        self.assertEqual(result["crossChecks"][0]["match"], True)


if __name__ == "__main__":
    unittest.main()
