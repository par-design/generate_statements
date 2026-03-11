#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════════════
  Relevé de compte PDF — DigitalOcean App Platform
  API REST pour Make.com / QuickBooks Online
══════════════════════════════════════════════════════════════

Endpoints:
  POST /generate-statement       → PDF depuis données pré-calculées
  POST /generate-statement-raw   → PDF depuis données brutes QuickBooks
  GET  /health                   → Health check

Le endpoint /generate-statement-raw est conçu pour simplifier
le scénario Make.com : il accepte directement les factures telles
que retournées par l'API QuickBooks et fait l'extraction du
Line Item 18 (Frais de retard) + calcul TPS/TVQ automatiquement.
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

# ── Config taxes Québec ─────────────────────────────────
TPS_RATE = 5.0        # 5%
TVQ_RATE = 9.975      # 9.975%
COMBINED_RATE = TPS_RATE + TVQ_RATE  # 14.975%

# ID du produit "Frais de retard" dans QuickBooks
FRAIS_RETARD_ITEM_ID = "18"


# ═══════════════════════════════════════════════════════════
# TRAITEMENT DES DONNÉES QUICKBOOKS
# ═══════════════════════════════════════════════════════════

def process_raw_invoices(raw_invoices, frais_retard_item_id=FRAIS_RETARD_ITEM_ID):
    """
    Transforme les factures brutes QuickBooks en données pour le PDF.

    Pour chaque facture :
    1. Extrait le montant du Line Item "Frais de retard" (Item ID configurable)
    2. Calcule le montant de base (sans frais de retard)
    3. Sépare TPS et TVQ depuis TxnTaxDetail.TotalTax

    Paramètres:
        raw_invoices: liste de factures telles que retournées par l'API QBO
        frais_retard_item_id: ID du produit "Frais de retard" (défaut: "18")

    Retourne:
        liste de dicts prêts pour le PDF
    """
    processed = []

    for inv in raw_invoices:
        # ── Extraire les frais de retard du Line Item ────
        frais_retard = 0.0
        montant_services = 0.0

        lines = inv.get("Line", [])
        for line in lines:
            detail_type = line.get("DetailType", "")

            # Ignorer les lignes SubTotal
            if detail_type == "SubTotalLineDetail":
                continue

            # Vérifier si c'est le Line Item "Frais de retard"
            if detail_type == "SalesItemLineDetail":
                item_ref = line.get("SalesItemLineDetail", {}).get("ItemRef", {})
                item_id = str(item_ref.get("value", ""))
                amount = float(line.get("Amount", 0))

                if item_id == str(frais_retard_item_id):
                    frais_retard += amount
                else:
                    montant_services += amount

        # ── Calculer TPS et TVQ ──────────────────────────
        total_tax = float(inv.get("TxnTaxDetail", {}).get("TotalTax", 0))

        if total_tax > 0 and COMBINED_RATE > 0:
            tps = round(total_tax * TPS_RATE / COMBINED_RATE, 2)
            tvq = round(total_tax * TVQ_RATE / COMBINED_RATE, 2)
            # Ajuster l'arrondi pour que tps + tvq = total_tax
            diff = round(total_tax - tps - tvq, 2)
            tvq = round(tvq + diff, 2)
        else:
            tps = 0.0
            tvq = 0.0

        # ── Formater la date ─────────────────────────────
        txn_date = inv.get("TxnDate", "")
        if "T" in txn_date:
            try:
                dt = datetime.fromisoformat(txn_date.replace("Z", "+00:00"))
                formatted_date = dt.strftime("%d-%m-%Y")
            except (ValueError, TypeError):
                formatted_date = txn_date[:10]
        else:
            formatted_date = txn_date

        # ── Construire l'objet facture pour le PDF ───────
        processed.append({
            "date": formatted_date,
            "invoice_number": inv.get("DocNumber", "—"),
            "amount": round(montant_services, 2),
            "interest": round(frais_retard, 2),
            "tps": tps,
            "tvq": tvq,
            "total": float(inv.get("TotalAmt", 0)),
        })

    return processed


# ═══════════════════════════════════════════════════════════
# GÉNÉRATION PDF
# ═══════════════════════════════════════════════════════════

