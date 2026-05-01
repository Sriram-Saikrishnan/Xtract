import React, { useState, useEffect } from 'react';
import { Ic } from '../components/icons';
import StatusBadge from '../components/StatusBadge';
import { apiFetch, downloadExcel, fmt, fmtDate } from '../utils/formatters';

export default function Detail({ navigate, toast, invoiceId, jobId: propJobId }) {
  const [inv, setInv] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!invoiceId) { navigate('extractions'); return; }
    apiFetch(`/invoices/${invoiceId}`)
      .then(r => r.json())
      .then(d => { setInv(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [invoiceId]);

  if (loading) return (
    <div className="page">
      <div className="loading">
        <span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)', width: 20, height: 20 }}></span>
        Loading invoice…
      </div>
    </div>
  );

  if (!inv) return (
    <div className="page">
      <div className="empty">
        <p>Invoice not found.</p>
        <button className="btn btn-primary mt-2" onClick={() => navigate('extractions')}>Back</button>
      </div>
    </div>
  );

  const jid = inv.job_id || propJobId;
  const confPct = Math.round((inv.confidence_score || 0) * 100);
  const r = 32, circ = 2 * Math.PI * r;
  const dash = (confPct / 100) * circ;

  const fields = [
    { k: 'Invoice No.', v: inv.invoice_number },
    { k: 'Date', v: inv.invoice_date },
    { k: 'Challan No.', v: inv.challan_number },
    { k: 'Doc Type', v: inv.document_type },
    { k: 'Supplier', v: inv.supplier_name },
    { k: 'Supplier GSTIN', v: inv.supplier_gstin },
    { k: 'Supplier State', v: inv.supplier_state },
    { k: 'Buyer', v: inv.buyer_name },
    { k: 'Buyer GSTIN', v: inv.buyer_gstin },
    { k: 'Place of Supply', v: inv.place_of_supply },
    { k: 'Category', v: inv.category },
    { k: 'Tax Type', v: inv.tax_type },
    { k: 'Transport', v: inv.transport_name },
    { k: 'LR Number', v: inv.lr_number },
    { k: 'Vehicle No.', v: inv.vehicle_number },
    { k: 'E-Way Bill', v: inv.eway_bill_number },
  ].filter(f => f.v);

  const taxRows = [
    { k: 'Assessable Value', v: `₹${fmt(inv.assessable_value)}` },
    ...(inv.tax_type === 'IGST' ? [
      { k: `IGST (${inv.igst_percent}%)`, v: `₹${fmt(inv.igst_amount)}` },
    ] : [
      { k: `CGST (${inv.cgst_percent}%)`, v: `₹${fmt(inv.cgst_amount)}` },
      { k: `SGST (${inv.sgst_percent}%)`, v: `₹${fmt(inv.sgst_amount)}` },
    ]),
    ...(inv.pf_charges ? [{ k: 'P&F Charges', v: `₹${fmt(inv.pf_charges)}` }] : []),
    ...(inv.round_off ? [{ k: 'Round Off', v: `₹${fmt(inv.round_off)}` }] : []),
  ];

  return (
    <div className="page">
      <div className="breadcrumb">
        <a onClick={() => navigate('extractions')}>Extractions</a>
        <span className="sep">/</span>
        <span className="current">{inv.source_filename}</span>
      </div>

      <div className="page-header">
        <div>
          <h1 className="page-title" style={{ fontSize: 22 }}>{inv.source_filename}</h1>
          <p className="page-sub">{inv.supplier_name || 'Unknown supplier'} · {inv.invoice_date || '—'}</p>
        </div>
        <div className="row gap-2">
          <StatusBadge status={inv.status} />
          {jid && (
            <button className="btn btn-primary" onClick={() => downloadExcel(jid)}>
              <Ic.download style={{ width: 14, height: 14 }} /> Download Excel
            </button>
          )}
        </div>
      </div>

      {inv.flags && (
        <div className="banner banner-amber mb-3">
          <Ic.warn style={{ width: 18, height: 18, flexShrink: 0 }} />
          <div><strong>Flags:</strong> {inv.flags}</div>
        </div>
      )}

      <div className="detail-grid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          <div className="card section">
            <h3>Document Info</h3>
            <div className="row gap-4" style={{ alignItems: 'center' }}>
              <div style={{ position: 'relative', width: 84, height: 84, flexShrink: 0 }}>
                <svg width="84" height="84" style={{ transform: 'rotate(-90deg)' }}>
                  <circle cx="42" cy="42" r={r} fill="none" stroke="var(--surface)" strokeWidth="8" />
                  <circle cx="42" cy="42" r={r} fill="none" stroke="var(--green-2)" strokeWidth="8"
                    strokeDasharray={`${dash} ${circ - dash}`} strokeLinecap="round" />
                </svg>
                <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', textAlign: 'center' }}>
                  <div>
                    <div style={{ fontFamily: 'var(--mono)', fontWeight: 700, fontSize: 17, color: 'var(--green)' }}>{confPct}%</div>
                    <div style={{ fontSize: 9, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>conf.</div>
                  </div>
                </div>
              </div>
              <div className="doc-info-grid" style={{ flex: 1 }}>
                <div><div className="k">File</div><div className="v">{inv.source_filename}</div></div>
                <div><div className="k">Category</div><div className="v">{inv.category || '—'}</div></div>
                <div><div className="k">Status</div><div className="v">{inv.status}</div></div>
                <div><div className="k">Extracted</div><div className="v">{fmtDate(inv.extracted_at)}</div></div>
                <div><div className="k">Total Qty</div><div className="v text-mono">{inv.total_qty || 0}</div></div>
                <div><div className="k">Weight (kg)</div><div className="v text-mono">{inv.total_weight_kg || 0}</div></div>
              </div>
            </div>
          </div>

          {fields.length > 0 && (
            <div className="card section">
              <h3>Key Fields</h3>
              <div className="kv-edit">
                {fields.map(f => (
                  <React.Fragment key={f.k}>
                    <div className="k">{f.k}</div>
                    <div className="field"><span>{f.v}</span></div>
                  </React.Fragment>
                ))}
              </div>
            </div>
          )}

          <div className="card section">
            <h3>Tax Summary</h3>
            <div className="kv-edit">
              {taxRows.map(r => (
                <React.Fragment key={r.k}>
                  <div className="k">{r.k}</div>
                  <div className="field text-mono">{r.v}</div>
                </React.Fragment>
              ))}
            </div>
            <div style={{ marginTop: 14, borderTop: '0.5px solid var(--border)', paddingTop: 12 }}>
              <div className="totals-row grand">
                <span>Grand Total</span>
                <span className="num" style={{ color: 'var(--green)' }}>₹{fmt(inv.grand_total)}</span>
              </div>
            </div>
          </div>

          {inv.line_items && inv.line_items.length > 0 && (
            <div className="card section">
              <h3>Line Items ({inv.line_items.length})</h3>
              <div style={{ overflowX: 'auto' }}>
                <table className="line-tbl" style={{ minWidth: 600 }}>
                  <thead>
                    <tr>
                      <th>Sr</th>
                      <th>Description</th>
                      <th>HSN</th>
                      <th>Grade</th>
                      <th style={{ textAlign: 'right' }}>Qty</th>
                      <th style={{ textAlign: 'right' }}>Rate</th>
                      <th style={{ textAlign: 'right' }}>Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inv.line_items.map((item, i) => (
                      <tr key={i}>
                        <td className="text-mono muted">{item.sr_no || i + 1}</td>
                        <td>{item.description || '—'}</td>
                        <td className="text-mono">{item.hsn_sac_code || '—'}</td>
                        <td>{item.grade || '—'}</td>
                        <td className="num">{item.quantity?.toLocaleString() || 0}</td>
                        <td className="num">₹{fmt(item.rate)}</td>
                        <td className="num" style={{ fontWeight: 600 }}>₹{fmt(item.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ marginTop: 14, marginLeft: 'auto', maxWidth: 300 }}>
                <div className="totals-row"><span className="muted">Assessable Value</span><span className="num">₹{fmt(inv.assessable_value)}</span></div>
                {inv.tax_type === 'IGST'
                  ? <div className="totals-row"><span className="muted">IGST ({inv.igst_percent}%)</span><span className="num">₹{fmt(inv.igst_amount)}</span></div>
                  : <>
                      <div className="totals-row"><span className="muted">CGST ({inv.cgst_percent}%)</span><span className="num">₹{fmt(inv.cgst_amount)}</span></div>
                      <div className="totals-row"><span className="muted">SGST ({inv.sgst_percent}%)</span><span className="num">₹{fmt(inv.sgst_amount)}</span></div>
                    </>}
                <div className="totals-row grand"><span>Grand Total</span><span className="num" style={{ color: 'var(--green)' }}>₹{fmt(inv.grand_total)}</span></div>
              </div>
            </div>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="card section">
            <h3>Supplier Details</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13 }}>
              {[
                { k: 'Address', v: inv.supplier_address },
                { k: 'Phone', v: inv.supplier_phone },
                { k: 'Email', v: inv.supplier_email },
                { k: 'Bank', v: inv.supplier_bank },
                { k: 'Account', v: inv.supplier_account_number },
                { k: 'IFSC', v: inv.supplier_ifsc },
              ].filter(f => f.v).map(f => (
                <div key={f.k}>
                  <div style={{ color: 'var(--text-3)', fontSize: 11.5, marginBottom: 2 }}>{f.k}</div>
                  <div style={{ fontWeight: 500, wordBreak: 'break-word' }}>{f.v}</div>
                </div>
              ))}
              {!inv.supplier_address && !inv.supplier_phone && !inv.supplier_email && (
                <div className="muted">No supplier details extracted.</div>
              )}
            </div>
          </div>

          <div className="card section">
            <h3>Logistics</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13 }}>
              {[
                { k: 'Destination', v: inv.destination },
                { k: 'Transport', v: inv.transport_name },
                { k: 'LR No.', v: inv.lr_number },
                { k: 'Vehicle', v: inv.vehicle_number },
                { k: 'E-Way Bill', v: inv.eway_bill_number },
                { k: 'IRN', v: inv.irn_number },
              ].filter(f => f.v).map(f => (
                <div key={f.k}>
                  <div style={{ color: 'var(--text-3)', fontSize: 11.5, marginBottom: 2 }}>{f.k}</div>
                  <div style={{ fontWeight: 500, wordBreak: 'break-word' }}>{f.v}</div>
                </div>
              ))}
              {!inv.destination && !inv.transport_name && (
                <div className="muted">No logistics details extracted.</div>
              )}
            </div>
          </div>

          <div className="card section">
            <h3>Actions</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {jid && (
                <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} onClick={() => downloadExcel(jid)}>
                  <Ic.download style={{ width: 14, height: 14 }} /> Download Excel
                </button>
              )}
              <button className="btn btn-secondary" style={{ width: '100%', justifyContent: 'center' }} onClick={() => navigate('extractions')}>
                <Ic.arrow style={{ width: 14, height: 14, transform: 'rotate(180deg)' }} /> Back to extractions
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
