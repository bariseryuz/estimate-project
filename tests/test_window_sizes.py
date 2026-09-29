"""Window sizes come from the columns a schedule prints, on any project."""

import unittest

import fitz

from domain.drawing_set import _project_name, combine_counts
from domain.window_sizes import read_window_sizes


def _page(rows: list[list[tuple[float, str]]], headers: list[tuple[float, str]]) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=900, height=500)
    for x, text in headers:
        page.insert_text(fitz.Point(x, 70), text)
    for index, row in enumerate(rows):
        for x, text in row:
            page.insert_text(fitz.Point(x, 110 + index * 24), text)
    page.insert_text(fitz.Point(620, 110), "14'-3\"")
    data = doc.tobytes()
    doc.close()
    return data


class TestWindowSizes(unittest.TestCase):
    def test_mark_w_h_headers_set_the_size(self):
        sizes = read_window_sizes(
            _page(
                [[(40, "W1"), (150, "3'-0\""), (260, "6'-0\"")]],
                [(40, "MARK"), (150, "W"), (260, "H")],
            )
        )
        self.assertEqual(sizes["W1"]["widthInches"], 36)
        self.assertEqual(sizes["W1"]["heightInches"], 72)
        self.assertNotIn("14", str(sizes["W1"]["width"]))

    def test_type_width_height_headers_set_the_size(self):
        sizes = read_window_sizes(
            _page(
                [[(40, "A"), (170, "4'-0\""), (300, "5'-2\"")]],
                [(40, "TYPE"), (170, "WIDTH"), (300, "HEIGHT")],
            )
        )
        self.assertEqual(sizes["A"]["width"], "4'-0\"")
        self.assertEqual(sizes["A"]["heightInches"], 62)

    def test_close_columns_keep_each_size_and_ignore_a_note(self):
        sizes = read_window_sizes(
            _page(
                [[(40, "A"), (100, "7'-0\""), (145, "11'-0\""), (210, "ALUM.")]],
                [(40, "TYPE"), (100, "W"), (145, "H")],
            )
        )
        self.assertEqual(sizes["A"]["widthInches"], 84)
        self.assertEqual(sizes["A"]["heightInches"], 132)

    def test_a_mark_with_two_different_sizes_is_not_guessed(self):
        sizes = read_window_sizes(
            _page(
                [
                    [(40, "B"), (170, "3'-0\""), (300, "4'-0\"")],
                    [(40, "B"), (170, "5'-0\""), (300, "6'-0\"")],
                ],
                [(40, "TYPE"), (170, "WIDTH"), (300, "HEIGHT")],
            )
        )
        self.assertNotIn("B", sizes)

    def test_plan_marks_keep_the_shade_total_and_take_the_schedule_size(self):
        combined = combine_counts(
            [{"type": "C-2", "count": 10}],
            [
                {
                    "file": "UNIT-C-2.pdf",
                    "unitType": "C-2",
                    "shadeOpenings": 2,
                    "tags": [
                        {"tag": "W1", "room": "LIVING"},
                        {"tag": "W1", "room": "BEDROOM"},
                    ],
                }
            ],
            {"W1": {"width": "3'-0\"", "height": "6'-0\"", "widthInches": 36, "heightInches": 72}},
        )
        self.assertEqual(combined["total"], 20)
        self.assertEqual(len(combined["lines"]), 1)
        line = combined["lines"][0]
        self.assertEqual(line["windowTag"], "W1")
        self.assertEqual(line["unitType"], "C-2")
        self.assertEqual(line["quantity"], 20)
        self.assertEqual(line["widthInches"], 36)
        self.assertEqual(line["heightInches"], 72)

    def test_project_name_is_whatever_the_title_block_prints(self):
        named = _project_name(
            [
                {"text": "PROJECT: Harbor Hotel\n100 MAIN STREET"},
                {"text": "PROJECT: Harbor Hotel"},
            ]
        )
        self.assertEqual(named, "Harbor Hotel")
        addressed = _project_name([{"text": "88 OAK AVENUE\n88 OAK AVENUE"}])
        self.assertEqual(addressed, "88 OAK AVENUE")


if __name__ == "__main__":
    unittest.main()
