"""Generate the synthetic sample report used to demonstrate uploads.

    python scripts/make_sample_upload.py

Everything in it is invented. It stands in for the analyst reports a team buys
(and cannot share), so the upload path can be shown and tested end to end.
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table

OUT = Path(__file__).resolve().parents[1] / "data" / "samples" / "poc_cardiac_markers_market_2026_synthetic.pdf"

PAGES = [
    ("Point-of-care cardiac markers: market report 2026", [
        "SYNTHETIC SAMPLE. All companies, figures and findings in this document are invented for software testing.",
        "This report covers point-of-care tests for cardiac troponin, natriuretic peptides and D-dimer used in emergency "
        "departments, ambulances and primary care.",
    ]),
    ("1. Market size", [
        "We estimate the global point-of-care troponin market at USD 0.88-1.02 billion in 2026, growing at a CAGR of 9.5% "
        "from 2026 to 2031.",
        "High-sensitivity assays are expected to account for most new placements by 2028 as emergency departments adopt "
        "0/1-hour rule-out pathways.",
    ]),
    ("2. Pricing", [
        "US list prices for point-of-care high-sensitivity troponin tests range from USD 11 to USD 16 per test, before "
        "group purchasing discounts.",
        "European tender prices are lower and continue to fall as more CE-marked products compete.",
    ]),
    ("3. Competitors", [
        "The table lists companies with point-of-care troponin products or late-stage programmes.",
    ]),
]

TABLE = [["Company", "Product", "Sensitivity", "Regions"],
         ["Kestrel Diagnostics", "K-Point hs-cTnI", "High", "EU (US pending)"],
         ["Norvia Medical", "Norvia QT hs-cTnI", "High", "EU"],
         ["Altamira Bio", "Biosensor hs-cTnI", "High", "In development"]]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    story = []
    for i, (head, paras) in enumerate(PAGES):
        story.append(Paragraph(head, styles["Heading1"] if i == 0 else styles["Heading2"]))
        for p in paras:
            story += [Paragraph(p, styles["BodyText"]), Spacer(1, 8)]
        if head.startswith("3."):
            story.append(Table(TABLE))
        story.append(PageBreak())
    SimpleDocTemplate(str(OUT), pagesize=A4, title="POC cardiac markers market 2026 (synthetic sample)").build(story)
    print(OUT)


if __name__ == "__main__":
    main()
