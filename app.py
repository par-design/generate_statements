#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════════════
  Relevé de compte PDF — DigitalOcean App Platform
  APFFQ — Design V2 Final
  API REST pour Make.com / QuickBooks Online
══════════════════════════════════════════════════════════════

Endpoints:
  POST /generate-statement       → PDF depuis données pré-calculées
  POST /generate-statement-raw   → PDF depuis données brutes QuickBooks
  GET  /health                   → Health check
"""

import os
import io
import json
import base64
import tempfile
import logging
from datetime import datetime

from flask import Flask, request, send_file, jsonify
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor, white, black
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Logging ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Flask App ───────────────────────────────────────────
app = Flask(__name__)

# ── Register Poppins fonts ──────────────────────────────
FONT_DIR = "/usr/share/fonts/truetype/poppins"
FONTS_LOADED = False

def F(name):
    """Retourne le nom de police, avec fallback Helvetica si Poppins pas dispo."""
    if FONTS_LOADED:
        return name
    mapping = {
        'Poppins': 'Helvetica',
        'Poppins-Bold': 'Helvetica-Bold',
        'Poppins-Medium': 'Helvetica',
        'Poppins-Light': 'Helvetica',
    }
    return mapping.get(name, 'Helvetica')

try:
    pdfmetrics.registerFont(TTFont('Poppins', f'{FONT_DIR}/Poppins-Regular.ttf'))
    pdfmetrics.registerFont(TTFont('Poppins-Bold', f'{FONT_DIR}/Poppins-Bold.ttf'))
    pdfmetrics.registerFont(TTFont('Poppins-Medium', f'{FONT_DIR}/Poppins-Medium.ttf'))
    pdfmetrics.registerFont(TTFont('Poppins-Light', f'{FONT_DIR}/Poppins-Light.ttf'))
    FONTS_LOADED = True
    logger.info("Polices Poppins chargées")
except Exception as e:
    logger.warning(f"Fallback Helvetica: {e}")

# ── APFFQ Colors ────────────────────────────────────────
DARK_RED = HexColor("#9F2842")
DARKER_RED = HexColor("#691C32")
RED = HexColor("#E45D30")
PINK = HexColor("#FADDD2")
WHITE_PINK = HexColor("#FFF3EF")
WHITE = HexColor("#FFFFFF")
TEXT_DARK = HexColor("#2D2D2D")
TEXT_GRAY = HexColor("#6B6B6B")
GRID_COLOR = HexColor("#F0C0B0")

# ── Config taxes Québec ─────────────────────────────────
TPS_RATE = 5.0
TVQ_RATE = 9.975
COMBINED_RATE = TPS_RATE + TVQ_RATE
FRAIS_RETARD_ITEM_ID = "18"

# ── Layout constants ────────────────────────────────────
ML = 35           # Marge gauche
MR = 35           # Marge droite
RADIUS = 8        # Coins arrondis


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def fmt_money(val):
    return f"{val:,.2f}".replace(",", " ") + " $"


def draw_rounded_rect(cv, x, y, width, height, radius, fill_color, stroke_color=None, stroke_width=0.5):
    cv.saveState()
    cv.setFillColor(fill_color)
    if stroke_color:
        cv.setStrokeColor(stroke_color)
        cv.setLineWidth(stroke_width)
    else:
        cv.setStrokeColor(fill_color)
    p = cv.beginPath()
    p.roundRect(x, y, width, height, radius)
    p.close()
    cv.drawPath(p, fill=1, stroke=1 if stroke_color else 0)
    cv.restoreState()


def draw_rounded_table(cv, table, x, y_top, table_width, radius, border_color, border_width=1):
    """Dessine un tableau avec des coins arrondis via clip path."""
    tw, th = table.wrap(table_width, 400)
    bot_y = y_top - th

    # Clip arrondi
    cv.saveState()
    clip = cv.beginPath()
    clip.roundRect(x - 1, bot_y - 1, table_width + 2, th + 2, radius)
    clip.close()
    cv.clipPath(clip, stroke=0)
    table.drawOn(cv, x, bot_y)
    cv.restoreState()

    # Contour arrondi
    cv.saveState()
    cv.setStrokeColor(border_color)
    cv.setLineWidth(border_width)
    p = cv.beginPath()
    p.roundRect(x - 0.5, bot_y - 0.5, table_width + 1, th + 1, radius)
    p.close()
    cv.drawPath(p, fill=0, stroke=1)
    cv.restoreState()

    return bot_y


# ═══════════════════════════════════════════════════════════
# TRAITEMENT DES DONNÉES QUICKBOOKS
# ═══════════════════════════════════════════════════════════

def process_raw_invoices(raw_invoices, frais_retard_item_id=FRAIS_RETARD_ITEM_ID):
    processed = []
    for inv in raw_invoices:
        frais_retard = 0.0
        montant_services = 0.0

        for line in inv.get("Line", []):
            if line.get("DetailType") == "SubTotalLineDetail":
                continue
            if line.get("DetailType") == "SalesItemLineDetail":
                item_id = str(line.get("SalesItemLineDetail", {}).get("ItemRef", {}).get("value", ""))
                amount = float(line.get("Amount", 0))
                if item_id == str(frais_retard_item_id):
                    frais_retard += amount
                else:
                    montant_services += amount

        total_tax = float(inv.get("TxnTaxDetail", {}).get("TotalTax", 0))
        if total_tax > 0 and COMBINED_RATE > 0:
            tps = round(total_tax * TPS_RATE / COMBINED_RATE, 2)
            tvq = round(total_tax - tps, 2)
        else:
            tps = tvq = 0.0

        txn_date = inv.get("TxnDate", "")
        if "T" in txn_date:
            try:
                dt = datetime.fromisoformat(txn_date.replace("Z", "+00:00"))
                formatted_date = dt.strftime("%d-%m-%Y")
            except (ValueError, TypeError):
                formatted_date = txn_date[:10]
        else:
            formatted_date = txn_date

        processed.append({
            "date": formatted_date,
            "invoice_number": inv.get("DocNumber", "—"),
            "amount": round(montant_services, 2),
            "interest": round(frais_retard, 2),
            "tps": tps, "tvq": tvq,
            "total": float(inv.get("TotalAmt", 0)),
        })
    return processed


def calculate_aging(raw_invoices):
    now = datetime.now()
    buckets = [0.0, 0.0, 0.0, 0.0, 0.0]
    for inv in raw_invoices:
        balance = float(inv.get("TotalAmt", 0))
        if balance <= 0:
            continue
        due_date_str = inv.get("DueDate", "")
        if not due_date_str:
            buckets[0] += balance
            continue
        try:
            if "T" in due_date_str:
                due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00")).replace(tzinfo=None)
            else:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            buckets[0] += balance
            continue
        days = (now - due_date).days
        if days <= 0:
            buckets[0] += balance
        elif days <= 30:
            buckets[1] += balance
        elif days <= 60:
            buckets[2] += balance
        elif days <= 90:
            buckets[3] += balance
        else:
            buckets[4] += balance

    return [fmt_money(b) if b > 0 else "—" for b in buckets]


# ═══════════════════════════════════════════════════════════
# GÉNÉRATION PDF — DESIGN V2 FINAL APFFQ
# ═══════════════════════════════════════════════════════════

def generate_statement_pdf(data, invoices):
    w, h = letter
    CW = w - ML - MR
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

    # ── Extraire les paramètres ──────────────────────────
    company_name = data.get("company_name", "Entreprise Inc.")
    company_address = data.get("company_address", "")
    company_phone = data.get("company_phone", "")
    company_email = data.get("company_email", "")
    company_tps = data.get("company_tps", "")
    company_tvq = data.get("company_tvq", "")
    customer_name = data.get("customer_name", "Client")
    customer_producer_name = data.get("customer_producer_name", "")
    customer_address = data.get("customer_address", "")
    customer_member_number = data.get("customer_member_number", "—")
    statement_date = data.get("statement_date", datetime.now().strftime("%d-%m-%Y"))
    period_start = data.get("period_start", "01-01-2025")
    period_end = data.get("period_end", datetime.now().strftime("%d-%m-%Y"))
    message_footer = data.get("message_footer",
        "Merci de votre confiance. Veuillez effectuer votre paiement dans les meilleurs délais.")
    aging = data.get("aging", ["—", "—", "—", "—", "—"])

    # ── Logo ─────────────────────────────────────────────
    logo_tmp_path = None
    logo_base64 = data.get("logo_base64", None)
    if logo_base64:
        try:
            logo_bytes = base64.b64decode(logo_base64)
            logo_tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            logo_tmp.write(logo_bytes)
            logo_tmp.close()
            logo_tmp_path = logo_tmp.name
        except Exception as e:
            logger.warning(f"Logo base64 invalide: {e}")

    # Fallback: logo par défaut inclus dans le container
    DEFAULT_LOGO = "logo.png" if os.path.exists("logo.png") else "/app/logo.png"
    if not logo_tmp_path and os.path.exists(DEFAULT_LOGO):
        logo_tmp_path = DEFAULT_LOGO

    # ── Totaux ───────────────────────────────────────────
    total_amount = sum(inv["amount"] for inv in invoices)
    total_interest = sum(inv["interest"] for inv in invoices)
    total_tps = sum(inv["tps"] for inv in invoices)
    total_tvq = sum(inv["tvq"] for inv in invoices)
    grand_total = sum(inv["total"] for inv in invoices)

    # ═════════════════════════════════════════════════════
    # HEADER
    # ═════════════════════════════════════════════════════
    header_h = 110
    c.setFillColor(white)
    c.rect(0, h - header_h, w, header_h, fill=1, stroke=0)
    c.setFillColor(RED)
    c.rect(0, h - header_h, w, 2, fill=1, stroke=0)

    if logo_tmp_path and os.path.exists(logo_tmp_path):
        try:
            c.drawImage(logo_tmp_path, ML, h - header_h + 18, width=65, height=54,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    name_lines = company_name.split("\n") if "\n" in company_name else [company_name]
    c.setFont(F("Poppins-Bold"), 10)
    c.setFillColor(DARKER_RED)
    y_name = h - 45
    for nl in name_lines:
        c.drawString(110, y_name, nl.strip())
        y_name -= 13

    c.setFont(F("Poppins-Medium"), 7)
    c.setFillColor(TEXT_DARK)
    addr_line = company_address.replace("\n", ", ")
    c.drawString(110, h - 73, f"{addr_line}  |  Tél: {company_phone}")
    c.drawString(110, h - 84, f"{company_email}  |  TPS: {company_tps}  |  TVQ: {company_tvq}")

    c.setFont(F("Poppins-Bold"), 22)
    c.setFillColor(DARKER_RED)
    c.drawRightString(w - MR, h - 55, "RELEVÉ DE")
    c.drawRightString(w - MR, h - 80, "COMPTE")

    # ═════════════════════════════════════════════════════
    # BANDE INFO
    # ═════════════════════════════════════════════════════
    y_info = h - header_h - 40
    draw_rounded_rect(c, ML, y_info - 8, CW, 38, RADIUS, white, PINK, 1)
    c.setFont(F("Poppins-Medium"), 8)
    c.setFillColor(DARKER_RED)
    c.drawString(ML + 15, y_info + 8, f"Date: {statement_date}")
    c.drawCentredString(w / 2, y_info + 8, f"Période: {period_start} au {period_end}")
    c.drawRightString(w - MR - 15, y_info + 8, f"No. membre: {customer_member_number}")

    # ═════════════════════════════════════════════════════
    # CLIENT + CARTE RÉSUMÉ
    # ═════════════════════════════════════════════════════
    y_section = y_info - 45

    # Client (gauche)
    c.setFont(F("Poppins-Bold"), 8)
    c.setFillColor(RED)
    c.drawString(ML + 10, y_section, "FACTURER À")
    c.setStrokeColor(RED)
    c.setLineWidth(2.5)
    # Adjust sidebar line height if producer name is present
    sidebar_top = y_section + 8
    sidebar_bot = y_section - 60 if customer_producer_name else y_section - 48
    c.line(ML + 5, sidebar_bot, ML + 5, sidebar_top)
    y_billing = y_section - 14
    if customer_producer_name:
        c.setFont(F("Poppins-Medium"), 8.5)
        c.setFillColor(TEXT_GRAY)
        c.drawString(ML + 15, y_billing, customer_producer_name)
        y_billing -= 14
    c.setFont(F("Poppins-Bold"), 10)
    c.setFillColor(TEXT_DARK)
    c.drawString(ML + 15, y_billing, customer_name)
    c.setFont(F("Poppins-Light"), 8.5)
    c.setFillColor(TEXT_GRAY)
    y_a = y_billing - 14
    for line in customer_address.split("\n"):
        c.drawString(ML + 15, y_a, line.strip())
        y_a -= 12

    # Carte résumé (droite)
    card_w = 250
    card_h = 115
    card_x = w - MR - card_w
    card_y = y_section - card_h + 18

    draw_rounded_rect(c, card_x, card_y, card_w, card_h, RADIUS, white, DARK_RED, 1)

    # Border under title
    c.setStrokeColor(DARK_RED)
    c.setLineWidth(1)
    c.line(card_x, card_y + card_h - 28, card_x + card_w, card_y + card_h - 28)

    c.setFont(F("Poppins-Bold"), 9)
    c.setFillColor(DARKER_RED)
    c.drawCentredString(card_x + card_w / 2, card_y + card_h - 20, "RÉSUMÉ DU COMPTE")

    y_line = card_y + card_h - 42
    for label, val in [
        ("Sous-total services", fmt_money(total_amount)),
        ("Frais de retard", fmt_money(total_interest)),
        ("TPS", fmt_money(total_tps)),
        ("TVQ", fmt_money(total_tvq)),
    ]:
        c.setFont(F("Poppins-Light"), 8)
        c.setFillColor(TEXT_GRAY)
        c.drawString(card_x + 15, y_line, label)
        c.setFont(F("Poppins-Medium"), 8)
        c.setFillColor(TEXT_DARK)
        c.drawRightString(card_x + card_w - 15, y_line, val)
        y_line -= 14

    c.setStrokeColor(RED)
    c.setLineWidth(1)
    c.line(card_x + 15, y_line + 6, card_x + card_w - 15, y_line + 6)

    c.setFont(F("Poppins-Bold"), 12)
    c.setFillColor(DARKER_RED)
    c.drawString(card_x + 15, y_line - 8, "TOTAL DÛ")
    c.drawRightString(card_x + card_w - 15, y_line - 8, fmt_money(grand_total))

    # ═════════════════════════════════════════════════════
    # TABLEAU DES FACTURES
    # ═════════════════════════════════════════════════════
    y_table = card_y - 25

    headers = ["Date", "# Facture", "Montant\nfacture", "Frais de\nretard", "TPS", "TVQ", "Total"]
    h_style = ParagraphStyle('h', fontName=F('Poppins-Bold'), fontSize=7.5, textColor=DARKER_RED, alignment=TA_CENTER, leading=9.5)
    c_right = ParagraphStyle('cr', fontName=F('Poppins'), fontSize=8, textColor=TEXT_DARK, alignment=TA_RIGHT, leading=11)
    c_center = ParagraphStyle('cc', fontName=F('Poppins'), fontSize=8, textColor=TEXT_DARK, alignment=TA_CENTER, leading=11)
    t_style = ParagraphStyle('ts', fontName=F('Poppins-Bold'), fontSize=8.5, textColor=DARKER_RED, alignment=TA_RIGHT, leading=11)
    t_label = ParagraphStyle('tl', fontName=F('Poppins-Bold'), fontSize=9, textColor=DARKER_RED, alignment=TA_CENTER, leading=11)

    tdata = [[Paragraph(hh.replace("\n", "<br/>"), h_style) for hh in headers]]
    for inv in invoices:
        tdata.append([
            Paragraph(inv["date"], c_center), Paragraph(str(inv["invoice_number"]), c_center),
            Paragraph(fmt_money(inv["amount"]), c_right), Paragraph(fmt_money(inv["interest"]), c_right),
            Paragraph(fmt_money(inv["tps"]), c_right), Paragraph(fmt_money(inv["tvq"]), c_right),
            Paragraph(fmt_money(inv["total"]), c_right),
        ])
    tdata.append([
        Paragraph("", t_style), Paragraph("TOTAL", t_label),
        Paragraph(fmt_money(total_amount), t_style), Paragraph(fmt_money(total_interest), t_style),
        Paragraph(fmt_money(total_tps), t_style), Paragraph(fmt_money(total_tvq), t_style),
        Paragraph(fmt_money(grand_total), t_style),
    ])

    base = [70, 66, 80, 92, 70, 70, 80]
    base_total = sum(base)
    col_w = [round(v / base_total * CW) for v in base]
    col_w[-1] = CW - sum(col_w[:-1])
    nr = len(tdata)

    table = Table(tdata, colWidths=col_w, repeatRows=1)
    table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, 0), 8), ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 1), (-1, -2), 7), ('BOTTOMPADDING', (0, 1), (-1, -2), 7),
        ('LINEABOVE', (0, -1), (-1, -1), 2, RED),
        ('TOPPADDING', (0, -1), (-1, -1), 10), ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
        ('GRID', (0, 1), (-1, -1), 0.25, GRID_COLOR),
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, RED),
    ]))

    table_bot = draw_rounded_table(c, table, ML, y_table, CW, RADIUS, DARK_RED)

    # ═════════════════════════════════════════════════════
    # ANCIENNETÉ DES COMPTES
    # ═════════════════════════════════════════════════════
    y_ag = table_bot - 25
    c.setFont(F("Poppins-Bold"), 8)
    c.setFillColor(DARKER_RED)
    c.drawString(ML, y_ag, "SOMMAIRE DE L'ANCIENNETÉ DES COMPTES")
    y_ag -= 5

    ag_h = ["Courant", "1-30 jours", "31-60 jours", "61-90 jours", "90+ jours"]
    ag_col = CW / 5
    ag_t = Table([ag_h, aging], colWidths=[ag_col] * 5)
    ag_t.setStyle(TableStyle([
        ('TEXTCOLOR', (0, 0), (-1, 0), DARKER_RED),
        ('FONTNAME', (0, 0), (-1, 0), F('Poppins-Bold')), ('FONTSIZE', (0, 0), (-1, 0), 7.5),
        ('FONTNAME', (0, 1), (-1, 1), F('Poppins-Medium')), ('FONTSIZE', (0, 1), (-1, 1), 8.5),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.3, PINK),
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, RED),
    ]))

    ag_bot = draw_rounded_table(c, ag_t, ML, y_ag, CW, RADIUS, DARK_RED, 0.8)

    # ═════════════════════════════════════════════════════
    # FOOTER
    # ═════════════════════════════════════════════════════
    y_f = ag_bot - 28
    c.setFont(F("Poppins-Light"), 7.5)
    c.setFillColor(TEXT_GRAY)
    c.drawCentredString(w / 2, y_f, message_footer)

    c.setFillColor(DARKER_RED)
    c.rect(0, 0, w, 6, fill=1, stroke=0)
    c.setFont(F("Poppins-Light"), 6.5)
    c.setFillColor(TEXT_GRAY)
    c.drawCentredString(w / 2, 12,
        f"Généré le {datetime.now().strftime('%d-%m-%Y à %H:%M')}"
        " — Ce document est un relevé de compte et non une facture.")

    c.save()

    if logo_tmp_path and logo_tmp_path != DEFAULT_LOGO and os.path.exists(logo_tmp_path):
        os.unlink(logo_tmp_path)

    buffer.seek(0)
    return buffer


# ═══════════════════════════════════════════════════════════
# ROUTES FLASK
# ═══════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "statement-generator", "version": "2.0"}), 200


@app.route("/generate-statement", methods=["POST"])
def generate_statement():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "Corps JSON vide."}), 400

        invoices = data.get("invoices", [])
        if not invoices:
            return jsonify({"error": "Aucune facture fournie."}), 400

        for i, inv in enumerate(invoices):
            for key in ("date", "invoice_number", "amount", "interest", "tps", "tvq", "total"):
                if key not in inv:
                    return jsonify({"error": f"Facture {i}: champ '{key}' manquant."}), 400
            for key in ("amount", "interest", "tps", "tvq", "total"):
                inv[key] = float(inv[key])

        logger.info(f"[generate-statement] {data.get('customer_name')} — {len(invoices)} facture(s)")
        pdf_buffer = generate_statement_pdf(data, invoices)
        customer = data.get("customer_name", "client").replace(" ", "_")
        filename = f"releve_{customer}_{datetime.now().strftime('%Y%m%d')}.pdf"
        return send_file(pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)

    except Exception as e:
        logger.error(f"Erreur: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/generate-statement-raw", methods=["POST"])
def generate_statement_raw():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "Corps JSON vide."}), 400

        raw_invoices = data.get("raw_invoices", [])

        # ── Support base64 (solution pour Make.com JSON String) ──
        raw_b64 = data.get("raw_invoices_base64", None)
        if raw_b64:
            try:
                decoded = base64.b64decode(raw_b64).decode("utf-8")
                logger.info(f"[raw] base64 décodé, longueur: {len(decoded)}, début: {decoded[:100]}...")

                # Nettoyer le format Make.com pour le rendre JSON-compatible
                import re
                cleaned = decoded
                # Remplacer None → null, True → true, False → false
                cleaned = re.sub(r'\bNone\b', 'null', cleaned)
                cleaned = re.sub(r'\bTrue\b', 'true', cleaned)
                cleaned = re.sub(r'\bFalse\b', 'false', cleaned)
                # Remplacer guillemets simples par doubles (attention aux apostrophes dans le texte)
                # Stratégie : remplacer ' par " seulement aux positions clés JSON
                # D'abord essayer json.loads directement
                try:
                    raw_invoices = json.loads(cleaned)
                except json.JSONDecodeError:
                    # Remplacer les guillemets simples utilisés comme délimiteurs JSON
                    # Pattern: début de valeur, clés, etc.
                    cleaned = cleaned.replace("'", '"')
                    # Corriger les apostrophes dans le texte qui ont été cassées
                    # Ex: "l"Agriculture" → "l'Agriculture"
                    # On ne peut pas tout corriger, mais on essaie le parse
                    try:
                        raw_invoices = json.loads(cleaned)
                    except json.JSONDecodeError:
                        # Dernier recours: ast.literal_eval sur le décodé original
                        import ast
                        raw_invoices = ast.literal_eval(decoded)

                logger.info(f"[raw] raw_invoices décodé depuis base64: {type(raw_invoices)}")
            except Exception as e:
                logger.error(f"[raw] Échec décodage base64: {e}, contenu: {decoded[:200] if 'decoded' in dir() else 'N/A'}")
                return jsonify({"error": f"raw_invoices_base64 invalide: {str(e)}"}), 400

        # ── Normaliser raw_invoices ──────────────────────
        # Make.com peut envoyer plusieurs formats selon le module :
        #
        # 1. Un tableau de factures directement : [{facture1}, {facture2}]
        # 2. Une string JSON/Python : "[{...}]"
        # 3. Un dict unique (1 seule facture) : {facture}
        # 4. Le format Array Aggregator de Make.com :
        #    [{"__IMTKEY__": "123", "array": [{facture1}, {facture2}]}]
        #    ou [{facture1_avec_champs_client}, {facture2_avec_champs_client}]

        # Si c'est une string, tenter de parser
        if isinstance(raw_invoices, str):
            try:
                raw_invoices = json.loads(raw_invoices)
            except (json.JSONDecodeError, TypeError):
                try:
                    import ast
                    raw_invoices = ast.literal_eval(raw_invoices)
                except Exception as e2:
                    return jsonify({"error": f"raw_invoices format invalide: {str(e2)}"}), 400

        # Si c'est un dict unique, le mettre dans une liste
        if isinstance(raw_invoices, dict):
            raw_invoices = [raw_invoices]

        # ── Make.com Data Structure envoie un tableau de STRINGS JSON ──
        # Ex: ["{\"Id\":\"642\"...}", "{\"Id\":\"643\"...}"]
        # Il faut parser chaque string individuellement
        if raw_invoices and isinstance(raw_invoices, list):
            if isinstance(raw_invoices[0], str):
                parsed = []
                for item_str in raw_invoices:
                    try:
                        parsed.append(json.loads(item_str))
                    except (json.JSONDecodeError, TypeError):
                        parsed.append(item_str)  # garder tel quel si ça échoue
                raw_invoices = parsed

        # Si c'est le format Array Aggregator: [{"__IMTKEY__": ..., "array": [...]}]
        # Extraire les factures du sous-tableau "array"
        if raw_invoices and isinstance(raw_invoices, list):
            extracted = []
            for item in raw_invoices:
                if isinstance(item, dict) and "array" in item:
                    # Format aggregator: extraire le sous-tableau
                    sub = item["array"]
                    if isinstance(sub, list):
                        extracted.extend(sub)
                    elif isinstance(sub, dict):
                        extracted.append(sub)
                elif isinstance(item, dict) and "Line" in item:
                    # Format direct: c'est déjà une facture
                    extracted.append(item)
                elif isinstance(item, dict):
                    # Objet inconnu mais on essaie quand même
                    extracted.append(item)
            if extracted:
                raw_invoices = extracted

        if not raw_invoices:
            return jsonify({"error": "Aucune facture dans 'raw_invoices'."}), 400

        logger.info(f"[raw] {data.get('customer_name', '?')} — {len(raw_invoices)} facture(s) — "
                     f"Premier DocNumber: {raw_invoices[0].get('DocNumber', '?') if raw_invoices else '?'}")

        frais_retard_id = data.get("frais_retard_item_id", FRAIS_RETARD_ITEM_ID)
        invoices = process_raw_invoices(raw_invoices, frais_retard_id)
        data["aging"] = calculate_aging(raw_invoices)

        # Auto-extraire le nom du producteur depuis la première facture si non fourni
        if not data.get("customer_producer_name") and raw_invoices:
            first = raw_invoices[0]
            given = first.get("GivenName", "") or ""
            family = first.get("FamilyName", "") or ""
            producer_name = " ".join(p for p in [given, family] if p).strip()
            if producer_name:
                data["customer_producer_name"] = producer_name

        pdf_buffer = generate_statement_pdf(data, invoices)
        customer = data.get("customer_name", "client").replace(" ", "_")
        filename = f"releve_{customer}_{datetime.now().strftime('%Y%m%d')}.pdf"
        return send_file(pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)

    except Exception as e:
        logger.error(f"Erreur: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
