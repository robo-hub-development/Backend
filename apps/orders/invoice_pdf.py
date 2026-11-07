"""The invoice as a PDF (for the customer's email and the admin download).

Built with ReportLab (pure Python: no browser or system libraries) from the same `invoice_context` as the
printable HTML page, so both always show the same numbers. It uses the PDF standard fonts (Helvetica), so no
font files ship with the app. Those fonts have no rupee sign (amounts print as "Rs.") and cover Latin text
only: any other character, e.g. a Devanagari name, is printed as "?" instead of failing.
"""

from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

REGULAR, BOLD = "Helvetica", "Helvetica-Bold"
INK = colors.HexColor("#1b2433")
NAVY, ORANGE = colors.HexColor("#03244f"), colors.HexColor("#fe6b03")
MUTED, LINE, SOFT = colors.HexColor("#5b6678"), colors.HexColor("#d5dce8"), colors.HexColor("#f3f6fb")
RUPEE = "Rs."
MARGIN = 15 * mm
WIDTH = A4[0] - 2 * MARGIN


def _latin(text) -> str:
    """Keep only what the standard PDF fonts can draw (Windows-1252); anything else becomes "?"."""
    return str(text).encode("cp1252", "replace").decode("cp1252")


def _raw(text: str, style: ParagraphStyle) -> Paragraph:
    """A paragraph from markup that is already escaped, limited to what the standard fonts can draw."""
    return Paragraph(_latin(text), style)


def _style(name="body", size=9, font=REGULAR, color=INK, leading=None, **extra):
    return ParagraphStyle(
        name, fontName=font, fontSize=size, leading=leading or size * 1.35, textColor=color, **extra
    )


def _p(text, **style) -> Paragraph:
    """A paragraph from untrusted text: the markup characters are escaped, line breaks kept."""
    return Paragraph(escape(_latin(text)).replace("\n", "<br/>"), _style(**style))


def _markup(text: str, **style) -> Paragraph:
    return Paragraph(_latin(text), _style(**style))


def _money(value) -> str:
    return f"{RUPEE} {Decimal(value):.2f}"


def _amount(value) -> str:
    return f"{Decimal(value):.2f}"


def render_invoice_pdf(ctx: dict) -> bytes:
    """One invoice, as PDF bytes. `ctx` is `orders.invoice.invoice_context(order)`."""
    order, seller = ctx["order"], ctx["seller"]
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=28 * mm,
        title=f"Invoice {ctx['invoice_number']}",
        author=seller["name"],
        subject=f"Order {order.order_number}",
        invariant=True,  # same bytes for the same invoice (no creation timestamp or random id)
    )
    story = [
        _header(ctx, order, seller),
        Spacer(1, 5 * mm),
        _parties(ctx),
        Spacer(1, 5 * mm),
        _items(ctx),
        Spacer(1, 3 * mm),
        _totals(ctx, order),
        Spacer(1, 4 * mm),
        _boxed(_markup(f"<b>Amount in words:</b> {escape(ctx['in_words'])}", size=9), SOFT),
    ]
    if ctx["cash_due"]:
        story += [Spacer(1, 4 * mm), _cod_box(ctx["cash_due"])]
    story += [Spacer(1, 4 * mm), _delivery_and_notes(order)]

    def decorate(canvas, doc):
        _footer(canvas, seller)
        if ctx["not_for_dispatch"]:
            _stamp(canvas, f"{order.get_status_display()} - do not ship")

    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()


def _header(ctx, order, seller) -> Table:
    left = [_p(seller["name"], size=15, font=BOLD, color=NAVY), _p(seller["address"], color=MUTED)]
    left.append(_p(f"Phone: {seller['phone']}  |  {seller['email']}", color=MUTED))
    if seller.get("gstin"):
        left.append(_markup(f"<b>GSTIN:</b> {escape(seller['gstin'])}"))
    meta = ParagraphStyle("meta", fontName=REGULAR, fontSize=9, leading=12.5, alignment=TA_RIGHT)
    payment_status = order.get_payment_status_display()
    title = ParagraphStyle("title", parent=meta, fontName=BOLD, fontSize=15, leading=20, textColor=ORANGE)
    right = [
        _raw("INVOICE", title),
        _raw(f"Invoice no. <b>{escape(ctx['invoice_number'])}</b>", meta),
        _raw(f"Invoice date {order.created_at:%d %b %Y}", meta),
        _raw(f"Order no. {escape(order.order_number)}", meta),
        _raw(f"Payment {escape(order.get_payment_method_display())}, {escape(payment_status)}", meta),
    ]
    table = Table([[left, right]], colWidths=[WIDTH * 0.58, WIDTH * 0.42])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 2, NAVY),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _address_block(title: str, ship: dict) -> list:
    lines = [_p(title.upper(), size=7.5, color=MUTED, font=BOLD), _p(ship["name"], font=BOLD)]
    lines += [_p(line) for line in ship["lines"]]
    lines += [_p(ship["city_line"]), _p(f"Phone: {ship['mobile']}")]
    return lines


def _parties(ctx) -> Table:
    gap = 4 * mm
    half = (WIDTH - gap) / 2
    table = Table(
        [[_address_block("Bill to", ctx["ship"]), "", _address_block("Ship to", ctx["ship"])]],
        colWidths=[half, gap, half],
    )
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (0, 0), 0.8, LINE),
                ("BOX", (2, 0), (2, 0), 0.8, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (0, 0), 8),
                ("LEFTPADDING", (2, 0), (2, 0), 8),
            ]
        )
    )
    return table


