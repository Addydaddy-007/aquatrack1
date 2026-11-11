"""Turn a report dict into CSV or PDF bytes.

Report dict: {title, subtitle, notes: [str], columns: [str], rows: [[...]], totals_row: [...] | None}
"""
import csv
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#0B2545")
TEAL = colors.HexColor("#13A89E")


def to_csv(report: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"# {report['title']}"])
    if report.get("subtitle"):
        w.writerow([f"# {report['subtitle']}"])
    for note in report.get("notes", []):
        w.writerow([f"# {note}"])
    w.writerow(report["columns"])
    w.writerows(report["rows"])
    if report.get("totals_row"):
        w.writerow(report["totals_row"])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")   # BOM so Excel opens it with the right encoding


def to_pdf(report: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm, title=report["title"])
    styles = getSampleStyleSheet()
    story = [Paragraph(report["title"], styles["Title"])]
    if report.get("subtitle"):
        story.append(Paragraph(report["subtitle"], styles["Normal"]))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
    story.append(Spacer(1, 4 * mm))
    for note in report.get("notes", []):
        story.append(Paragraph(f"- {note}", styles["BodyText"]))
    story.append(Spacer(1, 4 * mm))
    small = styles["BodyText"].clone("cell", fontSize=7, leading=9)
    head = small.clone("head", textColor=colors.white)
    data = [[Paragraph(f"<b>{c}</b>", head) for c in report["columns"]]]
    data += [[Paragraph(str(v), small) for v in row] for row in report["rows"]]
    if report.get("totals_row"):
        data.append([Paragraph(f"<b>{v}</b>", small) for v in report["totals_row"]])
    table = Table(data, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
             ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F7FA")])]
    if report.get("totals_row"):
        style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#D9F3F0")))
    table.setStyle(TableStyle(style))
    story.append(table)
    doc.build(story)
    return buf.getvalue()
