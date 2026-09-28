"""Architectural drawing-set counts, without calling a model."""

import unittest

from domain.drawing_set import (
    classify_sheet,
    combine_counts,
    merge_unit_rows,
    norm_type,
    type_from_filename,
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
        b1 = next(line for line in combined["lines"] if line["windowTag"] == "B-1")
        self.assertEqual(b1["quantity"], 16)
        self.assertIn("UNIT-B-1-Rev", b1["sourceLocation"])


if __name__ == "__main__":
    unittest.main()