def generate_statement_pdf(data, invoices):
    """
    Génère un relevé de compte PDF en mémoire.

    Paramètres:
        data: dict avec les infos entreprise/client
        invoices: liste de factures déjà formatées pour le PDF
    """
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

    # ── Préparer le logo ─────────────────────────────────
    logo_tmp_path = None
    if logo_base64:
        try:
            logo_bytes = base64.b64decode(logo_base64)
            logo_tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            logo_tmp.write(logo_bytes)
            logo_tmp.close()
            logo_tmp_path = logo_tmp.name
        except Exception as e:
            logger.warning(f"Impossible de décoder le logo base64: {e}")

    # ── Créer le PDF ─────────────────────────────────────
    buffer = io.BytesIO()
    w, h = letter
    c = canvas.Canvas(buffer, pagesize=letter)

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

    # ── Barre accent haut ────────────────────────────────
    c.setFillColor(PRIMARY)
    c.rect(0, h - 8, w, 8, fill=1, stroke=0)

    # ── Logo / Nom entreprise ────────────────────────────
    y_top = h - 50

    if logo_tmp_path and os.path.exists(logo_tmp_path):
        try:
            c.drawImage(logo_tmp_path, 40, y_top - 45, width=140, height=45,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            c.setFont("Helvetica-Bold", 16)
            c.setFillColor(PRIMARY)
            c.drawString(40, y_top - 20, company_name)
    else:
        c.setFont("Helvetica-Bold", 16)
        c.setFillColor(PRIMARY)
        c.drawString(40, y_top - 20, company_name)

    # ── Titre ────────────────────────────────────────────
    title_text = "RELEVÉ DE COMPTE"
    c.setFont("Helvetica-Bold", 20)
    title_w = c.stringWidth(title_text, "Helvetica-Bold", 20)
    draw_rounded_rect(c, w - title_w - 70, y_top - 35, title_w + 40, 36, 6, PRIMARY)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(w - title_w - 50, y_top - 27, title_text)

    # ── Infos entreprise ─────────────────────────────────
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

    # ── Détails du relevé ────────────────────────────────
    details_x = w - 230
    y_det = y_top - 65
    draw_rounded_rect(c, details_x - 15, y_det - 58, 220, 72, 6, LIGHT_BG, BORDER)

    for label, value in [
        ("Date du relevé:", statement_date),
        ("Période:", f"{period_start} au {period_end}"),
        ("No. membre:", customer_member_number),
    ]:
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

    headers = ["Date", "# Facture", "Montant\nfacture", "Frais de\nretard", "TPS", "TVQ", "Total"]

    header_style = ParagraphStyle('header', fontName='Helvetica-Bold', fontSize=8,
                                   textColor=white, alignment=TA_CENTER, leading=10)
    cell_right = ParagraphStyle('cell_right', fontName='Helvetica', fontSize=8.5,
                                 textColor=TEXT_DARK, alignment=TA_RIGHT, leading=11)
    cell_center = ParagraphStyle('cell_center', fontName='Helvetica', fontSize=8.5,
                                  textColor=TEXT_DARK, alignment=TA_CENTER, leading=11)
    total_style = ParagraphStyle('total', fontName='Helvetica-Bold', fontSize=9,
                                  textColor=PRIMARY, alignment=TA_RIGHT, leading=11)
    total_label = ParagraphStyle('total_label', fontName='Helvetica-Bold', fontSize=9.5,
                                  textColor=PRIMARY, alignment=TA_CENTER, leading=11)

    formatted_data = []
    formatted_data.append([
        Paragraph(h_text.replace("\n", "<br/>"), header_style) for h_text in headers
    ])

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

    col_widths = [72, 68, 82, 95, 72, 72, 82]
    total_table_w = sum(col_widths)
    table_x = (w - total_table_w) / 2
    num_rows = len(formatted_data)

    table = Table(formatted_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 1), (-1, -2), 7),
        ('BOTTOMPADDING', (0, 1), (-1, -2), 7),
        *[('BACKGROUND', (0, i), (-1, i), ROW_ALT) for i in range(2, num_rows - 1, 2)],
        ('BACKGROUND', (0, -1), (-1, -1), TOTAL_BG),
        ('TOPPADDING', (0, -1), (-1, -1), 10),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
        ('LINEABOVE', (0, -1), (-1, -1), 1.5, PRIMARY),
        ('GRID', (0, 0), (-1, -1), 0.4, BORDER),
        ('LINEBELOW', (0, 0), (-1, 0), 1, PRIMARY),
        ('BOX', (0, 0), (-1, -1), 0.8, ACCENT),
        ('ALIGN', (0, 1), (1, -1), 'CENTER'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
    ]))

    table_w, table_h = table.wrap(total_table_w, 400)
    table.drawOn(c, table_x, y_table_top - table_h)

    # ── Solde total ──────────────────────────────────────
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

    # ── Ancienneté des comptes ───────────────────────────
    y_aging = y_after - 55
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(PRIMARY)
    c.drawString(40, y_aging, "SOMMAIRE DE L'ANCIENNETÉ DES COMPTES")
    y_aging -= 5

    aging_headers = ["Courant", "1-30 jours", "31-60 jours", "61-90 jours", "90+ jours"]
    aging_values = data.get("aging", ["—", "—", "—", "—", "—"])
    aging_table = Table([aging_headers, aging_values], colWidths=[(w - 80) / 5] * 5)
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

    # ── Barre accent bas ─────────────────────────────────
    c.setFillColor(PRIMARY)
    c.rect(0, 0, w, 6, fill=1, stroke=0)
    c.setFont("Helvetica", 7)
    c.setFillColor(TEXT_GRAY)
    c.drawCentredString(w / 2, 12,
        f"Généré le {datetime.now().strftime('%d-%m-%Y à %H:%M')}"
        " — Ce document est un relevé de compte et non une facture.")

    c.save()

    if logo_tmp_path and os.path.exists(logo_tmp_path):
        os.unlink(logo_tmp_path)

    buffer.seek(0)
    return buffer


