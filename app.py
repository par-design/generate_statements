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
import re
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
# IDs d'article QuickBooks connus pour les frais de retard (varie d'un fichier à l'autre).
FRAIS_RETARD_ITEM_ID = ["17", "18"]
# Détection de secours par nom d'article (insensible à la casse/accents) si l'ID ne correspond pas.
FRAIS_RETARD_KEYWORDS = ("frais de retard", "frais retard", "interet", "intérêt", "late fee", "penalite", "pénalité")
# Champs qui n'existent que sur l'entité Customer de QuickBooks : leur présence
# trahit un payload Make où la fiche client est fusionnée dans la facture.
CUSTOMER_ONLY_KEYS = ("BalanceWithJobs", "FullyQualifiedName", "PrintOnCheckName",
                      "DisplayName", "PreferredDeliveryMethod", "BillWithParent")

# ── Layout constants ────────────────────────────────────
ML = 35           # Marge gauche
MR = 35           # Marge droite
RADIUS = 8        # Coins arrondis

# ── Fenêtre de l'enveloppe APFFQ ────────────────────────
# Enveloppe #10 (9½" × 4¼"), fenêtre de 4½" × 1" positionnée à 5/8" du bord
# gauche et ¾" du bord inférieur (donc 2½" du bord supérieur : 2½ + 1 + ¾ = 4¼).
#
# La feuille Letter est pliée en trois panneaux de 11/3 = 3,667". Le paquet plié
# repose au fond de l'enveloppe et c'est le tiers SUPÉRIEUR de la page qui se
# présente dans la fenêtre. La bande découverte va donc, mesurée depuis le haut
# de la page, de 3,667 − 1,75 = 1,917" à 3,667 − 0,75 = 2,917".
#
# Tant que le bloc destinataire tient entre WINDOW_TOP et WINDOW_BOTTOM (marge de
# sécurité comprise), la plieuse-inséreuse suffit : plus besoin de replier les
# feuilles à la main pour faire remonter l'adresse.
WINDOW_TOP = 138.0        # 1,917" × 72 — haut de la bande visible
WINDOW_BOTTOM = 210.0     # 2,917" × 72 — bas de la bande visible
WINDOW_SAFE = 9.0         # 1/8" de sécurité sur chaque bord (glissement du pli)

# Horizontalement, le paquet de 8½" flotte dans une enveloppe de 9½". Repères
# ci-dessous : position de la fenêtre sur la page quand le paquet est centré.
WINDOW_LEFT = 9.0         # 0,125" × 72
WINDOW_WIDTH = 324.0      # 4½" × 72

# Le paquet peut coulisser de ~0,9" dans l'enveloppe. Selon qu'il est plaqué à
# gauche ou à droite, la fenêtre balaie la page de 45 à 369 pt ou de −20 à
# 304 pt : seule la bande ci-dessous est visible dans TOUS les cas.
WINDOW_X_MIN = 45.0
WINDOW_X_MAX = 304.0

# Le bloc destinataire est posé à 65 pt plutôt qu'aligné sur ML + 15 : ça laisse
# 20 pt de marge à gauche au lieu de 5 pt si le paquet est plaqué à gauche.
ADDRESS_X = 65.0
ADDRESS_MAX_W = WINDOW_X_MAX - ADDRESS_X
FOLD_HEIGHT = 792.0 / 3   # 3,667" — hauteur d'un panneau du pli en trois

# Haut de la carte « Résumé du compte », aligné sur le trait du bloc destinataire.
CARD_TOP = 140.0

# Affiché à la place du numéro de membre quand aucun n'a pu être déterminé.
NO_MEMBER_NUMBER = "—"


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def fmt_money(val):
    return f"{val:,.2f}".replace(",", " ") + " $"


