"""GSTIN flag registry and state codes — used by sheet_flagged.py and tax_validator.py."""

GSTIN_STATES: dict[str, str] = {
    "01": "Jammu & Kashmir",           "02": "Himachal Pradesh",
    "03": "Punjab",                    "04": "Chandigarh",
    "05": "Uttarakhand",               "06": "Haryana",
    "07": "Delhi",                     "08": "Rajasthan",
    "09": "Uttar Pradesh",             "10": "Bihar",
    "11": "Sikkim",                    "12": "Arunachal Pradesh",
    "13": "Nagaland",                  "14": "Manipur",
    "15": "Mizoram",                   "16": "Tripura",
    "17": "Meghalaya",                 "18": "Assam",
    "19": "West Bengal",               "20": "Jharkhand",
    "21": "Odisha",                    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",            "24": "Gujarat",
    "25": "Daman & Diu",               "26": "Dadra & Nagar Haveli",
    "27": "Maharashtra",               "28": "Andhra Pradesh (old)",
    "29": "Karnataka",                 "30": "Goa",
    "31": "Lakshadweep",               "32": "Kerala",
    "33": "Tamil Nadu",                "34": "Puducherry",
    "35": "Andaman & Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh",            "38": "Ladakh",
    "97": "Other Territory",           "99": "Centre Jurisdiction",
}

FLAG_REGISTRY: dict[str, dict] = {
    "LINE_ITEMS_MISMATCH": {
        "severity": "CRITICAL",
        "message": "Line item totals do not match the assessable value.",
        "action": "Re-check all line item amounts and assessable value. Correct before filing.",
    },
    "GST_CALC_MISMATCH": {
        "severity": "CRITICAL",
        "message": "Calculated tax amount does not match the tax amount on the invoice.",
        "action": "Verify tax rate × assessable value matches tax amount on invoice. Do not file until resolved.",
    },
    "GRAND_TOTAL_MISMATCH": {
        "severity": "CRITICAL",
        "message": "Computed grand total does not match the invoice grand total.",
        "action": "Verify all components (assessable + taxes + round-off) equal the grand total. Correct before filing.",
    },
    "DUPLICATE": {
        "severity": "CRITICAL",
        "message": "This invoice appears to be a duplicate of another invoice in the batch.",
        "action": "Check if this is a re-uploaded bill. Delete the duplicate before filing.",
    },
    "PLACE_OF_SUPPLY_UNREADABLE": {
        "severity": "WARNING",
        "message": "Place of supply could not be mapped to a state. Tax type validation skipped.",
        "action": "Correct the place of supply field on the invoice before filing.",
    },
    "WRONG_TAX_TYPE_IGST_ON_INTRASTATE": {
        "severity": "CRITICAL",
        "message": "Intra-state transaction has IGST instead of CGST+SGST.",
        "action": "Contact supplier for a corrected invoice with CGST+SGST. Do not file with wrong tax type.",
    },
    "WRONG_TAX_TYPE_CGST_ON_INTERSTATE": {
        "severity": "CRITICAL",
        "message": "Inter-state transaction has CGST+SGST instead of IGST.",
        "action": "Contact supplier for a corrected invoice with IGST. Do not file with wrong tax type.",
    },
    "SPLIT_TAX_CONFLICT": {
        "severity": "CRITICAL",
        "message": "Invoice has both IGST and CGST+SGST applied simultaneously — invalid under GST.",
        "action": "Verify with original invoice. Request corrected invoice from supplier.",
    },
    "TAX_RATE_UNUSUAL": {
        "severity": "WARNING",
        "message": "Tax rate is not a standard GST slab.",
        "action": "Verify tax rate against original invoice. Likely an OCR misread.",
    },
    "CGST_SGST_MISMATCH": {
        "severity": "WARNING",
        "message": "CGST and SGST rates or amounts are not equal, which is invalid under GST.",
        "action": "Verify CGST and SGST values against original invoice. They must always be equal.",
    },
    "ZERO_RATE_NONZERO_AMOUNT": {
        "severity": "WARNING",
        "message": "Tax rate is 0% but tax amount is non-zero.",
        "action": "Verify tax rate and amount against original invoice. Likely an extraction error.",
    },
    "ITC_AT_RISK_WRONG_TAX_TYPE": {
        "severity": "CRITICAL",
        "message": "ITC claim is at risk — wrong tax type applied on this invoice.",
        "action": "Obtain corrected invoice from supplier before claiming ITC.",
    },
    "ITC_WINDOW_EXPIRED": {
        "severity": "CRITICAL",
        "message": "Invoice is more than 180 days old. ITC claim window under Section 16(4) has likely expired.",
        "action": "Consult your CA before attempting ITC claim on this invoice.",
    },
    "INVOICE_DATE_FUTURE": {
        "severity": "CRITICAL",
        "message": "Invoice date is in the future — likely an OCR error.",
        "action": "Verify invoice date against original document. Correct before filing.",
    },
    "INVOICE_DATE_OLD": {
        "severity": "INFO",
        "message": "Invoice is more than 90 days old. ITC claim window will expire at 180 days.",
        "action": "Ensure ITC is claimed before the 180-day window expires.",
    },
}

SEVERITY_ORDER: dict[str, int] = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
