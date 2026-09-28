"""Accuracy rules that apply to every project, not one building."""

import io
import unittest

from openpyxl import Workbook

from domain.direct_shades_workbooks import merge_workbook_analyses, parse_workbook_xlsx
from domain.pricing import price_takeoff
from domain.workbook_takeoff import build_takeoff_from_workbook


def _save(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _matrix_with_split_sizes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "WINDOW MATRIX"
    ws.append(["WINDOW MARKINGS", "SHADES PER OPENING", "TOTAL WINDOWS", "TOTAL SHADES"])
    ws.append(["LIVING AREAS"])
    ws.append(["A", 1, 1, 1])
    ws.append(["FD A1", 1, 8, 8])
    ws.append(["BEDROOMS"])
    ws.append(["A", 1, 3, 3])
    ws.append(["TOTAL", "", 12, 12])

    living = wb.create_sheet("Blind QTY UNITS - LIVINGS")
    living.append(["Area", "UNITS - LIVING AREAS"])
    living.append(
        [
            "WINDOW & DOOR",
            "QTY",
            "WIDTH (FT')",
            'WIDTH (INCH")',
            "HEIGHT (FT')",
            'HEIGHT (INCH")',
            "W (FT)",
            "H (FT)",
            "AREA (SQYD)",
            "SYSTEM TYPE",
            "W (INCH)",
            "H (INCH)",
        ]
    )
    living.append(["", "", "", "", "", "", "", "", 2.5, "CASSETTE + MOTOR", "", ""])
    living.append(["A", 1, 7, 0, 11, 0, 7, 11, 8.5, '4" FASCIA', 84, 132])
    living.append(["FD A1", 8, 3, 5, 8, 0, 3.42, 8, 3.03, '3" FASCIA', 41, 96])
    living.append(["TOTAL SHADES", 9])

    beds = wb.create_sheet("Blind QTY UNITS - BEDROOMS")
    beds.append(["Area", "UNITS - BEDROOMS"])
    beds.append(["WINDOW & DOOR", "QTY", "W (INCH)", "H (INCH)", "SYSTEM TYPE"])
    beds.append(["A", 3, 84, 120, '4" FASCIA'])
    beds.append(["TOTAL SHADES", 3])

    common = wb.create_sheet("Blind QTY UNITS - COMMONS")
    common.append(["Area", "COMMON AREAS"])
    common.append(["WINDOW & DOOR", "QTY", "W (INCH)", "H (INCH)", "SYSTEM TYPE"])
    common.append(["CLUB ROOM (108)"])
    common.append(["B", 2, 84, 120, "MOTORIZED FASCIA"])
    common.append(["TOTAL SHADES", 2])
    return _save(wb)


def _bid_with_alternate() -> bytes:
    wb = Workbook()
    units = wb.active
    units.title = "UNITS"
    units.append(["Window Marking", "W", "H", "SQF", "Cost Sqft", "Cost Per Shade", "Qty", "Total Cost", "Price Sqft", "Sales Price", "Total Price"])
    units.append(["LIVING AREAS"])
    units.append(["FD A1", 41, 96, 27.3, 1.57, 40, 8, 320, 1.65, 50, 400])
    units.append(["Total Product", "", "", "", "", "", 8, 320, "", "", 400])

    blk = wb.create_sheet("BLK")
    blk.append(["Window Marking", "Qty", "Sales Price", "Total Price"])
    blk.append(["LIVING AREAS"])
    blk.append(["FD A1", 8, 90, 720])
    blk.append(["Total Product", "", "", 720])

    com = wb.create_sheet("COMMONS")
    com.append(["Window Marking", "Qty", "Sales Price", "Total Price"])
    com.append(["COMMON AREAS"])
    com.append(["B", 2, 25, 50])
    com.append(["Total Product", "", "", 50])
    return _save(wb)


class TestScopeAndDimensions(unittest.TestCase):
    def test_finished_size_and_repeated_tags_and_extra_area(self):
        parsed = parse_workbook_xlsx("Window Matrix.xlsx", _matrix_with_split_sizes())
        fd = next(l for l in parsed.blind_qty_lines if l["windowTag"] == "FD A1")
        self.assertEqual(fd["widthInches"], 41)
        self.assertEqual(fd["heightInches"], 96)
        self.assertNotEqual(fd["widthInches"], 5)
        self.assertIsNone(fd["room"])

        tags = [l["windowTag"] for l in parsed.blind_qty_lines]
        self.assertEqual(tags.count("A"), 2)
        self.assertNotIn("", tags)

        merged = merge_workbook_analyses([parsed])
        self.assertEqual(merged["authoritativeTotalShades"], 12)
        self.assertEqual(merged["additionalShadeCount"], 2)
        self.assertEqual(merged["projectShadeCount"], 14)

        takeoff = build_takeoff_from_workbook(merged)
        self.assertEqual(takeoff["totalShadeCount"], 14)
        a_lines = [i for i in takeoff["takeoffItems"] if i["windowTag"] == "A"]
        self.assertEqual(len(a_lines), 2)
        self.assertEqual(sorted(i["quantity"] for i in a_lines), [1, 3])
        self.assertEqual(takeoff["motorizedCount"], 2)


class TestBidAlternates(unittest.TestCase):
    def test_same_markings_are_not_added(self):
        parsed = parse_workbook_xlsx("Bid Summary.xlsx", _bid_with_alternate())
        self.assertEqual(parsed.bid_grand_total, 450)
        roles = {t["sheet"]: t["role"] for t in parsed.bid_tabs}
        self.assertEqual(roles["UNITS"], "base")
        self.assertEqual(roles["BLK"], "alternate")
        self.assertEqual(roles["COMMONS"], "base")

        merged = merge_workbook_analyses([parsed])
        pricing = merged["referencePricing"]
        self.assertEqual(pricing["grandTotal"], 450)
        self.assertIn("BLK", pricing["alternateSheets"])
        self.assertNotIn("BLK", pricing["quotedSheets"])

        takeoff_lines = [
            {"windowTag": "FD A1", "quantity": 8, "areaSection": "LIVING AREAS", "category": "Shade"},
            {"windowTag": "B", "quantity": 2, "areaSection": "COMMON AREAS", "category": "Shade"},
        ]
        priced = price_takeoff(takeoff_lines, workbook=merged)
        self.assertEqual(priced["priceSource"], "bid_line")
        fd = next(l for l in priced["lines"] if l["windowTag"] == "FD A1")
        self.assertEqual(fd["unitPrice"], 50)
        self.assertEqual(fd["extendedPrice"], 400)
        self.assertEqual(priced["total"], 450)
        self.assertIsNone(pricing.get("clientTotal"))


def _bid_with_total_bid() -> bytes:
    """Product lines plus the sell-side stack under them, and an accounting grand total."""
    wb = Workbook()
    units = wb.active
    units.title = "UNITS"
    units.append(["Window Marking", "W", "H", "SQF", "Cost Sqft", "Cost Per Shade", "Qty", "Total Cost", "Price Sqft", "Sales Price", "Total Price"])
    units.append(["FD A1", 41, 96, 27.3, 1.57, 40, 8, 320, 1.65, 50, 400])
    units.append(["Total Product", "", "", "", "", "", 8, 320, "", "", 400])
    units.append(["Installation", "", "", "", "", "Cost Per Blind", "", "", "", "Charge Per Blind", "", "", "Sqyds."])
    units.append(["", "", "", "", "", 20, "", 160, "", 66, 528, "", 99])
    units.append(["Description", "QTY", "Price", "", "", "", "", "Cost", "", "", "Price"])
    units.append(["Trip Charges", 1, 150, "", "", "", "", 150, "Trip Charge", "", 150])
    units.append(["Floor Surcharge", 0, 200, "", "", "", "", 0, "Floor Surcharge", "", 0])
    units.append(["Sub Total", "", "", "", "", "", "", 1000, "", "", 1078])
    units.append(["", "", "", "", "", "", "", "", "Tax", 0.07, 75.46])
    units.append(["", "", "", "", "", "", "", "", "Total Bid", "", 1153.46])
    units.append(["Motorization"])
    units.append(["Celtic II Wired", 0, 75, 200, 0, 0])
    units.append(["", "", "", "", "", "", "", "", "GRAND TOTAL", 1153.46])
    units.append(["Grand Total", "", "", "", 0, 0])
    units.append(["Accounting Summary"])
    units.append(["GRAND TOTAL", 9999])

    com = wb.create_sheet("COMMONS")
    com.append(["Window Marking", "Qty", "Sales Price", "Total Price"])
    com.append(["B", 2, 25, 50])
    com.append(["Total Product", "", "", 50])
    com.append(["Installation", "", "", "", "", "Cost Per Blind", "", "", "", "Charge Per Blind"])
    com.append(["", "", "", "", "", 17, "", 34, "", 70, 140])
    com.append(["Description", "QTY", "Price", "", "", "", "", "Cost", "", "", "Price"])
    com.append(["Trip Charges", 1, 10, "", "", "", "", 10, "Trip Charge", "", 10])
    com.append(["", "", "", "", "", "", "", "", "Tax", 0.07, 14])
    com.append(["", "", "", "", "", "", "", "", "Total Bid", "", 214])
    com.append(["Motorization"])
    com.append(["Celtic II Wired", 2, 75, 200, 150, 400])
    com.append(["Labor", 2, 15, 17, 30, 34])
    com.append(["", "", "", "", "", "", "", "", "GRAND TOTAL", 648])
    com.append(["Grand Total", "", "", "", 180, 434])
    com.append(["Accounting Summary"])
    com.append(["GRAND TOTAL", 5000])
    return _save(wb)


class TestCommercialStack(unittest.TestCase):
    def test_total_bid_is_the_client_number_not_the_accounting_total(self):
        parsed = parse_workbook_xlsx("Bid Summary.xlsx", _bid_with_total_bid())
        by_sheet = {t["sheet"]: t for t in parsed.bid_tabs}
        units = by_sheet["UNITS"]["commercial"]
        self.assertEqual(units["installation"], 528)
        self.assertEqual(units["charges"], [{"name": "Trip Charges", "amount": 150}])
        self.assertEqual(units["tax"], 75.46)
        self.assertEqual(units["totalBid"], 1153.46)
        self.assertIsNone(units["motorizedTotal"])
        self.assertEqual(units["clientTotal"], 1153.46)

        commons = by_sheet["COMMONS"]["commercial"]
        self.assertEqual(commons["motorQuantity"], 2)
        self.assertEqual(commons["motorizedTotal"], 648)
        self.assertEqual(commons["clientTotal"], 648)

        merged = merge_workbook_analyses([parsed])
        pricing = merged["referencePricing"]
        self.assertEqual(pricing["grandTotal"], 450)
        self.assertEqual(pricing["clientTotal"], 1801.46)

        takeoff_lines = [
            {"windowTag": "FD A1", "quantity": 8, "category": "Shade"},
            {"windowTag": "B", "quantity": 2, "category": "Shade"},
        ]
        priced = price_takeoff(takeoff_lines, workbook=merged)
        self.assertEqual(priced["subtotal"], 450)
        self.assertEqual(priced["total"], 1801.46)
        fd = next(l for l in priced["lines"] if l["windowTag"] == "FD A1")
        self.assertEqual(fd["extendedPrice"], 400)


if __name__ == "__main__":
    unittest.main()