def _to_float(value, default=0.0):
    """Convertit une valeur QuickBooks en float. None/"" /texte → `default`."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_qb_date(value):
    """Date QuickBooks (« 2026-05-15 » ou ISO avec heure) → datetime naïf."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if "T" in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def is_customer_record(inv):
    """Vrai si le scénario Make a fusionné la fiche client dans la facture.

    Make.com envoie un objet qui mélange les champs de la facture et ceux du
    client. Ces clés-là n'existent que sur l'entité Customer de QuickBooks ;
    leur présence signifie que « Balance » a été écrasé par le solde GLOBAL du
    client — la même valeur sur chaque facture — et non le solde de la facture.
    """
    return any(k in inv for k in CUSTOMER_ONLY_KEYS)


def account_balance_from_raw(raw_invoices):
    """Solde du compte client, quand le payload porte la fiche client.

    Retourne None si le payload ne contient pas de solde client exploitable.
    """
    for inv in raw_invoices:
        if isinstance(inv, dict) and is_customer_record(inv) and inv.get("Balance") is not None:
            return _to_float(inv.get("Balance"))
    return None


def resolve_balances(raw_invoices):
    """Solde restant dû par facture, aligné sur `raw_invoices`.

    `TotalAmt` est le montant facturé à l'origine : il ne bouge pas quand le
    membre paie, donc l'utiliser tel quel affiche une dette déjà acquittée.

    Deux formes de payload :

    * facture pure → « Balance » est le solde de CETTE facture, on le prend ;
    * facture fusionnée avec la fiche client (le cas de Make.com) → « Balance »
      est le solde global du client, identique sur chaque ligne. L'additionner
      multiplierait la dette par le nombre de factures. On le répartit plutôt
      sur les factures, en supposant que les paiements ont réglé les plus
      anciennes d'abord (convention comptable usuelle) : le reliquat se pose
      donc sur les plus récentes.

    Retourne (soldes, source) où source vaut "invoice" (solde certain, facture
    par facture) ou "account" (solde du compte réparti, donc estimé par ligne).
    """
    totals = [_to_float(inv.get("TotalAmt")) for inv in raw_invoices]

    if not any(is_customer_record(inv) for inv in raw_invoices if isinstance(inv, dict)):
        balances = []
        for inv, total in zip(raw_invoices, totals):
            if inv.get("Balance") is not None:
                balances.append(_to_float(inv.get("Balance"), total))
            else:
                balances.append(total)
        return balances, "invoice"

    account = account_balance_from_raw(raw_invoices)
    grand_total = sum(totals)
    if account is None:
        return totals, "account"

    if account > grand_total + 0.005:
        # Le client doit plus que ce que le relevé énumère : des factures
        # manquent à l'appel. Mieux vaut un document cohérent avec ses propres
        # lignes qu'un total invérifiable.
        logger.warning(
            f"[solde] solde client {account:.2f} $ > total des factures listées "
            f"{grand_total:.2f} $ — factures manquantes dans le payload Make, "
            f"on s'en tient au total listé"
        )
        return totals, "account"

    if abs(account - grand_total) <= 0.005:
        return totals, "account"

    logger.info(
        f"[solde] solde client {account:.2f} $ pour {grand_total:.2f} $ facturés — "
        f"{grand_total - account:.2f} $ de paiements répartis (plus anciennes d'abord)"
    )
    order = sorted(
        range(len(raw_invoices)),
        key=lambda i: (_parse_qb_date(raw_invoices[i].get("DueDate"))
                       or _parse_qb_date(raw_invoices[i].get("TxnDate"))
                       or datetime.min, i),
        reverse=True,   # la plus récente encaisse le reliquat
    )
    balances = [0.0] * len(raw_invoices)
    remaining = account
    for i in order:
        take = min(max(remaining, 0.0), totals[i])
        balances[i] = round(take, 2)
        remaining -= take
    return balances, "account"


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