# ═══════════════════════════════════════════════════════════
# ROUTES FLASK
# ═══════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "statement-generator"}), 200


@app.route("/generate-statement", methods=["POST"])
def generate_statement():
    """
    Endpoint original — reçoit des factures déjà formatées.
    (amount, interest, tps, tvq, total pré-calculés)
    """
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
        filename = f"releve_{data.get('customer_name', 'client').replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
        return send_file(pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)

    except Exception as e:
        logger.error(f"Erreur: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/generate-statement-raw", methods=["POST"])
def generate_statement_raw():
    """
    ════════════════════════════════════════════════════════
    NOUVEAU ENDPOINT — Données brutes QuickBooks
    ════════════════════════════════════════════════════════

    Reçoit les factures TELLES QUELLES de l'API QuickBooks
    et fait automatiquement :
      1. Extraction du Line Item 18 (Frais de retard)
      2. Calcul TPS / TVQ depuis TotalTax
      3. Calcul du montant hors frais de retard
      4. Génération du PDF

    ── Payload JSON attendu depuis Make.com ─────────────

    {
      "company_name": "Votre Entreprise Inc.",
      "company_address": "123 rue Principale\nVille, QC H2X 1Y6",
      "company_phone": "(514) 555-1234",
      "company_email": "info@entreprise.com",
      "company_tps": "123456789 RT0001",
      "company_tvq": "1234567890 TQ0001",
      "customer_name": "{{3.DisplayName}}",
      "customer_address": "{{3.BillAddr.Line1}}",
      "customer_member_number": "{{3.Id}}",
      "statement_date": "10-03-2026",
      "period_start": "01-01-2025",
      "period_end": "10-03-2026",
      "frais_retard_item_id": "18",
      "raw_invoices": [
        ... factures brutes de QuickBooks (le tableau Invoice[]) ...
      ]
    }

    ── Dans Make.com (Module HTTP) ──────────────────────

    URL:     https://votre-url.ondigitalocean.app/generate-statement-raw
    Method:  POST
    Headers: Content-Type: application/json
    Body:    Le JSON ci-dessus
             raw_invoices = {{4.body.QueryResponse.Invoice}}
    """
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "Corps JSON vide."}), 400

        raw_invoices = data.get("raw_invoices", [])

        # Make.com peut envoyer raw_invoices comme une string JSON
        # au lieu d'un tableau — on gère les deux cas
        if isinstance(raw_invoices, str):
            try:
                import json
                raw_invoices = json.loads(raw_invoices)
            except (json.JSONDecodeError, TypeError) as e:
                return jsonify({"error": f"raw_invoices n'est pas un JSON valide: {str(e)}"}), 400

        # Si c'est un dict unique au lieu d'une liste (1 seule facture)
        if isinstance(raw_invoices, dict):
            raw_invoices = [raw_invoices]

        if not raw_invoices:
            return jsonify({"error": "Aucune facture dans 'raw_invoices'."}), 400

        # ID du produit Frais de retard (configurable, défaut = 18)
        frais_retard_id = data.get("frais_retard_item_id", FRAIS_RETARD_ITEM_ID)

        logger.info(
            f"[generate-statement-raw] {data.get('customer_name', 'inconnu')} "
            f"— {len(raw_invoices)} facture(s) brute(s), "
            f"frais_retard_item_id={frais_retard_id}"
        )

        # Transformer les factures brutes QBO → format PDF
        invoices = process_raw_invoices(raw_invoices, frais_retard_id)

        logger.info(
            f"  → Factures traitées: {len(invoices)}, "
            f"Total frais de retard: {sum(i['interest'] for i in invoices):.2f}, "
            f"Total TPS: {sum(i['tps'] for i in invoices):.2f}, "
            f"Total TVQ: {sum(i['tvq'] for i in invoices):.2f}"
        )

        # Générer le PDF
        pdf_buffer = generate_statement_pdf(data, invoices)

        customer = data.get("customer_name", "client").replace(" ", "_")
        filename = f"releve_{customer}_{datetime.now().strftime('%Y%m%d')}.pdf"

        return send_file(
            pdf_buffer, mimetype="application/pdf",
            as_attachment=True, download_name=filename
        )

    except Exception as e:
        logger.error(f"Erreur: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
