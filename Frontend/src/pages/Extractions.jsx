import { useState, useEffect } from 'react';
import { Ic } from '../components/icons';
import StatusBadge from '../components/StatusBadge';
import ConfBadge from '../components/ConfBadge';
import FileIcon from '../components/FileIcon';
import ConfirmDeleteModal from '../components/ConfirmDeleteModal';
import { apiFetch, downloadExcel, fmt, fmtDate } from '../utils/formatters';

export default function Extractions({ navigate, toast, highlightJobId }) {
  const [jobs, setJobs] = useState([]);
  const [expanded, setExpanded] = useState({});
  const [invoices, setInvoices] = useState({});
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [loadingInv, setLoadingInv] = useState({});
  const [search, setSearch] = useState('');
  const [deleteModal, setDeleteModal] = useState(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    apiFetch('/jobs')
      .then(r => r.json())
      .then(d => {
        setJobs(d);
        setLoadingJobs(false);
        if (highlightJobId) {
          setExpanded({ [highlightJobId]: true });
          fetchInvoices(highlightJobId);
        }
      })
      .catch(() => setLoadingJobs(false));
  }, [highlightJobId]);

  const fetchInvoices = async (jid) => {
    if (invoices[jid] || loadingInv[jid]) return;
    setLoadingInv(prev => ({ ...prev, [jid]: true }));
    try {
      const res = await apiFetch(`/jobs/${jid}/invoices`);
      const data = await res.json();
      setInvoices(prev => ({ ...prev, [jid]: data }));
    } catch {}
    setLoadingInv(prev => ({ ...prev, [jid]: false }));
  };

  const toggle = (jid) => {
    const isOpen = expanded[jid];
    setExpanded(prev => ({ ...prev, [jid]: !isOpen }));
    if (!isOpen) fetchInvoices(jid);
  };

  const handleDelete = async () => {
    if (!deleteModal) return;
    setDeleting(true);
    try {
      const res = await apiFetch(`/job/${deleteModal.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Delete failed');
      setJobs(prev => prev.filter(j => j.id !== deleteModal.id));
      setInvoices(prev => { const n = { ...prev }; delete n[deleteModal.id]; return n; });
      setExpanded(prev => { const n = { ...prev }; delete n[deleteModal.id]; return n; });
      setDeleteModal(null);
      toast('Job deleted');
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Extractions</h1>
          <p className="page-sub">All jobs and extracted invoices.</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('upload')}>
          <Ic.plus style={{ width: 14, height: 14 }} /> New batch
        </button>
      </div>

      <div className="filter-bar">
        <div className="search">
          <Ic.search style={{ width: 16, height: 16, color: 'var(--text-3)' }} />
          <input placeholder="Search supplier or invoice…" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
      </div>

      {loadingJobs ? (
        <div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading jobs…</div>
      ) : jobs.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon"><Ic.extract style={{ width: 22, height: 22 }} /></div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>No extractions yet</div>
            <div style={{ fontSize: 12.5 }}>Upload invoices to see results here.</div>
          </div>
        </div>
      ) : jobs.map(job => {
        const isOpen = expanded[job.id];
        const jobInvs = (invoices[job.id] || []).filter(inv =>
          !search ||
          (inv.supplier_name || '').toLowerCase().includes(search.toLowerCase()) ||
          (inv.invoice_number || '').toLowerCase().includes(search.toLowerCase())
        );
        const dateStr = fmtDate(job.created_at);
        return (
          <div key={job.id} style={{ marginBottom: 22 }}>
            <div className="batch-head" onClick={() => toggle(job.id)}>
              <div className="batch-meta">
                {isOpen ? <Ic.chevronD style={{ width: 16, height: 16, color: 'var(--text-3)' }} /> : <Ic.chevronR style={{ width: 16, height: 16, color: 'var(--text-3)' }} />}
                <div className="batch-name">Job · {dateStr}</div>
                <span className="badge badge-gray">{job.total_files} file{job.total_files !== 1 ? 's' : ''}</span>
                <StatusBadge status={job.status} />
                {job.verified_count > 0 && <span className="badge badge-green">{job.verified_count} verified</span>}
                {job.flagged_count > 0 && <span className="badge badge-amber">{job.flagged_count} flagged</span>}
              </div>
              <div className="row gap-2">
                <span className="text-mono muted" style={{ fontSize: 12 }}>{job.id.slice(0, 8)}…</span>
                {job.excel_ready && (
                  <button className="btn btn-secondary btn-sm" onClick={e => { e.stopPropagation(); downloadExcel(job.id).catch(() => toast('Download failed')); }}>
                    <Ic.download style={{ width: 13, height: 13 }} /> Excel
                  </button>
                )}
                <button
                  className="btn btn-ghost btn-sm"
                  style={{ color: 'var(--text-3)', padding: '5px 8px', borderRadius: 8 }}
                  title="Delete job"
                  onClick={e => { e.stopPropagation(); setDeleteModal(job); }}
                >
                  <Ic.trash style={{ width: 13, height: 13 }} />
                </button>
              </div>
            </div>

            {isOpen && (
              loadingInv[job.id] ? (
                <div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading invoices…</div>
              ) : jobInvs.length === 0 ? (
                <div style={{ padding: '24px', color: 'var(--text-3)', fontSize: 13, textAlign: 'center' }}>
                  {search ? 'No invoices match your search.' : 'No invoices extracted for this job.'}
                </div>
              ) : (
                <div className="file-grid">
                  {jobInvs.map(inv => {
                    const isFailed = inv.status === 'failed' || inv.status === 'error';
                    return (
                      <div
                        key={inv.id}
                        className={`file-card ${isFailed ? 'failed' : ''}`}
                        onClick={() => !isFailed && navigate('detail', { invoiceId: inv.id, jobId: job.id })}
                      >
                        <div className="file-card-head">
                          <FileIcon type={inv.source_filename?.split('.').pop().toLowerCase() || 'pdf'} size="lg" />
                          <StatusBadge status={inv.status} />
                        </div>
                        <div className="file-card-name mb-2">{inv.source_filename || 'Unknown'}</div>
                        <div className="kv-grid">
                          <div className="kv-row"><span className="k">Invoice</span><span className="v">{inv.invoice_number || '—'}</span></div>
                          <div className="kv-row"><span className="k">Supplier</span><span className="v">{inv.supplier_name || '—'}</span></div>
                          <div className="kv-row"><span className="k">Total</span><span className="v amount">₹{fmt(inv.grand_total)}</span></div>
                        </div>
                        <div className="file-card-foot">
                          {inv.confidence_score ? <ConfBadge value={inv.confidence_score} /> : <span className="muted" style={{ fontSize: 11.5 }}>—</span>}
                          {isFailed
                            ? <span className="link-green" style={{ color: 'var(--red)' }}>Failed</span>
                            : <span className="link-green">View details →</span>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )
            )}
          </div>
        );
      })}

      <ConfirmDeleteModal
        job={deleteModal}
        onConfirm={handleDelete}
        onCancel={() => !deleting && setDeleteModal(null)}
        loading={deleting}
      />
    </div>
  );
}
