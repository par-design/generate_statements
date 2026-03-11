import json
payload = [
    {
        "url": "https://generate-statement-6cakq.ondigitalocean.app/generate-statement-raw",
        "method": "post",
        "contentType": "json",
        "inputMethod": "dataStructure",
        "shareCookies": false,
        "parseResponse": true,
        "allowRedirects": true,
        "stopOnHttpError": true,
        "bodyDataStructure": 270500,
        "dataStructureBodyContent": {
            "company_tps": "987654321 RT0001",
            "company_tvq": "9876543210 TQ0001",
            "company_name": "Association des producteurs de fraises\\net framboises du Québec",
            "raw_invoices": [
                "{\"Id\":\"626\",\"Job\":false,\"Line\":[{\"Id\":\"1\",\"LineNum\":1,\"Description\":\"Entente de Commandite APFFQ 2026-2028 - année 2026\",\"Amount\":4750,\"DetailType\":\"SalesItemLineDetail\",\"SalesItemLineDetail\":{\"ServiceDate\":\"2026-01-01T05:00:00.000Z\",\"ItemRef\":{\"value\":\"24\",\"name\":\"59052 - 00001:Entente de Commandite APFFQ 2026-2028 Majeur\"},\"ClassRef\":{\"value\":\"1000000007\",\"name\":\"Commandite industrie\"},\"UnitPrice\":4750,\"Qty\":1,\"ItemAccountRef\":{\"value\":\"49\",\"name\":\"Services\"},\"TaxCodeRef\":{\"value\":\"7\"}}},{\"Amount\":4750,\"DetailType\":\"SubTotalLineDetail\",\"SubTotalLineDetail\":{}}],\"Notes\":null,\"Title\":null,\"Active\":true,\"Mobile\":null,\"Suffix\":null,\"domain\":\"QBO\",\"sparse\":false,\"Balance\":5461.31,\"Deposit\":null,\"DueDate\":\"2025-12-18T05:00:00.000Z\",\"Taxable\":false,\"TxnDate\":\"2025-12-18T05:00:00.000Z\",\"BillAddr\":{\"Id\":\"717\",\"Line1\":\"256, Haut Rivière Nord\",\"City\":\"Saint-Césaire\",\"CountrySubDivisionCode\":\"Québec\",\"PostalCode\":\"J0L 1T0\"},\"MetaData\":{\"CreateTime\":\"2025-12-17T20:46:32.000Z\",\"LastUpdatedTime\":\"2025-12-18T16:31:16.000Z\"},\"ShipAddr\":{\"Id\":\"717\",\"Line1\":\"256, Haut Rivière Nord\",\"City\":\"Saint-Césaire\",\"CountrySubDivisionCode\":\"Québec\",\"PostalCode\":\"J0L 1T0\"},\"ShipDate\":null,\"TotalAmt\":5461.31,\"BillEmail\":null,\"DocNumber\":\"F30499\",\"GivenName\":\"Justine\",\"IsProject\":false,\"LinkedTxn\":[],\"SyncToken\":\"1\",\"FamilyName\":\"Massé\",\"MiddleName\":null,\"CompanyName\":\"Pépinière A. Massé\",\"CurrencyRef\":{\"value\":\"CAD\",\"name\":\"Dollar canadien\"},\"CustomField\":[],\"CustomerRef\":null,\"DisplayName\":\"Pépinière A. Massé\",\"EmailStatus\":null,\"PrintStatus\":null,\"PrivateNote\":null,\"TrackingNum\":null,\"CustomerMemo\":null,\"PrimaryPhone\":null,\"SalesTermRef\":{\"value\":\"3\",\"name\":\"Net 30\"},\"TxnTaxDetail\":{\"TotalTax\":711.31},\"__IMTINDEX__\":17,\"ShipMethodRef\":null,\"V4IDPseudonym\":\"002093fce64a36face4dce833d14c5e34876c0\",\"__IMTLENGTH__\":21,\"BillWithParent\":false,\"ClientEntityId\":\"0\",\"AllowIPNPayment\":null,\"BalanceWithJobs\":5461.31,\"CustomerTypeRef\":{\"value\":\"626466\"},\"PrimaryEmailAddr\":{\"Address\":\"jmasse@pepiniereamasse.com\"},\"PrintOnCheckName\":\"Pépinière A. Massé\",\"DefaultTaxCodeRef\":null,\"AllowOnlinePayment\":null,\"FullyQualifiedName\":\"Pépinière A. Massé\",\"GlobalTaxCalculation\":null,\"PrimaryTaxIdentifier\":null,\"AllowOnlineACHPayment\":null,\"SecondaryTaxIdentifier\":null,\"PreferredDeliveryMethod\":\"None\",\"AllowOnlineCreditCardPayment\":null}"
            ],
            "company_email": "apffq@upa.qc.ca",
            "company_phone": "450 679-0540 poste 8792",
            "customer_name": "Pépinière A. Massé",
            "company_address": "555 Bd Roland-Therrien, Longueuil, QC J4J 5J1",
            "customer_address": "256, Haut Rivière Nord Saint-Césaire, Québec, J0L 1T0",
            "frais_retard_item_id": "18",
            "customer_member_number": "626"
        },
        "requestCompressedContent": True
    }
]

with open("payload2.json", "w") as f:
    json.dump(payload[0]["dataStructureBodyContent"], f)
