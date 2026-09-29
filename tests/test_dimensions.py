import unittest

from utils.dimensions import enrich_takeoff_item, parse_length_inches, square_feet


class TestDimensions(unittest.TestCase):
    def test_inches_quote(self):
        self.assertEqual(parse_length_inches('48"'), 48.0)

    def test_feet_inches(self):
        self.assertEqual(parse_length_inches("4' 6"), 54.0)

    def test_feet_hyphen_inches(self):
        self.assertEqual(parse_length_inches("3'-5\""), 41.0)

    def test_plain_number_large_is_inches(self):
        self.assertEqual(parse_length_inches("72"), 72.0)

    def test_plain_number_small_is_feet(self):
        self.assertEqual(parse_length_inches("6"), 72.0)

    def test_a_word_with_an_uppercase_x_is_not_a_size(self):
        self.assertIsNone(parse_length_inches("EXTRA"))
        self.assertEqual(parse_length_inches("3'-0\" X 5'-0\""), 36.0)

    def test_square_feet(self):
        self.assertEqual(square_feet(48, 72), 24.0)

    def test_enrich_takeoff_item(self):
        item = {"width": '48"', "height": '72"', "quantity": 2, "windowTag": "A-1"}
        enrich_takeoff_item(item)
        self.assertEqual(item["widthInches"], 48.0)
        self.assertEqual(item["heightInches"], 72.0)
        self.assertEqual(item["squareFeet"], 24.0)
        self.assertIn("calculationBasis", item)


if __name__ == "__main__":
    unittest.main()
