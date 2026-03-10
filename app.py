#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════════════
  Relevé de compte PDF — Google Cloud Run
  API REST pour Make.com / QuickBooks Online
══════════════════════════════════════════════════════════════

Endpoints:
  POST /generate-statement   → Génère et retourne un PDF
  GET  /health               → Health check

Déploiement:
  gcloud run deploy statement-generator \
    --source . \
    --region northamerica-northeast1 \
    --allow-unauthenticated
"""

import os
import io
import base64
import tempfile
import logging
from datetime import datetime

from flask import Flask, request, send_file, jsonify
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch, mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

# ── Logging ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Flask App ───────────────────────────────────────────
app = Flask(__name__)

# ── Colors ──────────────────────────────────────────────
PRIMARY = HexColor("#1a365d")
ACCENT = HexColor("#2b6cb0")
LIGHT_BG = HexColor("#ebf4ff")
HEADER_BG = HexColor("#1a365d")
ROW_ALT = HexColor("#f0f7ff")
BORDER = HexColor("#bee3f8")
TEXT_DARK = HexColor("#1a202c")
TEXT_GRAY = HexColor("#718096")
TOTAL_BG = HexColor("#ebf8ff")


# ═══════════════════════════════════════════════════════════
# GÉNÉRATION PDF
# ═══════════════════════════════════════════════════════════

def generate_statement_pdf(data):
    """
    Génère un relevé de compte PDF en mémoire et retourne le buffer.

    Paramètres attendus dans `data` (dict):
    ─────────────────────────────────────────
    company_name          str   Nom de l'entreprise
    company_address       str   Adresse (\\n pour séparer les lignes)
    company_phone         str   Téléphone
    company_email         str   Courriel
    company_tps           str   Numéro TPS
    company_tvq           str   Numéro TVQ
    customer_name         str   Nom du client
    customer_address      str   Adresse du client
    customer_member_number str  Numéro de membre (optionnel)
    statement_date        str   Date du relevé (DD-MM-YYYY)
    period_start          str   Début de période (DD-MM-YYYY)
    period_end            str   Fin de période (DD-MM-YYYY)
    message_footer        str   Message en bas du relevé (optionnel)
    logo_base64           str   Logo en base64 (optionnel, PNG/JPG)
    invoices              list  Liste de factures (voir ci-dessous)

    Structure de chaque facture dans `invoices`:
    ─────────────────────────────────────────────
    date            str    Date de la facture (DD-MM-YYYY)
    invoice_number  str    Numéro de facture
    amount          float  Montant de la facture (hors frais de retard)
    interest        float  Frais de retard (Line Item 18 de QuickBooks)
    tps             float  Montant TPS
    tvq             float  Montant TVQ
    total           float  Total de la facture (TotalAmt de QuickBooks)
    """

    # ── Extraire les paramètres avec valeurs par défaut ──
    company_name = data.get("company_name", "Entreprise Inc.")
    company_address = data.get("company_address", "")
    company_phone = data.get("company_phone", "")
    company_email = data.get("company_email", "")
    company_tps = data.get("company_tps", "")
    company_tvq = data.get("company_tvq", "")
    customer_name = data.get("customer_name", "Client")
    customer_address = data.get("customer_address", "")
    customer_member_number = data.get("customer_member_number", "—")
    statement_date = data.get("statement_date", datetime.now().strftime("%d-%m-%Y"))
    period_start = data.get("period_start", "01-01-2025")
    period_end = data.get("period_end", datetime.now().strftime("%d-%m-%Y"))
    message_footer = data.get(
        "message_footer",
        "Merci de votre confiance. Veuillez effectuer votre paiement dans les meilleurs délais."
    )
    logo_base64 = data.get("logo_base64", None)
    invoices = data.get("invoices", [])

    # ── Valider les factures ─────────────────────────────
    if not invoices:
        raise ValueError("Aucune facture fournie dans 'invoices'.")

    for i, inv in enumerate(invoices):
        for key in ("date", "invoice_number", "amount", "interest", "tps", "tvq", "total"):
            if key not in inv:
                raise ValueError(f"Facture {i}: champ '{key}' manquant.")
        # Convertir en float si nécessaire
        for key in ("amount", "interest", "tps", "tvq", "total"):
            inv[key] = float(inv[key])

    # ── Préparer le logo (si base64 fourni) ──────────────
    logo_tmp_path = None
    if logo_base64:
        try:
            logo_bytes = base64.b64decode(logo_base64)
            logo_tmp = tempfile.NamedTemporaryFile(
                suffix=".png", delete=False
            )
            logo_tmp.write(logo_bytes)
            logo_tmp.close()
            logo_tmp_path = logo_tmp.name
        except Exception as e:
            logger.warning(f"Impossible de décoder le logo base64: {e}")
            logo_tmp_path = None

    # ── Créer le PDF en mémoire ──────────────────────────
    buffer = io.BytesIO()
    w, h = letter
    c = canvas.Canvas(buffer, pagesize=letter)

    # ── Helpers ──────────────────────────────────────────
    def fmt_money(val):
        formatted = f"{val:,.2f}".replace(",", " ")
        return f"{formatted} $"

    def draw_rounded_rect(cv, x, y, width, height, radius, fill_color, stroke_color=None):
        cv.saveState()
        cv.setFillColor(fill_color)
        if stroke_color:
            cv.setStrokeColor(stroke_color)
            cv.setLineWidth(0.5)
        else:
            cv.setStrokeColor(fill_color)
        p = cv.beginPath()
        p.roundRect(x, y, width, height, radius)
        p.close()
        cv.drawPath(p, fill=1, stroke=1 if stroke_color else 0)
        cv.restoreState()

    # ═════════════════════════════════════════════════════
    # DESSIN DU PDF
    # ═════════════════════════════════════════════════════

    # ── Barre accent en haut ─────────────────────────────
    c.setFillColor(PRIMARY)
    c.rect(0, h - 8, w, 8, fill=1, stroke=0)

    # ── Logo / Nom entreprise ────────────────────────────
    y_top = h - 50

    if logo_tmp_path and os.path.exists(logo_tmp_path):
        try:
            c.drawImage(
                logo_tmp_path, 40, y_top - 45,
                width=140, height=45,
                preserveAspectRatio=True, mask='auto'
            )
        except Exception:
            c.setFont("Helvetica-Bold", 16)
            c.setFillColor(PRIMARY)
            c.drawString(40, y_top - 20, company_name)
    else:
        c.setFont("Helvetica-Bold", 16)
        c.setFillColor(PRIMARY)
        c.drawString(40, y_top - 20, company_name)

    # ── Titre "RELEVÉ DE COMPTE" ─────────────────────────
    title_text = "RELEVÉ DE COMPTE"
    c.setFont("Helvetica-Bold", 20)
    title_w = c.stringWidth(title_text, "Helvetica-Bold", 20)

    draw_rounded_rect(c, w - title_w - 70, y_top - 35, title_w + 40, 36, 6, PRIMARY)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(w - title_w - 50, y_top - 27, title_text)

    # ── Infos entreprise (gauche) ────────────────────────
    y_info = y_top - 70
    c.setFont("Helvetica", 8.5)
    c.setFillColor(TEXT_GRAY)

    for line in company_address.split("\n"):
        c.drawString(40, y_info, line.strip())
        y_info -= 12
    if company_phone:
        c.drawString(40, y_info, f"Tél: {company_phone}")
        y_info -= 12
    if company_email:
        c.drawString(40, y_info, f"Courriel: {company_email}")
        y_info -= 14
    c.setFont("Helvetica", 7.5)
    if company_tps:
        c.drawString(40, y_info, f"TPS: {company_tps}")
        y_info -= 10
    if company_tvq:
        c.drawString(40, y_info, f"TVQ: {company_tvq}")

    # ── Détails du relevé (droite) ───────────────────────
    details_x = w - 230
    y_det = y_top - 65

    draw_rounded_rect(c, details_x - 15, y_det - 58, 220, 72, 6, LIGHT_BG, BORDER)

    labels = [
        ("Date du relevé:", statement_date),
        ("Période:", f"{period_start} au {period_end}"),
        ("No. membre:", customer_member_number),
    ]

    for label, value in labels:
        c.setFont("Helvetica", 8)
        c.setFillColor(TEXT_GRAY)
        c.drawString(details_x, y_det, label)
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColor(TEXT_DARK)
        c.drawString(details_x + 85, y_det, str(value))
        y_det -= 16

    # ── Boîte client ─────────────────────────────────────
    y_cust = y_top - 162

    draw_rounded_rect(c, 40, y_cust - 52, w - 80, 62, 6, LIGHT_BG, BORDER)

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(ACCENT)
    c.drawString(55, y_cust, "FACTURER À:")

    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(TEXT_DARK)
    c.drawString(140, y_cust, customer_name)

    c.setFont("Helvetica", 9)
    c.setFillColor(TEXT_GRAY)
    y_addr = y_cust - 14
    for line in customer_address.split("\n"):
        c.drawString(140, y_addr, line.strip())
        y_addr -= 13

    # ═════════════════════════════════════════════════════
    # TABLEAU DES FACTURES
    # ═════════════════════════════════════════════════════

    y_table_top = y_cust - 78

    headers = [
        "Date", "# Facture", "Montant\nfacture",
        "Frais de\nretard", "TPS", "TVQ", "Total"
    ]

    # ── Styles des cellules ──────────────────────────────
    header_style = ParagraphStyle(
        'header', fontName='Helvetica-Bold', fontSize=8,
        textColor=white, alignment=TA_CENTER, leading=10
    )
    cell_right = ParagraphStyle(
        'cell_right', fontName='Helvetica', fontSize=8.5,
        textColor=TEXT_DARK, alignment=TA_RIGHT, leading=11
    )
    cell_center = ParagraphStyle(
        'cell_center', fontName='Helvetica', fontSize=8.5,
        textColor=TEXT_DARK, alignment=TA_CENTER, leading=11
    )
    total_style = ParagraphStyle(
        'total', fontName='Helvetica-Bold', fontSize=9,
        textColor=PRIMARY, alignment=TA_RIGHT, leading=11
    )
    total_label = ParagraphStyle(
        'total_label', fontName='Helvetica-Bold', fontSize=9.5,
        textColor=PRIMARY, alignment=TA_CENTER, leading=11
    )

    # ── Construire les données du tableau ────────────────
    formatted_data = []

    # En-tête
    formatted_data.append([
        Paragraph(h_text.replace("\n", "<br/>"), header_style)
        for h_text in headers
    ])

    # Lignes de factures
    for inv in invoices:
        formatted_data.append([
            Paragraph(inv["date"], cell_center),
            Paragraph(str(inv["invoice_number"]), cell_center),
            Paragraph(fmt_money(inv["amount"]), cell_right),
            Paragraph(fmt_money(inv["interest"]), cell_right),
            Paragraph(fmt_money(inv["tps"]), cell_right),
            Paragraph(fmt_money(inv["tvq"]), cell_right),
            Paragraph(fmt_money(inv["total"]), cell_right),
        ])

    # Totaux
    total_amount = sum(inv["amount"] for inv in invoices)
    total_interest = sum(inv["interest"] for inv in invoices)
    total_tps = sum(inv["tps"] for inv in invoices)
    total_tvq = sum(inv["tvq"] for inv in invoices)
    grand_total = sum(inv["total"] for inv in invoices)

    formatted_data.append([
        Paragraph("", total_style),
        Paragraph("TOTAL", total_label),
        Paragraph(fmt_money(total_amount), total_style),
        Paragraph(fmt_money(total_interest), total_style),
        Paragraph(fmt_money(total_tps), total_style),
        Paragraph(fmt_money(total_tvq), total_style),
        Paragraph(fmt_money(grand_total), total_style),
    ])

    # ── Créer et styliser le tableau ─────────────────────
    col_widths = [72, 68, 82, 95, 72, 72, 82]
    total_table_w = sum(col_widths)
    table_x = (w - total_table_w) / 2
    num_rows = len(formatted_data)

    table = Table(formatted_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        # En-tête
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
        # Données
        ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -2), 8.5),
        ('TOPPADDING', (0, 1), (-1, -2), 7),
        ('BOTTOMPADDING', (0, 1), (-1, -2), 7),
        ('VALIGN', (0, 1), (-1, -1), 'MIDDLE'),
        # Lignes alternées
        *[('BACKGROUND', (0, i), (-1, i), ROW_ALT) for i in range(2, num_rows - 1, 2)],
        # Ligne total
        ('BACKGROUND', (0, -1), (-1, -1), TOTAL_BG),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, -1), (-1, -1), 10),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
        ('LINEABOVE', (0, -1), (-1, -1), 1.5, PRIMARY),
        # Grille
        ('GRID', (0, 0), (-1, -1), 0.4, BORDER),
        ('LINEBELOW', (0, 0), (-1, 0), 1, PRIMARY),
        ('BOX', (0, 0), (-1, -1), 0.8, ACCENT),
        ('ALIGN', (0, 1), (1, -1), 'CENTER'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
    ]))

    table_w, table_h = table.wrap(total_table_w, 400)
    table.drawOn(c, table_x, y_table_top - table_h)

    # ── Encadré solde total ──────────────────────────────
    y_after = y_table_top - table_h - 30
    total_box_w = 240
    total_box_x = w - total_box_w - 40

    draw_rounded_rect(c, total_box_x, y_after - 8, total_box_w, 38, 8, PRIMARY)

    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(white)
    c.drawString(total_box_x + 15, y_after + 8, "SOLDE TOTAL DÛ:")

    c.setFont("Helvetica-Bold", 14)
    total_str = fmt_money(grand_total)
    tw = c.stringWidth(total_str, "Helvetica-Bold", 14)
    c.drawString(total_box_x + total_box_w - tw - 15, y_after + 6, total_str)

    # ── Sommaire ancienneté des comptes ──────────────────
    y_aging = y_after - 55

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(PRIMARY)
    c.drawString(40, y_aging, "SOMMAIRE DE L'ANCIENNETÉ DES COMPTES")
    y_aging -= 5

    aging_headers = ["Courant", "1-30 jours", "31-60 jours", "61-90 jours", "90+ jours"]
    aging_values = data.get("aging", ["—", "—", "—", "—", "—"])
    aging_data_table = [aging_headers, aging_values]

    aging_col_w = [(w - 80) / 5] * 5
    aging_table = Table(aging_data_table, colWidths=aging_col_w)
    aging_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), ACCENT),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, 1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BOX', (0, 0), (-1, -1), 0.5, ACCENT),
        ('GRID', (0, 0), (-1, -1), 0.3, BORDER),
        ('BACKGROUND', (0, 1), (-1, 1), LIGHT_BG),
    ]))

    aging_w, aging_h = aging_table.wrap(w - 80, 100)
    aging_table.drawOn(c, 40, y_aging - aging_h)

    # ── Pied de page ─────────────────────────────────────
    y_footer = y_aging - aging_h - 35
    draw_rounded_rect(c, 40, y_footer - 12, w - 80, 32, 6, LIGHT_BG, BORDER)

    c.setFont("Helvetica-Oblique", 8.5)
    c.setFillColor(TEXT_GRAY)
    c.drawCentredString(w / 2, y_footer + 2, message_footer)

    # ── Barre accent en bas ──────────────────────────────
    c.setFillColor(PRIMARY)
    c.rect(0, 0, w, 6, fill=1, stroke=0)

    c.setFont("Helvetica", 7)
    c.setFillColor(TEXT_GRAY)
    c.drawCentredString(
        w / 2, 12,
        f"Généré le {datetime.now().strftime('%d-%m-%Y à %H:%M')}"
        " — Ce document est un relevé de compte et non une facture."
    )

    # ── Finaliser ────────────────────────────────────────
    c.save()

    # Nettoyer le logo temporaire
    if logo_tmp_path and os.path.exists(logo_tmp_path):
        os.unlink(logo_tmp_path)

    buffer.seek(0)
    return buffer


# ═══════════════════════════════════════════════════════════
# ROUTES FLASK
# ═══════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    """Health check pour Cloud Run."""
    return jsonify({"status": "ok", "service": "statement-generator"}), 200


@app.route("/generate-statement", methods=["POST"])
def generate_statement():
    """
    Génère un relevé de compte PDF.

    Reçoit un JSON depuis Make.com et retourne le PDF en binaire.

    Exemple d'appel depuis Make.com (HTTP Module):
    ───────────────────────────────────────────────
    URL:     https://votre-service.run.app/generate-statement
    Method:  POST
    Headers: Content-Type: application/json
    Body:    (voir la docstring de generate_statement_pdf)
    Parse response: Yes
    Response type: Binary
    """
    try:
        data = request.get_json(force=True)

        if not data:
            return jsonify({"error": "Corps JSON vide ou invalide."}), 400

        logger.info(
            f"Génération relevé pour: {data.get('customer_name', 'inconnu')} "
            f"— {len(data.get('invoices', []))} facture(s)"
        )

        pdf_buffer = generate_statement_pdf(data)

        # Nom du fichier avec le nom du client et la date
        customer = data.get("customer_name", "client").replace(" ", "_")
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"releve_{customer}_{date_str}.pdf"

        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )

    except ValueError as e:
        logger.error(f"Erreur de validation: {e}")
        return jsonify({"error": str(e)}), 400

    except Exception as e:
        logger.error(f"Erreur serveur: {e}", exc_info=True)
        return jsonify({"error": f"Erreur interne: {str(e)}"}), 500


# ═══════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
