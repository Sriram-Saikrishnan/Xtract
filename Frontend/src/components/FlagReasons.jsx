import { useState, useMemo } from 'react';
import { Ic } from './icons';

// Human-readable label + short action for each flag code the backend emits.
// Mirrors Backend/app/core/gstin_validator.py FLAG_REGISTRY.
const FLAG_META = {
  LINE_ITEMS_MISMATCH:              { sev: 'critical', label: 'Line items don’t match assessable value', action: 'Re-check line item amounts and the assessable value.' },
  GST_CALC_MISMATCH:                { sev: 'critical', label: 'Tax amount mismatch',                     action: 'Verify tax rate × assessable value against the invoice.' },
  GRAND_TOTAL_MISMATCH:             { sev: 'critical', label: 'Grand total mismatch',                    action: 'Check that all components sum to the grand total.' },
  DUPLICATE:                        { sev: 'critical', label: 'Possible duplicate invoice',              action: 'Check for a re-uploaded bill; remove the duplicate.' },
  PLACE_OF_SUPPLY_UNREADABLE:       { sev: 'warning',  label: 'Place of supply unreadable',              action: 'Correct the place of supply field before filing.' },
  WRONG_TAX_TYPE_IGST_ON_INTRASTATE:{ sev: 'critical', label: 'IGST used on intra-state supply',         action: 'Request a corrected invoice with CGST+SGST.' },
  WRONG_TAX_TYPE_CGST_ON_INTERSTATE:{ sev: 'critical', label: 'CGST+SGST used on inter-state supply',     action: 'Request a corrected invoice with IGST.' },
  SPLIT_TAX_CONFLICT:               { sev: 'critical', label: 'Both IGST and CGST+SGST applied',          action: 'Verify against the original invoice.' },
  TAX_RATE_UNUSUAL:                 { sev: 'warning',  label: 'Non-standard tax rate',                   action: 'Verify the tax rate against the original invoice.' },
  CGST_SGST_MISMATCH:               { sev: 'warning',  label: 'CGST and SGST are not equal',             action: 'CGST and SGST must always be equal.' },
  ZERO_RATE_NONZERO_AMOUNT:         { sev: 'warning',  label: '0% rate but non-zero tax',                action: 'Verify the tax rate and amount.' },
  ITC_AT_RISK_WRONG_TAX_TYPE:       { sev: 'critical', label: 'ITC at risk — wrong tax type',            action: 'Obtain a corrected invoice before claiming ITC.' },
  ITC_WINDOW_EXPIRED:               { sev: 'critical', label: 'ITC window likely expired (>180 days)',    action: 'Consult your CA before claiming ITC.' },
  INVOICE_DATE_FUTURE:              { sev: 'critical', label: 'Invoice date is in the future',           action: 'Verify the invoice date against the document.' },
  INVOICE_DATE_OLD:                 { sev: 'info',     label: 'Invoice older than 90 days',              action: 'Claim ITC before the 180-day window closes.' },
};

const SEV_RANK = { critical: 0, warning: 1, info: 2 };

function parseFlags(flagsStr) {
  return flagsStr
    .split(';')
    .map(s => s.trim())
    .filter(Boolean)
    .map(raw => {
      if (FLAG_META[raw]) return { code: raw, ...FLAG_META[raw] };
      // Dynamic duplicate message, e.g. "DUPLICATE of file.jpg (Invoice: INV-001)"
      if (raw.toUpperCase().startsWith('DUPLICATE')) return { code: 'DUPLICATE', ...FLAG_META.DUPLICATE };
      return { code: raw, sev: 'warning', label: raw, action: 'Review manually.' };
    })
    .sort((a, b) => SEV_RANK[a.sev] - SEV_RANK[b.sev]);
}

export default function FlagReasons({ flags }) {
  const [open, setOpen] = useState(false);
  const parsed = useMemo(() => parseFlags(flags || ''), [flags]);
  if (parsed.length === 0) return null;

  const top = parsed[0];
  const extra = parsed.length - 1;

  return (
    // Stop propagation so expanding a reason never triggers the card's navigate.
    <div className={`flag-reasons sev-${top.sev}`} onClick={e => e.stopPropagation()}>
      <button
        type="button"
        className="flag-reason-head"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        title={parsed.map(f => f.label).join(' • ')}
      >
        <Ic.warn className="flag-ico" />
        <span className="flag-reason-text">{top.label}</span>
        {extra > 0 && !open && <span className="flag-more">+{extra}</span>}
        <Ic.chevronD className={`flag-chev ${open ? 'up' : ''}`} />
      </button>
      {open && (
        <ul className="flag-reason-list">
          {parsed.map((f, i) => (
            <li key={i} className={`sev-${f.sev}`}>
              <span className="flag-dot" />
              <div>
                <div className="flag-li-label">{f.label}</div>
                {f.action && <div className="flag-li-action">{f.action}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
