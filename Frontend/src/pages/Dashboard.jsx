import { useState, useEffect } from 'react';
import { Ic } from '../components/icons';
import StatusBadge from '../components/StatusBadge';
import Sparkline from '../components/Sparkline';
import LineChart from '../components/LineChart';
import ConfirmDeleteModal from '../components/ConfirmDeleteModal';
import { apiFetch, fmtDate } from '../utils/formatters';

export default function Dashboard({ navigate, toast }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleteModal, setDeleteModal] = useState(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    apiFetch('/jobs')
      .then(r => r.json())
      .then(d => { setJobs(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const handleDelete = async () => {
    if (!deleteModal) return;
    setDeleting(true);
    try {
      const res = await apiFetch(`/job/${deleteModal.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Delete failed');
      setJobs(prev => prev.filter(j => j.id !== deleteModal.id));
      setDeleteModal(null);
      toast('Job deleted');
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setDeleting(false);
    }
  };

  const totalFiles = jobs.reduce((s, j) => s + (j.total_files || 0), 0);
  const totalOk    = jobs.reduce((s, j) => s + (j.verified_count || 0), 0);
  const totalFlag  = jobs.reduce((s, j) => s + (j.flagged_count || 0), 0);
  const totalErr   = jobs.reduce((s, j) => s + (j.error_count || 0), 0);
  const recentJobs = jobs.slice(0, 8);
  const sparkData  = jobs.slice(0, 13).reverse().map(j => j.total_files || 0);
  const sparkOk    = jobs.slice(0, 13).reverse().map(j => j.verified_count || 0);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-sub">Extraction summary across all jobs.</p>
        </div>
        <button className="btn btn-primary btn-lg" onClick={() => navigate('upload')}>
          <Ic.plus style={{ width: 16, height: 16 }} /> New Extraction
        </button>
      </div>

      <div className="stat-grid">
        <div className="card stat">
          <div className="stat-label"><Ic.doc style={{ width: 14, height: 14 }} />Total Files Uploaded</div>
          <div className="stat-value">{totalFiles.toLocaleString()}</div>
          <div className="stat-foot">
            <span className="trend trend-up">{jobs.length} jobs</span>
            <Sparkline data={sparkData.length ? sparkData : [0]} />
          </div>
        </div>
        <div className="card stat">
          <div className="stat-label"><Ic.check style={{ width: 14, height: 14 }} />Verified Invoices</div>
          <div className="stat-value">{totalOk.toLocaleString()}</div>
          <div className="stat-foot">
            <span className="trend trend-up">{totalOk + totalFlag > 0 ? Math.round(totalOk / (totalOk + totalFlag + totalErr) * 100) : 0}% success</span>
            <Sparkline data={sparkOk.length ? sparkOk : [0]} />
          </div>
        </div>
        <div className="card stat">
          <div className="stat-label"><Ic.warn style={{ width: 14, height: 14 }} />Flagged for Review</div>
          <div className="stat-value">{totalFlag.toLocaleString()}</div>
          <div className="stat-foot">
            <span className="trend trend-flat">needs review</span>
          </div>
        </div>
        <div className="card stat">
          <div className="stat-label"><Ic.trendUp style={{ width: 14, height: 14 }} />Extraction Errors</div>
          <div className="stat-value">{totalErr.toLocaleString()}</div>
          <div className="stat-foot">
            <span className="trend trend-down">{totalErr > 0 ? 'check logs' : 'all clear'}</span>
          </div>
        </div>
      </div>

      <div className="dash-grid">
        <div>
          <div className="card">
            <div className="card-head">
              <div>
                <h3 className="card-title">Recent Jobs</h3>
                <div className="card-sub">Latest {recentJobs.length} of {jobs.length} total</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => navigate('extractions')}>
                View all <Ic.arrow style={{ width: 14, height: 14 }} />
              </button>
            </div>
            {loading ? (
              <div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading…</div>
            ) : recentJobs.length === 0 ? (
              <div className="empty">
                <div className="empty-icon"><Ic.inbox style={{ width: 22, height: 22 }} /></div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>No extractions yet</div>
                <div style={{ fontSize: 12.5 }}>Upload your first invoice to get started.</div>
              </div>
            ) : (
              <table className="tbl">
                <thead>
                  <tr><th>Job ID</th><th>Files</th><th>Status</th><th>Verified</th><th>Flagged</th><th>Date</th><th style={{ width: 44 }}></th></tr>
                </thead>
                <tbody>
                  {recentJobs.map(j => (
                    <tr key={j.id} onClick={() => navigate('extractions', { jobId: j.id })}>
                      <td><span className="text-mono" style={{ fontSize: 12 }}>{j.id.slice(0, 8)}…</span></td>
                      <td className="text-mono muted" style={{ fontSize: 12.5 }}>{j.total_files}</td>
                      <td><StatusBadge status={j.status} /></td>
                      <td className="text-mono" style={{ color: 'var(--green)', fontWeight: 700 }}>{j.verified_count}</td>
                      <td className="text-mono" style={{ color: 'var(--amber)' }}>{j.flagged_count}</td>
                      <td className="muted" style={{ fontSize: 12.5 }}>{fmtDate(j.created_at)}</td>
                      <td style={{ padding: '0 10px' }}>
                        <button
                          className="icon-btn row-del"
                          style={{ width: 28, height: 28, borderRadius: 7, color: 'var(--text-3)' }}
                          title="Delete job"
                          onClick={e => { e.stopPropagation(); setDeleteModal(j); }}
                        >
                          <Ic.trash style={{ width: 13, height: 13 }} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {sparkData.length > 1 && (
            <div className="card mt-3">
              <div className="card-head">
                <h3 className="card-title">Files processed — last {sparkData.length} jobs</h3>
              </div>
              <div className="line-chart-wrap">
                <LineChart data={sparkData} height={160} />
              </div>
            </div>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div className="card">
            <div className="card-head"><h3 className="card-title">Quick actions</h3></div>
            <div className="card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} onClick={() => navigate('upload')}>
                <Ic.upload style={{ width: 14, height: 14 }} /> Upload new files
              </button>
              <button className="btn btn-secondary" style={{ width: '100%', justifyContent: 'center' }} onClick={() => navigate('extractions')}>
                <Ic.extract style={{ width: 14, height: 14 }} /> Browse extractions
              </button>
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h3 className="card-title">Status breakdown</h3></div>
            <div className="card-pad">
              {[
                { label: 'Verified', val: totalOk, color: 'var(--green)' },
                { label: 'Flagged', val: totalFlag, color: 'var(--amber)' },
                { label: 'Errors', val: totalErr, color: 'var(--red)' },
              ].map(row => (
                <div key={row.label} style={{ marginBottom: 12 }}>
                  <div className="row between mb-1">
                    <span style={{ fontSize: 13, color: 'var(--text-2)' }}>{row.label}</span>
                    <span className="text-mono" style={{ fontWeight: 700, color: row.color }}>{row.val}</span>
                  </div>
                  <div className="progress">
                    <div className="fill" style={{ width: `${totalOk + totalFlag + totalErr > 0 ? (row.val / (totalOk + totalFlag + totalErr)) * 100 : 0}%`, background: row.color }}></div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <ConfirmDeleteModal
        job={deleteModal}
        onConfirm={handleDelete}
        onCancel={() => !deleting && setDeleteModal(null)}
        loading={deleting}
      />
    </div>
  );
}