def _strip_accents(s):
    """Retire les accents pour une comparaison de texte robuste."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def is_frais_retard(item_id, item_name, frais_retard_ids):
    """Détermine si une ligne est un frais de retard, par ID OU par nom d'article.

    La détection par nom sert de filet de sécurité : l'ID d'article QuickBooks varie
    d'un fichier à l'autre, mais le nom (« Frais de retard ») reste stable.
    """
    if str(item_id) in {str(i) for i in frais_retard_ids}:
        return True
    name = _strip_accents(str(item_name or "")).lower()
    return any(kw in name for kw in FRAIS_RETARD_KEYWORDS)


# Noms possibles du champ personnalisé QuickBooks portant le numéro de membre
# (comparés sans accents ni casse).
MEMBER_FIELD_NAMES = ("numero de membre", "no de membre", "numero membre", "membre")


def extract_member_number(data, raw_invoices):
    """Détermine le numéro de membre APFFQ à afficher sur le relevé.

    Ordre de priorité :
      1. `customer_member_number` du payload — sauf s'il est identique à l'ID
         d'enregistrement QuickBooks du client (Make.com a longtemps mappé
         `{{9.Id}}` ici, ce qui donnait un faux numéro sur tous les relevés).
         `customer_member_number_source` lève ce doute : toute valeur autre que
         « qb_id » (p. ex. « wp », l'API ffq-qb/v1 du site fraises qui fait foi)
         est retenue telle quelle, même si elle coïncide avec l'ID QuickBooks ;
      2. champ personnalisé QuickBooks « Numéro de membre » de la fiche client ;
      3. motif « #1234 » dans le nom d'affichage ou la raison sociale.

    Aucune correspondance → « — ». Un numéro absent est corrigeable ; un numéro
    faux se rend chez le membre sans que personne ne le remarque.
    """
    first = raw_invoices[0] if raw_invoices else {}
    customer_id = str(first.get("Id", "") or "").strip()
    explicit = str(data.get("customer_member_number", "") or "").strip()
    source = str(data.get("customer_member_number_source", "") or "").strip().lower()
    trusted_source = source not in ("", "qb_id")

    if explicit and (trusted_source or explicit != customer_id):
        return explicit
    if explicit:
        logger.warning(
            f"[membre] '{explicit}' ignoré: identique à l'ID client QuickBooks — "
            f"vérifier le mapping customer_member_number dans Make.com"
        )

    # 2) Champ personnalisé de la fiche client
    for inv in raw_invoices:
        for key in ("CustomerCustomField", "CustomField"):
            fields = inv.get(key) or []
            if isinstance(fields, dict):
                fields = [fields]
            if not isinstance(fields, list):
                continue
            for field in fields:
                if not isinstance(field, dict):
                    continue
                name = _strip_accents(str(field.get("Name", "") or "")).strip().lower()
                if name in MEMBER_FIELD_NAMES:
                    value = str(field.get("StringValue") or field.get("Value") or "").strip()
                    if value:
                        logger.info(f"[membre] #{value} depuis le champ personnalisé '{key}'")
                        return value

    # 3) Motif #1234 dans le nom ou les notes du client
    for candidate in (first.get("DisplayName"), first.get("CompanyName"),
                      data.get("customer_name"), first.get("Notes")):
        match = re.search(r"#\s*(\d+)", str(candidate or ""))
        if match:
            logger.info(f"[membre] #{match.group(1)} extrait du nom '{candidate}'")
            return match.group(1)

    logger.warning(
        f"[membre] introuvable pour '{data.get('customer_name', '?')}' "
        f"(ID client QB {customer_id or '?'}) — relevé ignoré"
    )
    return NO_MEMBER_NUMBER


# Provinces et territoires, avec leurs abréviations : sert à repérer où couper
# une adresse livrée sur une seule ligne.
PROVINCE_KEYS = {
    "quebec", "qc", "ontario", "on", "nouveau-brunswick", "nb",
    "nouvelle-ecosse", "ns", "ile-du-prince-edouard", "pe",
    "terre-neuve-et-labrador", "nl", "manitoba", "mb", "saskatchewan", "sk",
    "alberta", "ab", "colombie-britannique", "bc", "yukon", "yt",
    "territoires du nord-ouest", "nt", "nunavut", "nu",
}


def split_single_line_address(address):
    """Coupe une adresse d'une seule ligne en « rue » puis « ville province CP ».

    Filet de sécurité quand la fiche client n'a pas d'adresse structurée : on
    coupe au segment qui porte la province, faute de quoi à la dernière virgule.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) < 2:
        return [address.strip()] if address.strip() else []
    for i in range(len(parts) - 1, 0, -1):
        if _strip_accents(parts[i]).lower() in PROVINCE_KEYS:
            return [", ".join(parts[:i]), " ".join(parts[i:])]
    return [", ".join(parts[:-1]), parts[-1]]