def _items(ctx) -> Table:
    head_style = {"font": BOLD, "color": colors.white, "size": 8.5}
    rows = [
        [
            _p("#", **head_style),
            _p("Item", **head_style),
            _p("Qty", alignment=TA_RIGHT, **head_style),
            _p(f"Unit price ({RUPEE})", alignment=TA_RIGHT, **head_style),
            _p(f"Amount ({RUPEE})", alignment=TA_RIGHT, **head_style),
        ]
    ]
    for number, item in enumerate(ctx["items"], start=1):
        rows.append(
            [
                _p(number),
                _p(item["name"]),
                _p(item["quantity"], alignment=TA_RIGHT),
                _p(_amount(item["unit_price"]), alignment=TA_RIGHT),
                _p(_amount(item["amount"]), alignment=TA_RIGHT),
            ]
        )
    table = Table(rows, colWidths=[9 * mm, None, 14 * mm, 30 * mm, 32 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _totals(ctx, order) -> Table:
    label, value = {"alignment": TA_RIGHT, "color": MUTED}, {"alignment": TA_RIGHT}
    rows = [[_p("Items total", **label), _p(_money(order.subtotal), **value)]]
    for charge in ctx["charges"]:
        rows.append([_p(charge.label, **label), _p(_money(charge.amount), **value)])
    total_at = len(rows)
    rows.append(
        [
            _p("Total", font=BOLD, size=11, alignment=TA_RIGHT),
            _p(_money(order.total), font=BOLD, size=11, alignment=TA_RIGHT),
        ]
    )
    if order.amount_paid:
        rows.append([_p("Paid online", **label), _p(f"- {_money(order.amount_paid)}", **value)])
    style = [
        ("LINEABOVE", (0, total_at), (-1, total_at), 1.5, NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if ctx["cash_due"]:
        rows.append(
            [
                _p(
                    "Cash to collect on delivery",
                    font=BOLD,
                    color=colors.HexColor("#b24400"),
                    alignment=TA_RIGHT,
                ),
                _p(_money(ctx["cash_due"]), font=BOLD, color=colors.HexColor("#b24400"), alignment=TA_RIGHT),
            ]
        )
        style.append(("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), colors.HexColor("#fff4e8")))
    table = Table(rows, colWidths=[52 * mm, 36 * mm], hAlign="RIGHT")
    table.setStyle(TableStyle(style))
    return table


def _boxed(content, background) -> Table:
    table = Table([[content]], colWidths=[WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _cod_box(cash_due) -> Table:
    box = Table(
        [[_p(f"CASH ON DELIVERY - collect {_money(cash_due)}", font=BOLD, size=10.5, alignment=1)]],
        colWidths=[WIDTH],
    )
    box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1.3, ORANGE, 1, (4, 3)),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return box


def _delivery_and_notes(order) -> Table:
    delivery = [_p("DELIVERY", size=7.5, color=MUTED, font=BOLD)]
    if order.courier_name:
        delivery.append(_markup(f"Delivery partner: <b>{escape(order.courier_name)}</b>"))
    if order.tracking_number:
        delivery.append(_markup(f"Tracking no.: <b>{escape(order.tracking_number)}</b>"))
    if not (order.courier_name or order.tracking_number):
        delivery.append(_p("Tracking details will be shared after dispatch."))
    notes = [
        _p("NOTES", size=7.5, color=MUTED, font=BOLD),
        _p("Please check the parcel on delivery and keep this invoice for warranty and returns."),
    ]
    gap = 4 * mm
    half = (WIDTH - gap) / 2
    table = Table([[delivery, "", notes]], colWidths=[half, gap, half])
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (0, 0), 0.8, LINE),
                ("BOX", (2, 0), (2, 0), 0.8, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (0, 0), 8),
                ("LEFTPADDING", (2, 0), (2, 0), 8),
            ]
        )
    )
    return table


def _footer(canvas, seller) -> None:
    canvas.saveState()
    y = 22 * mm
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.8)
    canvas.line(MARGIN, y, A4[0] - MARGIN, y)
    canvas.setFont(REGULAR, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, y - 10, "This is a computer-generated invoice.")
    canvas.drawString(MARGIN, y - 20, _latin(f"Thank you for shopping with {seller['name']}."))
    sign_x = A4[0] - MARGIN - 48 * mm
    canvas.setStrokeColor(colors.HexColor("#1b2433"))
    canvas.line(sign_x, y - 8, A4[0] - MARGIN, y - 8)
    canvas.drawCentredString(sign_x + 24 * mm, y - 18, "Authorised signatory")
    canvas.restoreState()


def _stamp(canvas, text: str) -> None:
    canvas.saveState()
    canvas.translate(A4[0] - 62 * mm, A4[1] - 55 * mm)
    canvas.rotate(-8)
    canvas.setStrokeColor(colors.HexColor("#c62828"))
    canvas.setFillColor(colors.HexColor("#c62828"))
    canvas.setLineWidth(2)
    canvas.setFont(BOLD, 13)
    width = canvas.stringWidth(text.upper(), BOLD, 13) + 14
    canvas.roundRect(-width / 2, -9, width, 22, 4, stroke=1, fill=0)
    canvas.drawCentredString(0, -1, text.upper())
    canvas.restoreState()
