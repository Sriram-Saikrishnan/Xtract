import { useState, useEffect } from 'react';
import { Ic } from '../components/icons';
import StatusBadge from '../components/StatusBadge';
import ConfBadge from '../components/ConfBadge';
import { apiFetch, downloadExcel } from '../utils/formatters';

function humanize(key) {
  return String(key || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

export default function ResultDetail({ navigate, toast, resultId, jobId: propJobId }) {
  const [result, setResult] = useState(null);
  const [fields, setFields] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!resultId) { navigate('dashboard'); return; }
    (async () => {
      try {
        const res = await apiFetch(`/extraction-results/${resultId}`);
        if (!res.ok) { setLoading(false); return; }
        const r = await res.json();
        setResult(r);
        setDraft(r.data || {});
        if (r.blueprint_id) {
          const bpRes = await apiFetch(`/blueprints/${r.blueprint_id}`);
          if (bpRes.ok) {
            const bp = await bpRes.json();
            setFields((bp.fields || []).slice().sort((a, b) => a.sort_order - b.sort_order));
          }
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [resultId]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await apiFetch(`/extraction-results/${resultId}/correct`, {
        method: 'POST', body: JSON.stringify({ data: draft }),
      });
      if (!res.ok) throw new Error('Save failed');
      const updated = await res.json();
      setResult(updated);
      setEditing(false);
      toast('Result updated');
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return (
    <div className="page"><div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading…</div></div>
  );
  if (!result) return (
    <div className="page"><div className="empty"><p>Result not found.</p><button className="btn btn-primary mt-2" onClick={() => navigate('dashboard')}>Back</button></div></div>
  );

  const jid = result.job_id || propJobId;
  const scalarFields = fields.filter(f => f.field_type !== 'table');
  const tableFields = fields.filter(f => f.field_type === 'table');

  const renderTable = (f) => {
    const value = draft[f.field_key];
    const rows = Array.isArray(value) ? value : [];
    const columns = f.table_columns?.length ? f.table_columns : (rows[0] ? Object.keys(rows[0]).map(k => ({ key: k })) : []);
    return (
      <div style={{ overflowX: 'auto' }}>
        <table className="line-tbl">
          <thead><tr>{columns.map(c => <th key={c.key}>{humanize(c.key)}</th>)}</tr></thead>
          <tbody>
            {rows.length === 0
              ? <tr><td className="muted">No rows</td></tr>
              : rows.map((row, i) => (
                <tr key={i}>
                  {columns.map(c => (
                    <td key={c.key}>
                      {editing ? (
                        <input
                          className="input"
                          value={row[c.key] ?? ''}
                          onChange={e => {
                            const newRows = rows.map((r, ri) => ri === i ? { ...r, [c.key]: e.target.value } : r);
                            setDraft(prev => ({ ...prev, [f.field_key]: newRows }));
                          }}
                        />
                      ) : (row[c.key] === null || row[c.key] === undefined || row[c.key] === '' ? '—' : String(row[c.key]))}
                    </td>
                  ))}
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    );
  };

  const renderBoxValue = (f) => {
    const value = draft[f.field_key];
    if (editing) {
      return (
        <input
          className="input"
          value={value ?? ''}
          onChange={e => setDraft(prev => ({ ...prev, [f.field_key]: e.target.value }))}
        />
      );
    }
    return <span>{value === null || value === undefined || value === '' ? '—' : String(value)}</span>;
  };

  return (
    <div className="page">
      <div className="breadcrumb">
        <a onClick={() => navigate('dashboard')}>Dashboard</a>
        {jid && <><span className="sep">/</span><a onClick={() => navigate('results', { jobId: jid })}>Results</a></>}
        <span className="sep">/</span>
        <span className="current">{result.source_filename}</span>
      </div>

      <div className="page-header">
        <div>
          <h1 className="page-title" style={{ fontSize: 22 }}>{result.source_filename}</h1>
          <p className="page-sub">Extracted {result.extracted_at ? new Date(result.extracted_at).toLocaleString() : '—'}</p>
        </div>
        <div className="row gap-2">
          <StatusBadge status={result.status} />
          <ConfBadge value={result.confidence_score} />
          {jid && (
            <button className="btn btn-secondary" onClick={() => downloadExcel(jid).catch(() => toast('Download failed'))}>
              <Ic.download style={{ width: 14, height: 14 }} /> Excel
            </button>
          )}
          {editing ? (
            <>
              <button className="btn btn-ghost" onClick={() => { setEditing(false); setDraft(result.data || {}); }} disabled={saving}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? <span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span> : 'Save changes'}
              </button>
            </>
          ) : (
            <button className="btn btn-primary" onClick={() => setEditing(true)}>
              <Ic.edit style={{ width: 14, height: 14 }} /> Edit
            </button>
          )}
        </div>
      </div>

      {result.status === 'failed' && result.data?._error && (
        <div className="banner banner-amber mb-3">
          <Ic.warn style={{ width: 18, height: 18, flexShrink: 0 }} />
          <div><strong>Extraction failed:</strong> {result.data._error}</div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {scalarFields.length > 0 && (
          <div className="card section">
            <h3>Fields</h3>
            <div className="field-box-grid">
              {scalarFields.map(f => (
                <div key={f.id} className="field-box">
                  <div className="k">{f.display_label}{f.is_required && <span style={{ color: 'var(--red)' }}> *</span>}</div>
                  <div className="v">{renderBoxValue(f)}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {tableFields.map(f => {
          const rowCount = Array.isArray(draft[f.field_key]) ? draft[f.field_key].length : 0;
          return (
            <div key={f.id} className="card section">
              <h3 className="row between" style={{ marginBottom: 14 }}>
                <span>{f.display_label}</span>
                <span className="badge badge-blue" style={{ textTransform: 'none', letterSpacing: 0 }}>{rowCount} row{rowCount !== 1 ? 's' : ''}</span>
              </h3>
              {renderTable(f)}
            </div>
          );
        })}

        {fields.length === 0 && (
          <div className="card section">
            <h3>Raw data</h3>
            <pre style={{ fontSize: 12, overflowX: 'auto' }}>{JSON.stringify(result.data, null, 2)}</pre>
          </div>
        )}
      </div>
    </div>
  );
}
