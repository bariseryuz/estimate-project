import unittest

from rag.pdf_pages import prioritize_pdf_page_indices


class TestPdfPages(unittest.TestCase):
    def test_prioritize_schedule_pages(self):
        texts = [
            "cover sheet project info",
            "window schedule tag width height room",
            "floor plan level 2",
            "general notes shade motorized",
        ]
        indices = prioritize_pdf_page_indices(texts, max_pages=3)
        self.assertIn(2, indices)
        self.assertIn(4, indices)
        self.assertLessEqual(len(indices), 3)


if __name__ == "__main__":
    unittest.main()
