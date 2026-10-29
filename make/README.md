# Scénarios Make — QuickBooks APFFQ

Dossier Make : « Fraises » (folderId 297101), équipe PAR Design (58943).

## Fichiers

| Fichier | Quoi |
|---|---|
| `frais-de-retard-v1-actif.notes.json` | Résumé du scénario **actif** en production (id 4406014) au 2026-10-29, avec ses problèmes. |
| `frais-de-retard-v2.blueprint.json` + `.body.txt` | Brouillon v2 (14 oct. 2026) : mise à jour sparse par appel API, frais incrémentaux. Jamais déployé. |
| `frais-de-retard-v3.blueprint.json` | Version v3 construite à partir de v2, créée dans Make **inactive, en exécution manuelle**. |
| `etats-de-compte.blueprint.json` | Export du scénario « QB - États de compte » (id 4607525), clé API masquée. |

## Règle des frais de retard

- Les intérêts courent dès le 11e jour après la date de facture (`chargeable_days = days_overdue - 10`).
- Ils deviennent exigibles à partir du 101e jour (aucun frais avant).
- Taux : 7 % par an, au prorata des jours (`taux_annuel`).
- Seules les factures de contribution sont visées (filtre « Contributions seulement »).
- Article QuickBooks « Frais de retard » = ItemRef 17, code de taxe 4 (**Exempt**).

## Ce que corrige v3 par rapport au scénario actif (v1)

1. **Crédits et autres lignes conservés.** v1 remplaçait toutes les lignes par la première + la ligne de frais. v3 relit la facture brute par l'API (`GET invoice/{Id}`), renvoie toutes les lignes telles quelles et n'ajoute que la ligne de frais, avec un SyncToken frais.
2. **Paiement partiel / note de crédit appliquée.** La base de calcul est la contribution qui reste à payer, selon la règle d'imputation de l'art. 1570 C.c.Q. (un paiement partiel s'impute d'abord sur les intérêts, puis sur le capital) :
   - `paiements = TotalAmt - Balance` (paiements et notes de crédit appliqués) ;
   - `frais_impayés = max(0; frais_déjà_facturés - paiements)` ;
   - `base = Balance - frais_impayés`.
   Sans paiement, la base exclut les frais déjà facturés (pas d'intérêt sur intérêt, art. 1620 C.c.Q.). Avec un paiement, les frais sont réputés réglés en premier et le reste du solde est de la contribution.
   - **Hors taxes** : la base est ramenée au montant avant taxes avec la proportion réelle de la facture, `ratio_hors_taxes = (TotalAmt - frais - TotalTax) / (TotalAmt - frais)` (les frais étant exempts, toute la taxe porte sur la contribution). Exact même si une ligne est exempte, contrairement à un diviseur fixe de 1,14975.
3. **Frais incrémentaux.** Chaque passage ajoute une ligne « Frais de retard (N jours au JJ-MM-AAAA) » pour les jours non encore facturés, au lieu de recalculer et remplacer. Les lignes v1 (« Frais de retard (120 jours) », sans ServiceDate) sont reconnues par l'expression régulière pour ne pas refacturer les jours déjà couverts.
4. Le code de taxe des lignes existantes n'est plus écrasé.

## Décisions en attente (client)

- **Taxes** : réglé le 29 oct. 2026, les frais se calculent sur le montant hors taxes (voir plus haut). La ligne de frais reste exempte (code 4).
- **Paiement partiel** : réglé le 29 oct. 2026, règle du Code civil (art. 1570), voir plus haut.
- **Période avant un paiement partiel** : v3 facture les nouveaux jours sur le solde au moment du passage. Pour un calcul par segments (ancien solde jusqu'à la date du paiement), il faudrait lire les paiements liés (`LinkedTxn`) — non implémenté.

## Mise en production

1. Tester v3 à la main sur une facture de test (bac à sable ou facture connue), vérifier les lignes conservées et le montant.
2. Désactiver « Frais de retard ACTIF » (4406014).
3. Donner à v3 la planification du 1er et du 15 à 00:06, puis l'activer.
