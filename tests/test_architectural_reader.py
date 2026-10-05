"""Architectural plan-reader contract. Counts stay deterministic and project-specific."""

import unittest
from pathlib import Path

import fitz

from domain.architectural_reader import (
    copy_suffix,
    expand_openings,
    formatting_key,
    lock_drawing_set,
    reconcile_floor_totals,
    revision_from_name,
)
from domain.drawing_set import combine_counts
from domain.plan_openings import count_plan_openings
from domain.window_sizes import read_schedule_dictionary

_ARCH = Path("/Users/bariseryuz/Desktop/Architectural")


def _page(rows, headers, title="WINDOW SCHEDULE"):
    doc = fitz.open()
    page = doc.new_page(width=900, height=500)
    page.insert_text(fitz.Point(40, 40), title)
    for x, text in headers:
        page.insert_text(fitz.Point(x, 70), text)
    for index, row in enumerate(rows):
        for x, text in row:
            page.insert_text(fitz.Point(x, 110 + index * 24), text)
    data = doc.tobytes()
    doc.close()
    return data


class TestArchitecturalReader(unittest.TestCase):
    def test_formatting_alias_keeps_real_variants(self):
        self.assertEqual(formatting_key("A-1A"), formatting_key("A-1-a"))
        self.assertNotEqual(formatting_key("A-1a"), formatting_key("A-1b"))
        self.assertNotEqual(formatting_key("A-1a"), formatting_key("A-1"))
        self.assertNotEqual(formatting_key("A-1c"), formatting_key("A-1"))

    def test_blank_schedule_cells_are_not_filled_from_the_next_row(self):
        data = _page(
            [
                [(40, "C"), (150, "5'-2\""), (280, "8'-0\"")],
                [(40, "D")],
                [(40, "E")],
                [(40, "G"), (150, "5'-0\""), (280, "8'-0\"")],
            ],
            [(40, "TYPE"), (150, "WIDTH"), (280, "HEIGHT")],
        )
        marks = {row["mark_normalized"]: row for row in read_schedule_dictionary(data)}
        self.assertEqual(marks["C"]["width_inches"], 62)
        self.assertEqual(marks["C"]["height_inches"], 96)
        for blank in ("D", "E"):
            self.assertIsNone(marks[blank]["width_inches"])
            self.assertIsNone(marks[blank]["height_inches"])
            self.assertIn("BLANK_DIMENSION", marks[blank]["issue_codes"])
        self.assertEqual(marks["G"]["width_inches"], 60)
        self.assertNotEqual(marks["D"]["width_inches"], marks["C"]["width_inches"])

    def test_conflicting_sizes_are_kept_and_not_chosen(self):
        data = _page(
            [
                [(40, "B"), (150, "3'-0\""), (280, "4'-0\"")],
                [(40, "B"), (150, "5'-0\""), (280, "6'-0\"")],
            ],
            [(40, "TYPE"), (150, "WIDTH"), (280, "HEIGHT")],
        )
        mark = read_schedule_dictionary(data)[0]
        self.assertIn("DIMENSION_CONFLICT", mark["issue_codes"])
        self.assertIsNone(mark["width_inches"])
        self.assertIsNone(mark["height_inches"])
        self.assertEqual(len(mark["conflicts"]), 2)

    def test_door_mark_does_not_become_the_window_mark(self):
        window = _page(
            [[(40, "A"), (150, "5'-2\""), (280, "8'-0\"")]],
            [(40, "TYPE"), (150, "WIDTH"), (280, "HEIGHT")],
            "WINDOW SCHEDULE",
        )
        door = _page(
            [[(40, "A"), (150, "3'-0\""), (280, "7'-0\""), (400, "WOOD")]],
            [(40, "TYPE"), (150, "WIDTH"), (280, "HEIGHT")],
            "DOOR SCHEDULE",
        )
        records = read_schedule_dictionary(window, "WINDOW") + read_schedule_dictionary(door, "DOOR")
        combined = combine_counts(
            [{"type": "A-1", "count": 2}],
            [{"file": "UNIT-A-1.pdf", "unitType": "A-1", "tags": [{"tag": "A", "room": "BEDROOM"}]}],
            schedules=records,
        )
        line = combined["lines"][0]
        self.assertEqual(line["opening_class"], "WINDOW")
        self.assertEqual(line["widthInches"], 62)
        self.assertEqual(line["heightInches"], 96)
        self.assertEqual(line["quantity"], 2)
        self.assertIsNone(line["shade_quantity"])

    def test_pilot_window_c_is_three_openings_and_not_a_shade_total(self):
        schedule = read_schedule_dictionary(
            _page(
                [[(40, "C"), (150, "5'-2\""), (280, "8'-0\"")]],
                [(40, "TYPE"), (150, "WIDTH"), (280, "HEIGHT")],
            )
        )
        combined = expand_openings(
            [{"type": "A-1A", "count": 3}],
            [
                {
                    "file": "A-4.00-UNIT-A-1-a-Rev.Permit.pdf",
                    "unitType": "A-1-a",
                    "tags": [{"tag": "C", "room": "BEDROOM"}],
                }
            ],
            schedules=schedule,
            matrix_units=[
                {"unit": "LW-104", "type": "A-1A", "qty": 1, "floor": "ground"},
                {"unit": "LW-105", "type": "A-1A", "qty": 1, "floor": "ground"},
                {"unit": "LW-106", "type": "A-1A", "qty": 1, "floor": "ground"},
            ],
            project_id="301-341-madeira-avenue",
        )
        self.assertEqual(combined["aliases"], [{"matrix": "A-1A", "plan": "A-1-a", "basis": "formatting"}])
        record = combined["records"][0]
        self.assertEqual(record["extended_quantity"], 3)
        self.assertEqual(record["count_per_unit"], 1)
        self.assertEqual(record["applicable_unit_count"], 3)
        self.assertEqual(record["width_inches"], 62)
        self.assertEqual(record["height_inches"], 96)
        self.assertEqual(record["review_status"], "provisional")
        self.assertIsNone(record["shade_quantity"])
        self.assertEqual(combined["releasedTotal"], 0)
        self.assertNotEqual(combined["total"], combined.get("shadeQuantity"))

    def test_variants_are_not_collapsed_or_silently_chosen(self):
        combined = combine_counts(
            [{"type": "A-1", "count": 4}, {"type": "A-1a", "count": 2}],
            [
                {"file": "UNIT-A-1.pdf", "unitType": "A-1", "tags": [{"tag": "C", "room": "BEDROOM"}]},
                {"file": "UNIT-A-1-a.pdf", "unitType": "A-1a", "tags": [{"tag": "C", "room": "BEDROOM"}]},
            ],
            {"C": {"width": "5'-2\"", "height": "8'-0\"", "widthInches": 62, "heightInches": 96}},
        )
        types = {row["unitType"] for row in combined["records"]}
        self.assertEqual(types, {"A-1", "A-1a"})
        self.assertEqual(sum(row["quantity"] for row in combined["records"]), 6)

        disagreed = combine_counts(
            [{"type": "B-1", "count": 5}],
            [
                {"file": "UNIT-B-1-a.pdf", "unitType": "B-1", "tags": [{"tag": "C", "room": "BEDROOM"}]},
                {
                    "file": "UNIT-B-1-b.pdf",
                    "unitType": "B-1",
                    "tags": [{"tag": "C", "room": "BEDROOM"}, {"tag": "G", "room": "LIVING"}],
                },
            ],
            {"C": {"width": "5'-0\"", "height": "8'-0\"", "widthInches": 60, "heightInches": 96}},
        )
        self.assertEqual(disagreed["records"], [])
        self.assertTrue(any(item["issue_code"] == "VARIANT_UNCONFIRMED" for item in disagreed["exceptions"]))

    def test_a_plan_for_one_unit_is_not_copied_onto_the_others(self):
        combined = combine_counts(
            [{"type": "A-1", "count": 3}],
            [{"file": "UNIT-201-ONLY.pdf", "unitType": "A-1", "notes": "UNIT 201", "tags": [{"tag": "C", "room": "BEDROOM"}]}],
            {"C": {"width": "5'-2\"", "height": "8'-0\"", "widthInches": 62, "heightInches": 96}},
            matrix_units=[
                {"unit": "201", "type": "A-1", "qty": 1},
                {"unit": "202", "type": "A-1", "qty": 1},
                {"unit": "203", "type": "A-1", "qty": 1},
            ],
        )
        self.assertEqual(combined["records"], [])
        self.assertTrue(any(item["issue_code"] == "VARIANT_UNCONFIRMED" for item in combined["exceptions"]))

    def test_floor_subtotal_is_not_added_as_units(self):
        exceptions = reconcile_floor_totals(
            [
                {"unit": "201", "type": "A-1", "qty": 1, "floor": "2"},
                {"unit": "202", "type": "A-1", "qty": 1, "floor": "2"},
            ],
            [{"floor": "2", "total": 9}],
            printed_total=10,
            transcribed=2,
        )
        codes = [item["issue_code"] for item in exceptions]
        self.assertEqual(codes, ["MATRIX_MISMATCH", "MATRIX_MISMATCH"])

    def test_duplicate_bytes_collapse_and_a_copy_suffix_is_not_a_revision(self):
        same = b"same-drawing"
        locked = lock_drawing_set(
            [
                {"file": "A-6.02-WINDOW-SCHEDULE-Rev.Permit.pdf", "data": same},
                {"file": "A-6.02-WINDOW-SCHEDULE-Rev.Permit (3).pdf", "data": same},
                {"file": "A-4.00-UNIT-A-1-a-Rev.Permit (4).pdf", "data": b"plan-a"},
                {"file": "A-4.00-UNIT-A-1-a-Rev.GMP.pdf", "data": b"plan-b"},
            ],
            ["301-341 Madeira Avenue"],
        )
        self.assertEqual(len(locked["duplicates"]), 1)
        self.assertEqual(copy_suffix("A-6.02-WINDOW-SCHEDULE-Rev.Permit (3).pdf"), "3")
        self.assertEqual(revision_from_name("A-6.02-WINDOW-SCHEDULE-Rev.Permit (3).pdf"), "Permit")
        self.assertNotEqual(revision_from_name("A-6.02-WINDOW-SCHEDULE-Rev.Permit (3).pdf"), "3")
        self.assertTrue(any(item["issue_code"] == "REVISION_CONFLICT" for item in locked["exceptions"]))
        self.assertNotIn("A-4.00-UNIT-A-1-a-Rev.Permit (4).pdf", locked["usable_files"])
        self.assertNotIn("A-4.00-UNIT-A-1-a-Rev.GMP.pdf", locked["usable_files"])

    def test_two_projects_are_not_multiplied_together(self):
        combined = expand_openings(
            [{"type": "A-1", "count": 3}],
            [{"file": "UNIT-A-1.pdf", "unitType": "A-1", "tags": [{"tag": "C", "room": "BEDROOM"}]}],
            {"C": {"width": "5'-2\"", "height": "8'-0\"", "widthInches": 62, "heightInches": 96}},
            project_names=["301-341 Madeira Avenue", "Central Pointe Santa Ana"],
        )
        self.assertEqual(combined["records"], [])
        self.assertEqual(combined["total"], 0)
        self.assertEqual(combined["exceptions"][0]["issue_code"], "PROJECT_SEPARATION")

    def test_square_door_tag_is_excluded_and_an_untagged_opening_is_kept(self):
        doc = fitz.open()
        page = doc.new_page(width=900, height=640)
        page.insert_text(fitz.Point(80, 560), "UNIT TYPE A-1 FLOOR PLAN")
        page.insert_text(fitz.Point(120, 180), "BEDROOM")
        page.draw_oval(fitz.Rect(150, 240, 172, 276))
        page.insert_text(fitz.Point(154, 262), "C")
        page.draw_rect(fitz.Rect(300, 248, 314, 262))
        page.insert_text(fitz.Point(301, 259), "8A")
        page.draw_oval(fitz.Rect(200, 300, 222, 336))
        data = doc.tobytes()
        doc.close()
        counted = count_plan_openings(data)
        self.assertEqual([tag["tag"] for tag in counted["tags"]], ["C"])
        self.assertEqual(counted["doors"], [{"tag": "8A", "room": "BEDROOM"}])
        self.assertEqual(len(counted["untagged"]), 1)
        combined = combine_counts(
            [{"type": "A-1", "count": 2}],
            [
                {
                    "file": "UNIT-A-1.pdf",
                    "unitType": "A-1",
                    "openings": counted["openings"],
                    "doors": counted["doors"],
                    "tags": counted["tags"],
                }
            ],
            {"C": {"width": "5'-2\"", "height": "8'-0\"", "widthInches": 62, "heightInches": 96}},
        )
        self.assertEqual(combined["lines"][0]["windowTag"], "C")
        self.assertEqual(combined["lines"][0]["quantity"], 2)
        self.assertEqual(combined["excluded"][0]["windowTag"], "8A")
        self.assertEqual(combined["excluded"][0]["review_status"], "excluded")
        self.assertIn("exclusion_reason", combined["excluded"][0])

    def test_supplied_window_schedule_keeps_c_and_the_blank_rows(self):
        path = _ARCH / "A-6.02-STOREFRONT-&-WINDOW-SCHEDULE-Rev.Permit.pdf"
        if not path.exists():
            self.skipTest("supplied A-6.02 is not on this machine")
        marks = {
            (row["opening_class"], row["mark_normalized"]): row
            for row in read_schedule_dictionary(path.read_bytes(), "WINDOW")
        }
        window_c = marks[("WINDOW", "C")]
        self.assertEqual(window_c["width_inches"], 62)
        self.assertEqual(window_c["height_inches"], 96)
        for blank in ("D", "E", "F", "H", "I", "L"):
            row = marks[("WINDOW", blank)]
            self.assertIsNone(row["width_inches"])
            self.assertIsNone(row["height_inches"])
            self.assertIn("BLANK_DIMENSION", row["issue_codes"])
        self.assertEqual(marks[("STOREFRONT", "ST-1")]["opening_class"], "STOREFRONT")
        self.assertNotIn(("WINDOW", "ST-1"), marks)

    def test_supplied_unit_plan_counts_bedroom_c_once(self):
        path = _ARCH / "A-4.00-UNIT-A-1-a-Rev.Permit.pdf"
        if not path.exists():
            self.skipTest("supplied A-4.00 is not on this machine")
        counted = count_plan_openings(path.read_bytes())
        bedrooms = [tag for tag in counted["tags"] if tag["tag"] == "C" and tag["room"] == "BEDROOM"]
        self.assertEqual(len(bedrooms), 1)


if __name__ == "__main__":
    unittest.main()