def format_customer_address(data, raw_invoices):
    """Compose l'adresse du destinataire sur deux lignes.

    Make.com livre `customer_address` concaténé sur une seule ligne
    (« 11 000, rang Sainte-Henriette Mirabel, Québec, J7J 1Z9 »), qui déborde de
    la fenêtre de l'enveloppe dès que la rue est un peu longue. La fiche client
    porte pourtant l'adresse structurée dans BillAddr : on la recompose au format
    postal — rue d'abord, puis ville, province et code postal — ce qui tient
    dans la largeur et laisse à l'adresse la place de s'allonger.

    Sans BillAddr exploitable, on retombe sur `customer_address` tel quel s'il
    est déjà sur plusieurs lignes, sinon on le coupe avant la province.
    """
    addr = {}
    for inv in raw_invoices:
        candidate = inv.get("BillAddr") or inv.get("ShipAddr")
        if isinstance(candidate, dict) and candidate.get("Line1"):
            addr = candidate
            break

    if addr:
        lines = [str(addr.get(k, "") or "").strip() for k in ("Line1", "Line2", "Line3")]
        lines = [s for s in lines if s]
        city = str(addr.get("City", "") or "").strip()
        province = str(addr.get("CountrySubDivisionCode", "") or "").strip()
        postal = str(addr.get("PostalCode", "") or "").strip()
        # Format postal québécois, celui que l'APFFQ utilise déjà sur ses
        # enveloppes : « Longueuil (Québec)  J4H 3Y9 ». Sans ville, la province
        # perd ses parenthèses — elles n'encadreraient plus rien.
        region = f"({province})" if (province and city) else province
        locality = " ".join(p for p in (city, region, postal) if p)
        if locality:
            lines.append(locality)
        if lines:
            return "\n".join(lines)

    existing = str(data.get("customer_address", "") or "").strip()
    if "\n" in existing:
        return existing
    return "\n".join(split_single_line_address(existing))


def process_raw_invoices(raw_invoices, frais_retard_item_id=FRAIS_RETARD_ITEM_ID, balances=None):
    # Accepte un ID unique ("17") ou une liste (["17", "18"]).
    frais_retard_ids = frais_retard_item_id if isinstance(frais_retard_item_id, (list, tuple)) else [frais_retard_item_id]

    if balances is None:
        balances, _ = resolve_balances(raw_invoices)

    processed = []
    for idx, inv in enumerate(raw_invoices):
        frais_retard = 0.0
        montant_services = 0.0

        for line in inv.get("Line", []):
            if line.get("DetailType") == "SubTotalLineDetail":
                continue
            if line.get("DetailType") == "SalesItemLineDetail":
                item_ref = line.get("SalesItemLineDetail", {}).get("ItemRef", {})
                item_id = str(item_ref.get("value", ""))
                item_name = item_ref.get("name", "")
                amount = float(line.get("Amount", 0))
                if is_frais_retard(item_id, item_name, frais_retard_ids):
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

        total_amt = _to_float(inv.get("TotalAmt"))
        balance = balances[idx] if idx < len(balances) else total_amt

        processed.append({
            "date": formatted_date,
            "invoice_number": inv.get("DocNumber", "—"),
            "amount": round(montant_services, 2),
            "interest": round(frais_retard, 2),
            "tps": tps, "tvq": tvq,
            "total": round(total_amt, 2),
            "balance": round(balance, 2),
        })
    return processed


def calculate_aging(raw_invoices, balances=None):
    if balances is None:
        balances, _ = resolve_balances(raw_invoices)

    now = datetime.now()
    buckets = [0.0, 0.0, 0.0, 0.0, 0.0]
    for idx, inv in enumerate(raw_invoices):
        balance = balances[idx] if idx < len(balances) else _to_float(inv.get("TotalAmt"))
        if balance <= 0:
            continue
        due_date = _parse_qb_date(inv.get("DueDate"))
        if due_date is None:
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

