"""Window tags on a unit plan are counted from the drawn marks."""

import unittest

import fitz

from domain.plan_openings import count_plan_openings


def _sheet() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=900, height=640)
    page.insert_text(fitz.Point(80, 560), "UNIT TYPE A-1 FLOOR PLAN")
    page.insert_text(fitz.Point(120, 180), "BEDROOM")
    page.insert_text(fitz.Point(420, 180), "BATHROOM")
    page.draw_oval(fitz.Rect(150, 240, 172, 276))
    page.insert_text(fitz.Point(154, 262), "U2")
    page.draw_oval(fitz.Rect(430, 240, 452, 276))
    page.insert_text(fitz.Point(436, 262), "M")
    page.draw_rect(fitz.Rect(300, 248, 314, 262))
    page.insert_text(fitz.Point(301, 259), "8A")
    data = doc.tobytes()
    doc.close()
    return data


class TestPlanOpenings(unittest.TestCase):
    def test_oval_tags_are_windows_and_square_tags_are_doors(self):
        counted = count_plan_openings(_sheet())
        self.assertIsNotNone(counted)
        self.assertEqual(counted["windows"], 2)
        self.assertEqual(counted["shades"], 1)
        self.assertEqual(counted["blinds"], 0)
        rooms = {tag["tag"]: tag["room"] for tag in counted["tags"]}
        self.assertEqual(rooms["U2"], "BEDROOM")
        self.assertEqual(rooms["M"], "BATHROOM")
        self.assertNotIn("8A", rooms)


if __name__ == "__main__":
    unittest.main()
