import { Ic } from './icons';
import { fmtDate } from '../utils/formatters';

export default function ConfirmDeleteModal({ job, onConfirm, onCancel, loading }) {
  if (!job) return null;
  const extracted = (job.verified_count || 0) + (job.flagged_count || 0) + (job.error_count || 0);
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="row gap-3" style={{ marginBottom: 20 }}>
          <div style={{ width: 42, height: 42, borderRadius: 11, background: 'var(--red-soft)', display: 'grid', placeItems: 'center', flexShrink: 0 }}>
            <Ic.trash style={{ width: 18, height: 18, color: 'var(--red)' }} />
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 16, letterSpacing: '-0.02em', marginBottom: 2 }}>Delete this job?</div>
            <div style={{ color: 'var(--text-3)', fontSize: 12.5 }}>This action cannot be undone</div>
          </div>
        </div>

        <div style={{ background: 'var(--surface)', border: '0.5px solid var(--border)', borderRadius: 10, padding: '12px 14px', marginBottom: 18 }}>
          {[
            ['Job ID', <span className="text-mono" style={{ fontSize: 12 }}>{job.id.slice(0, 8)}…</span>],
            ['Files uploaded', <span className="text-mono">{job.total_files}</span>],
            ['Invoices extracted', <span className="text-mono">{extracted}</span>],
            ['Created', fmtDate(job.created_at)],
          ].map(([k, v]) => (
            <div key={k} className="row between" style={{ fontSize: 13, marginBottom: 6 }}>
              <span style={{ color: 'var(--text-3)' }}>{k}</span>
              <span>{v}</span>
            </div>
          ))}
        </div>

        <p style={{ fontSize: 13, color: 'var(--text-2)', margin: '0 0 22px', lineHeight: 1.6 }}>
          All extracted invoices, line items, and the Excel report for this job will be permanently deleted.
          {job.excel_ready ? ' Any report not yet downloaded will be lost.' : ''}
        </p>

        <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
          <button className="btn btn-ghost" onClick={onCancel} disabled={loading}>Cancel</button>
          <button className="btn btn-danger" onClick={onConfirm} disabled={loading} style={{ minWidth: 120, justifyContent: 'center' }}>
            {loading
              ? <><span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span> Deleting…</>
              : <><Ic.trash style={{ width: 13, height: 13 }} /> Delete job</>}
          </button>
        </div>
      </div>
    </div>
  );
}
