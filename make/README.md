# Scénarios Make — QuickBooks APFFQ

Dossier Make : « Fraises » (folderId 297101), équipe PAR Design (58943).

## Fichiers

| Fichier | Quoi |
|---|---|
| `frais-de-retard-v1-actif.notes.json` | Résumé de l'ancien scénario (id 4406014, désactivé le 29 oct. 2026), avec ses problèmes. |
| `frais-de-retard-v2.blueprint.json` + `.body.txt` | Brouillon v2 (14 oct. 2026) : mise à jour sparse par appel API, frais incrémentaux. Jamais déployé. |
| `frais-de-retard-v3.blueprint.json` | Version v3, **en production depuis le 29 oct. 2026** (id 5008892, le 1er et le 15 à 00:06). |
| `etats-de-compte.blueprint.json` | Export du scénario « QB - États de compte » (id 4607525), clé API masquée. |

## Règle des frais de retard

- Les intérêts courent dès le 11e jour après la date de facture (`chargeable_days = days_overdue - 10`).
- Ils deviennent exigibles à partir du 101e jour (aucun frais avant).
- Taux : 7 % par an, au prorata des jours (`taux_annuel`).
- Seules les factures de contribution sont visées (filtre « Contributions seulement »).
- Article QuickBooks « Frais de retard » = ItemRef 17, code de taxe 4 (**Exempt**). C'est le seul article de frais : l'article 18 (« Contribution test », compte Intérêts, supprimé le 29 oct. 2026) a été retiré du scénario de relevés et de la liste par défaut de `app.py`.

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

## Test du 29 octobre 2026 (client PAR Design, id 620)

Scénario v3 lancé deux fois, restreint au client 620 par la requête du premier module.

| Facture | Cas | Attendu | Obtenu |
|---|---|---|---|
| TEST2026-10-30 | paiement partiel 400 $ + note de crédit 200 $ | ligne 11,55 $ | 11,55 $, paiements toujours liés, taxes inchangées |
| TEST2026-10-32 | ancienne ligne de frais « 30 jours » (7 $) | ligne 18,41 $ pour 96 jours, ancienne ligne conservée | conforme |
| TEST2026-10-33 | contribution trop récente | rien | rien |
| TEST2026-10-34 | hors contribution (et trop récente) | rien | rien |

Second passage le même jour : aucune nouvelle ligne (jetons de version inchangés).

Non couverts par ce test : contribution simple sans paiement, ligne de rabais négative, facture hors contribution assez vieille pour passer la date.

## Pièges Make rencontrés (blueprint créé par l'API)

- `util:SetVariables` n'a plus de champ `scope` : le laisser fait échouer la validation.
- Une expression régulière dans une expression `{{ }}` fait échouer la validation (« 1 problem found », sans détail).
- `map()` n'accepte pas de clé imbriquée (`SalesItemLineDetail.ItemRef.value`), et les clés doivent être entre guillemets dans un blueprint. D'où l'itérateur + agrégateur filtré pour isoler les lignes de frais.
- Le module « Make an API call » s'appelle `quickbooks:MakeApiCall` et exige `"version": "v3"` dans le mapper.
- QuickBooks refuse (« erreur lors du calcul de la taxe ») une mise à jour partielle des lignes si on renvoie la ligne `SubTotalLineDetail`, le champ `CustomExtensions`, ou si on omet `GlobalTaxCalculation` et `TxnTaxDetail.TxnTaxCodeRef`.

## Mise en production (faite le 29 oct. 2026)

- v3 (5008892) : restriction au client de test retirée, horaire du 1er et du 15 à 00:06, activé. Prochain passage : 1er novembre 2026.
- Ancien scénario « Frais de retard ACTIF » (4406014) désactivé, conservé pour référence.
- Scénario temporaire de lecture, data store et structure de test supprimés.
- Reste à faire côté QuickBooks : supprimer ou neutraliser les factures de test du client PAR Design (TEST2026-10-30 à 34), sinon v3 leur ajoutera des frais à chaque passage.

## Simulation à blanc du 29 oct. 2026 (vraies factures, aucune écriture QB)

Copie du v3 sans le module POST, chaque facture écrite dans un data store Make (scénario et data store supprimés après coup). 139 factures de contribution de plus de 101 jours avec solde ; 974 opérations, 93 s.

- 137 vraies factures : toutes portent déjà une ligne v1 « Frais de retard (N jours) ». Le v3 n'ajoute que les jours écoulés depuis (14 ou 15 jours), de 0,06 $ à 19,91 $, environ 300 $ au total pour le passage du 1er novembre.
- Un paiement partiel réel (2026000132 : 23,60 $ restant sur 1 000,25 $) : base 20,53 $ hors taxes, frais 0,06 $, conforme à la règle du Code civil.
- Formule et base recalculées hors Make sur les 100 premiers enregistrements : aucun écart.
- Aucune ligne négative (rabais) dans les factures en retard ; ce cas reste non testé.
- Facture TEST2026-10-29 (client APFFQ, 1er mai 2025, sans ligne de frais) : 57,81 $ seraient ajoutés. À supprimer avec les autres factures de test.

**Bogue trouvé et corrigé (lignes mixtes v1 + v3).** Sur TEST2026-10-32 (ligne v1 « 30 jours » sans ServiceDate + ligne v3 avec ServiceDate), `last(sort(map(14.array; "service_date")))` renvoyait vide à cause de l'entrée sans date, donc le calcul retombait sur l'analyse du texte de la ligne v3 « 96 jours au 29-10-2026 », dont `parseNumber` faisait 9629102026. Résultat : 0 nouveau jour pour toujours. Après le 1er novembre, toutes les vraies factures auraient été dans ce cas au passage du 15. Correctif :

- module 14 : `service_date = ifempty(ServiceDate; "0000-00-00")` ;
- module 7 : `last_fee_date` = dernière date triée, vide si c'est « 0000-00-00 » ;
- module 8 : l'analyse du texte ne lit que le nombre avant « jours » (`split`), au cas où.

Vérifié par une seconde simulation : TEST2026-10-32 → 126 jours déjà facturés, 0 nouveau jour. Appliqué au scénario de production 5008892 le 29 oct. 2026.