GUIDE_COLOR = HexColor("#0090C0")


def draw_fitted(cv, x, y, text, font, size, max_width, min_size=6.5):
    """Écrit le texte en rétrécissant la police jusqu'à tenir dans `max_width`.

    Le bloc destinataire doit rester dans la fenêtre de l'enveloppe : les raisons
    sociales longues (« Les Jardins Maraîchers de la Rivière-du-Nord et Fils
    inc. ») débordaient à droite et se faisaient couper par le papier. Perdre un
    point ou deux de corps reste lisible ; se faire tronquer par l'enveloppe non.
    """
    while size > min_size and cv.stringWidth(text, font, size) > max_width:
        size -= 0.25
    cv.setFont(font, size)
    cv.drawString(x, y, text)


def wrap_to_width(cv, text, font, size, max_width):
    """Découpe le texte aux espaces pour qu'aucun morceau ne dépasse max_width.

    QuickBooks livre l'adresse tantôt sur trois lignes, tantôt concaténée sur une
    seule (« 11 000, rang Sainte-Henriette Mirabel, Québec, J7J 1Z9 »). Sous
    6,5 pt le rétrécissement de draw_fitted cesse d'être lisible : passé cette
    limite, mieux vaut replier.
    """
    words = text.split()
    if not words:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if cv.stringWidth(trial, font, size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def draw_envelope_guides(cv, w, h):
    """Imprime le contour de la fenêtre d'enveloppe et les lignes de pli.

    Sert à valider la mise en page sur une vraie enveloppe : on imprime, on plie
    sur les deux traits, on insère, et le bloc destinataire doit tomber
    entièrement dans le rectangle. Le rectangle correspond à un paquet centré ;
    comme la feuille de 8½" flotte dans une enveloppe de 9½", la fenêtre réelle
    peut glisser jusqu'à un demi-pouce à gauche ou à droite.

    Activé par `envelope_guides` dans le payload — jamais sur un relevé de membre.
    """
    cv.saveState()
    cv.setStrokeColor(GUIDE_COLOR)
    cv.setFillColor(GUIDE_COLOR)
    cv.setLineWidth(0.8)
    cv.setDash(4, 3)
    cv.rect(WINDOW_LEFT, h - WINDOW_BOTTOM, WINDOW_WIDTH,
            WINDOW_BOTTOM - WINDOW_TOP, fill=0, stroke=1)
    # Bande garantie quel que soit le glissement du paquet : c'est elle, et non
    # le rectangle nominal, que le bloc destinataire ne doit jamais déborder.
    cv.setDash(1, 2)
    cv.line(WINDOW_X_MIN, h - WINDOW_BOTTOM, WINDOW_X_MIN, h - WINDOW_TOP)
    cv.line(WINDOW_X_MAX, h - WINDOW_BOTTOM, WINDOW_X_MAX, h - WINDOW_TOP)
    cv.setDash(4, 3)
    for n in (1, 2):
        y_fold = h - FOLD_HEIGHT * n
        cv.line(0, y_fold, w, y_fold)

    cv.setDash()
    cv.setFont(F("Poppins-Medium"), 6.5)
    cv.drawString(WINDOW_LEFT, h - WINDOW_TOP + 4,
                  'FENÊTRE DE L\'ENVELOPPE — 4½" × 1"')
    for n in (1, 2):
        y_fold = h - FOLD_HEIGHT * n
        cv.drawRightString(w - MR, y_fold + 4, f"PLI {n}")
    cv.restoreState()


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
    customer_member_number = data.get("customer_member_number", NO_MEMBER_NUMBER)
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
    # Solde réellement dû : facturé moins les paiements déjà appliqués.
    total_balance = sum(inv.get("balance", inv["total"]) for inv in invoices)
    total_paid = grand_total - total_balance
    has_payments = total_paid > 0.005
    # Colonne « Solde dû » seulement si le solde est connu facture par facture.
    # Sur un payload Make, il vient du compte client et n'est qu'une répartition
    # estimée par ligne : l'afficher donnerait un chiffre invérifiable au membre.
    show_balance_col = has_payments and data.get("balance_source") != "account"

    # ═════════════════════════════════════════════════════
    # HEADER
    # ═════════════════════════════════════════════════════
    # En-tête resserré (110 → 88 pt) : les 22 pt récupérés servent à faire
    # remonter le bloc destinataire dans la fenêtre de l'enveloppe.
    header_h = 88
    c.setFillColor(white)
    c.rect(0, h - header_h, w, header_h, fill=1, stroke=0)
    c.setFillColor(RED)
    c.rect(0, h - header_h, w, 2, fill=1, stroke=0)

    if logo_tmp_path and os.path.exists(logo_tmp_path):
        try:
            c.drawImage(logo_tmp_path, ML, h - header_h + 14, width=55, height=46,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    name_lines = company_name.split("\n") if "\n" in company_name else [company_name]
    c.setFont(F("Poppins-Bold"), 9.5)
    c.setFillColor(DARKER_RED)
    y_name = h - 36
    for nl in name_lines:
        c.drawString(100, y_name, nl.strip())
        y_name -= 12

    c.setFont(F("Poppins-Medium"), 7)
    c.setFillColor(TEXT_DARK)
    addr_line = company_address.replace("\n", ", ")
    c.drawString(100, h - 61, f"{addr_line}  |  Tél: {company_phone}")
    c.drawString(100, h - 71, f"{company_email}  |  TPS: {company_tps}  |  TVQ: {company_tvq}")

    c.setFont(F("Poppins-Bold"), 19)
    c.setFillColor(DARKER_RED)
    c.drawRightString(w - MR, h - 44, "RELEVÉ DE")
    c.drawRightString(w - MR, h - 65, "COMPTE")

    # ═════════════════════════════════════════════════════
    # BANDE INFO
    # ═════════════════════════════════════════════════════
    # Remontée sous l'en-tête : l'encadré descendait jusqu'à 158 pt et mordait
    # donc sur la fenêtre (138–210 pt), au détriment de l'adresse. Il s'arrête
    # maintenant à 132 pt, ce qui libère toute la bande pour le destinataire.
    y_info = h - header_h - 36
    draw_rounded_rect(c, ML, y_info - 8, CW, 38, RADIUS, white, PINK, 1)
    c.setFont(F("Poppins-Medium"), 8)
    c.setFillColor(DARKER_RED)
    c.drawString(ML + 15, y_info + 8, f"Date: {statement_date}")
    c.drawCentredString(w / 2, y_info + 8, f"Période: {period_start} au {period_end}")
    c.drawRightString(w - MR - 15, y_info + 8, f"No. membre: {customer_member_number}")

    # ═════════════════════════════════════════════════════
    # CLIENT + CARTE RÉSUMÉ
    # ═════════════════════════════════════════════════════
    # Destinataire (gauche) — calé sur la fenêtre de l'enveloppe.
    #
    # La mention « FACTURER À » a été retirée : elle mangeait une ligne entière
    # de la bande visible, qui n'en compte qu'une poignée.
    #
    # Le bloc est composé d'abord et tracé ensuite : son nombre de lignes varie
    # (producteur facultatif, adresse sur une à trois lignes, repli des lignes
    # trop larges) et c'est lui qui détermine l'interligne. Deux à quatre lignes
    # gardent les 13 pt habituels ; au-delà, l'interligne se resserre pour que la
    # dernière ligne reste au-dessus de WINDOW_BOTTOM.
    addr_font = F("Poppins-Light")
    block = []
    if customer_producer_name:
        block.append((customer_producer_name, F("Poppins-Medium"), 8.5, TEXT_GRAY))
    block.append((customer_name, F("Poppins-Bold"), 10, TEXT_DARK))
    for raw_line in customer_address.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        for piece in wrap_to_width(c, line, addr_font, 8.5, ADDRESS_MAX_W):
            block.append((piece, addr_font, 8.5, TEXT_GRAY))

    y_block_top = h - (WINDOW_TOP + WINDOW_SAFE)
    y_block_bot = h - (WINDOW_BOTTOM - WINDOW_SAFE)
    # Interligne nominal de 13 pt, resserré seulement s'il faut caser six lignes.
    leading = 13.0
    if len(block) > 1:
        leading = min(leading, (y_block_top - y_block_bot) / (len(block) - 1))

    y_line = y_block_top
    for text, font, size, color in block:
        c.setFillColor(color)
        draw_fitted(c, ADDRESS_X, y_line, text, font, size, ADDRESS_MAX_W)
        y_line -= leading

    # Trait tracé après coup, sur la hauteur réellement occupée : de longueur
    # fixe, il dépassait sous les adresses courtes.
    c.setStrokeColor(RED)
    c.setLineWidth(2.5)
    # Volontairement laissé à ML + 5 (40 pt) : en deçà de la bande 45–304 pt, il
    # ne risque donc pas d'apparaître dans la fenêtre à côté de l'adresse.
    c.line(ML + 5, y_line + leading - 6, ML + 5, y_block_top + 8)

    # Carte résumé (droite)
    card_w = 250
    card_h = 129 if has_payments else 115
    card_x = w - MR - card_w
    # Découplée du bloc destinataire : celui-ci est désormais contraint par la
    # fenêtre de l'enveloppe, la carte se cale simplement sous la bande info.
    card_y = h - CARD_TOP - card_h

    draw_rounded_rect(c, card_x, card_y, card_w, card_h, RADIUS, white, DARK_RED, 1)

    # Border under title
    c.setStrokeColor(DARK_RED)
    c.setLineWidth(1)
    c.line(card_x, card_y + card_h - 28, card_x + card_w, card_y + card_h - 28)

    c.setFont(F("Poppins-Bold"), 9)
    c.setFillColor(DARKER_RED)
    c.drawCentredString(card_x + card_w / 2, card_y + card_h - 20, "RÉSUMÉ DU COMPTE")

    summary_lines = [
        ("Sous-total services", fmt_money(total_amount)),
        ("Frais de retard", fmt_money(total_interest)),
        ("TPS", fmt_money(total_tps)),
        ("TVQ", fmt_money(total_tvq)),
    ]
    if has_payments:
        summary_lines.append(("Paiements reçus", "- " + fmt_money(abs(total_paid))))

    y_line = card_y + card_h - 42
    for label, val in summary_lines:
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
    c.drawRightString(card_x + card_w - 15, y_line - 8, fmt_money(total_balance))

    # ═════════════════════════════════════════════════════
    # TABLEAU DES FACTURES
    # ═════════════════════════════════════════════════════
    y_table = card_y - 25

    headers = ["Date", "# Facture", "Montant\nfacture", "Frais de\nretard", "TPS", "TVQ", "Total"]
    if show_balance_col:
        headers.append("Solde\ndû")
    h_style = ParagraphStyle('h', fontName=F('Poppins-Bold'), fontSize=7.5, textColor=DARKER_RED, alignment=TA_CENTER, leading=9.5)
    c_right = ParagraphStyle('cr', fontName=F('Poppins'), fontSize=8, textColor=TEXT_DARK, alignment=TA_RIGHT, leading=11)
    c_center = ParagraphStyle('cc', fontName=F('Poppins'), fontSize=8, textColor=TEXT_DARK, alignment=TA_CENTER, leading=11)
    t_style = ParagraphStyle('ts', fontName=F('Poppins-Bold'), fontSize=8.5, textColor=DARKER_RED, alignment=TA_RIGHT, leading=11)
    t_label = ParagraphStyle('tl', fontName=F('Poppins-Bold'), fontSize=9, textColor=DARKER_RED, alignment=TA_CENTER, leading=11)

    tdata = [[Paragraph(hh.replace("\n", "<br/>"), h_style) for hh in headers]]
    for inv in invoices:
        row = [
            Paragraph(inv["date"], c_center), Paragraph(str(inv["invoice_number"]), c_center),
            Paragraph(fmt_money(inv["amount"]), c_right), Paragraph(fmt_money(inv["interest"]), c_right),
            Paragraph(fmt_money(inv["tps"]), c_right), Paragraph(fmt_money(inv["tvq"]), c_right),
            Paragraph(fmt_money(inv["total"]), c_right),
        ]
        if show_balance_col:
            row.append(Paragraph(fmt_money(inv.get("balance", inv["total"])), c_right))
        tdata.append(row)
    total_row = [
        Paragraph("", t_style), Paragraph("TOTAL", t_label),
        Paragraph(fmt_money(total_amount), t_style), Paragraph(fmt_money(total_interest), t_style),
        Paragraph(fmt_money(total_tps), t_style), Paragraph(fmt_money(total_tvq), t_style),
        Paragraph(fmt_money(grand_total), t_style),
    ]
    if show_balance_col:
        total_row.append(Paragraph(fmt_money(total_balance), t_style))
    tdata.append(total_row)

    base = [70, 66, 80, 92, 70, 70, 80]
    if show_balance_col:
        base = [62, 68, 72, 82, 56, 56, 68, 68]
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

    if data.get("envelope_guides"):
        draw_envelope_guides(c, w, h)

    c.save()

    if logo_tmp_path and logo_tmp_path != DEFAULT_LOGO and os.path.exists(logo_tmp_path):
        os.unlink(logo_tmp_path)

    buffer.seek(0)
    return buffer


# ═══════════════════════════════════════════════════════════
# ROUTES FLASK
# ═══════════════════════════════════════════════════════════

def _as_bool(val, default=True):
    """Lit un booléen tolérant : Make.com envoie souvent « true »/« 0 » en texte."""
    if isinstance(val, bool):
        return val
    text = str(val or "").strip().lower()
    if not text:
        return default
    return text in ("1", "true", "yes", "oui", "vrai")


def skip_non_member(data):
    """Réponse à renvoyer quand le client n'est pas un membre, sinon None.

    Seuls les membres reçoivent un relevé : les fiches QuickBooks sans numéro de
    membre (« Pupilles », comptes internes, fournisseurs) doivent être écartées
    plutôt que de produire un PDF qui partira à la poste pour rien.

    On répond 200 — et non une erreur — pour que le scénario Make.com puisse
    filtrer sans que la branche parte en échec. Trois façons de filtrer côté
    Make : le champ `skipped` du corps, le Content-Type (JSON au lieu de PDF),
    ou l'en-tête X-Statement-Skipped.

    `require_member_number: false` dans le payload force la génération malgré
    tout, pour les cas hors série (test, relevé produit à la main).
    """
    if not _as_bool(data.get("require_member_number"), True):
        return None

    number = str(data.get("customer_member_number", "") or "").strip()
    if number and number != NO_MEMBER_NUMBER:
        return None

    customer = data.get("customer_name", "?")
    logger.info(f"[skip] '{customer}' — aucun numéro de membre, relevé non généré")
    resp = jsonify({
        "skipped": True,
        "reason": "no_member_number",
        "customer_name": data.get("customer_name", ""),
    })
    resp.headers["X-Statement-Skipped"] = "no_member_number"
    return resp, 200


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
            # « balance » facultatif : solde restant dû si des paiements ont
            # été appliqués. Absent → la facture est réputée impayée en entier.
            inv["balance"] = _to_float(inv.get("balance"), inv["total"])

        skipped = skip_non_member(data)
        if skipped:
            return skipped

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

        data["customer_member_number"] = extract_member_number(data, raw_invoices)
        data["customer_address"] = format_customer_address(data, raw_invoices)

        skipped = skip_non_member(data)
        if skipped:
            return skipped

        frais_retard_id = data.get("frais_retard_item_id", FRAIS_RETARD_ITEM_ID)
        balances, balance_source = resolve_balances(raw_invoices)
        data["balance_source"] = balance_source
        invoices = process_raw_invoices(raw_invoices, frais_retard_id, balances)
        data["aging"] = calculate_aging(raw_invoices, balances)

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
