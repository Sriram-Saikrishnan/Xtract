import { useState, useEffect } from 'react';
import { Ic } from '../components/icons';
import StatusBadge from '../components/StatusBadge';
import ConfBadge from '../components/ConfBadge';
import { apiFetch, downloadExcel } from '../utils/formatters';

export default function ResultsList({ navigate, toast, jobId }) {
  const [job, setJob] = useState(null);
  const [results, setResults] = useState([]);
  const [fields, setFields] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!jobId) { navigate('dashboard'); return; }
    (async () => {
      try {
        const [statusRes, resultsRes] = await Promise.all([
          apiFetch(`/status/${jobId}`),
          apiFetch(`/jobs/${jobId}/results`),
        ]);
        const statusData = statusRes.ok ? await statusRes.json() : null;
        const resultsData = resultsRes.ok ? await resultsRes.json() : [];
        setJob(statusData);
        setResults(resultsData);

        const blueprintId = resultsData.find(r => r.blueprint_id)?.blueprint_id;
        if (blueprintId) {
          const bpRes = await apiFetch(`/blueprints/${blueprintId}`);
          if (bpRes.ok) {
            const bp = await bpRes.json();
            setFields((bp.fields || []).slice().sort((a, b) => a.sort_order - b.sort_order));
          }
        }
      } catch {
        // fall through to empty state
      } finally {
        setLoading(false);
      }
    })();
  }, [jobId]);

  if (loading) return (
    <div className="page"><div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading results…</div></div>
  );

  const scalarFields = fields.filter(f => f.field_type !== 'table');
  const tableFields = fields.filter(f => f.field_type === 'table');

  return (
    <div className="page">
      <div className="breadcrumb">
        <a onClick={() => navigate('dashboard')}>Dashboard</a>
        <span className="sep">/</span>
        <span className="current">Results</span>
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Extraction results</h1>
          <p className="page-sub">
            {job ? <>{job.total_files} file{job.total_files !== 1 ? 's' : ''} · <StatusBadge status={job.status} /></> : `Job ${jobId?.slice(0, 8)}…`}
          </p>
        </div>
        {job?.excel_ready && (
          <button className="btn btn-primary" onClick={() => downloadExcel(jobId).catch(() => toast('Download failed'))}>
            <Ic.download style={{ width: 14, height: 14 }} /> Download Excel
          </button>
        )}
      </div>

      {results.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon"><Ic.extract style={{ width: 22, height: 22 }} /></div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>No results yet</div>
            <div style={{ fontSize: 12.5 }}>Results appear here as pages finish extracting.</div>
          </div>
        </div>
      ) : (
        <div className="card" style={{ overflowX: 'auto' }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Source File</th>
                {scalarFields.map(f => <th key={f.id}>{f.display_label}</th>)}
                {tableFields.map(f => <th key={f.id}>{f.display_label}</th>)}
                <th>Confidence</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {results.map(r => (
                <tr key={r.id} onClick={() => navigate('result-detail', { resultId: r.id })}>
                  <td className="file-cell">{r.source_filename || 'Unknown'}</td>
                  {scalarFields.map(f => {
                    const val = r.data ? r.data[f.field_key] : null;
                    return <td key={f.id}>{val === null || val === undefined || val === '' ? '—' : String(val)}</td>;
                  })}
                  {tableFields.map(f => {
                    const rows = r.data ? r.data[f.field_key] : null;
                    const count = Array.isArray(rows) ? rows.length : 0;
                    return (
                      <td key={f.id}>
                        {count > 0
                          ? <span className="badge badge-blue">{count} row{count !== 1 ? 's' : ''}</span>
                          : <span className="muted">—</span>}
                      </td>
                    );
                  })}
                  <td>{r.status === 'done' ? <ConfBadge value={r.confidence_score} /> : <span className="muted">—</span>}</td>
                  <td><StatusBadge status={r.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
