"""
Metric-specific counting guidelines — authority rules for accurate take-off.

Injected into vision, take-off, and reconciliation prompts so each metric
(shades, blinds, screens, motorized, etc.) follows the correct source of truth.
"""

from __future__ import annotations

METRIC_GUIDELINES = """
METRIC AUTHORITY (which document wins when sources disagree):
  1. Window Schedule / Opening Schedule table (Excel sheet or PDF schedule) = PRIMARY for counts
  2. Specification Section 12 20 00 / window treatment spec = PRIMARY for product type & motor
  3. Floor plans = cross-check only (count tags; do NOT exceed schedule unless schedule missing)
  4. Elevations = sizing cross-check (width × height)
  5. General notes / keynotes = scope inclusions/exclusions (NIC, by others, OFCI)
  6. Vision on drawing pages = use when text layer is empty; still dedupe by window TAG

DEDUPLICATION:
  • Same window TAG on multiple pages = ONE line item (qty from schedule row, not sum of page sightings)
  • Same opening on plan + elevation + schedule = one unit
  • Do not double-count paired tags (W-101A / W-101B) unless schedule shows qty 2

SHADES (roller, solar, blackout, cellular, roman, dual):
  • Count: one EA per schedule row where treatment column indicates shade/screen/shd/ws
  • If schedule says "NIC" or "by owner" for treatment → needsShade=false, exclude from offer qty
  • Motorized: separate metric — count motors where spec or schedule column says MOT/motorized

BLINDS (horizontal, vertical, mini, Venetian):
  • Count separately from roller shades — productKind=Blind
  • Vertical blinds on sliding doors: count openings in door schedule if marked BLIND/VB

SCREENS (solar screen, exterior screen):
  • productKind=Screen; may overlap solar shade — follow spec (interior vs exterior)

METRICS THAT NEED THEIR OWN RULE:
  | Metric              | Source priority        | Count rule |
  |---------------------|------------------------|------------|
  | totalShades         | Window schedule rows   | Sum qty where treatment is shade type |
  | totalBlinds         | Schedule / room finish | Sum qty blind types |
  | motorizedCount      | Spec + schedule MOT    | Count rows with motor or spec mandate |
  | blackoutCount       | Schedule/spec BB       | Count blackout/solar+blackout dual |
  | insideMountCount    | Schedule remarks IMT   | Count with inside mount note |
  | outsideMountCount   | Schedule remarks OMT   | Count with outside mount note |
  | excludedNIC         | Notes / schedule NIC   | List but do not price |

ACCURACY REQUIREMENTS:
  • Analyze EVERY sheet (Excel) and EVERY page (PDF) provided — do not skip pages
  • If a page has no windows, record in pageSummaries as "no openings"
  • Missing dimensions: include line with notes "TBD — verify field" — do not invent sizes
  • Final totals must equal sum of line item quantities (reconcile before output)
  • If vision count ≠ schedule count, prefer schedule and note discrepancy in confidenceNotes
""".strip()


def metric_guidelines_for_prompt() -> str:
    return METRIC_GUIDELINES
