import { useState, useEffect } from 'react';
import { apiFetch } from '../utils/formatters';

export default function ProjectForm({ navigate, toast, projectId }) {
  const isEdit = !!projectId;
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [industry, setIndustry] = useState('');
  const [docTypes, setDocTypes] = useState('');
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(isEdit);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isEdit) return;
    apiFetch(`/projects/${projectId}`)
      .then(r => r.json())
      .then(p => {
        setName(p.name || '');
        setDescription(p.description || '');
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [projectId]);

  const buildContext = () => {
    const parts = [];
    if (industry.trim()) parts.push(`Industry: ${industry.trim()}`);
    if (docTypes.trim()) parts.push(`Typical document types: ${docTypes.trim()}`);
    if (notes.trim()) parts.push(`Extraction notes: ${notes.trim()}`);
    return parts.join('\n');
  };

  const handleSubmit = async () => {
    if (!name.trim()) { toast('Project name is required'); return; }
    setSaving(true);
    try {
      const body = isEdit
        ? { name: name.trim(), description: description.trim() || null }
        : { name: name.trim(), description: description.trim() || null, context: buildContext() || null };
      const res = await apiFetch(isEdit ? `/projects/${projectId}` : '/projects', {
        method: isEdit ? 'PATCH' : 'POST',
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Save failed');
      }
      const data = await res.json();
      toast(isEdit ? 'Project updated' : 'Project created');
      navigate('project-detail', { projectId: data.id });
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return (
    <div className="page">
      <div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading…</div>
    </div>
  );

  return (
    <div className="page" style={{ maxWidth: 640 }}>
      <div className="breadcrumb">
        <a onClick={() => navigate('projects')}>Projects</a>
        <span className="sep">/</span>
        <span className="current">{isEdit ? 'Edit' : 'New'}</span>
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">{isEdit ? 'Edit project' : 'New project'}</h1>
          <p className="page-sub">Projects group blueprints and extraction jobs together.</p>
        </div>
      </div>

      <div className="card">
        <div className="card-pad">
          <div className="form-row">
            <label>Project name</label>
            <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Vendor Invoices" />
          </div>
          <div className="form-row">
            <label>Description</label>
            <input className="input" value={description} onChange={e => setDescription(e.target.value)} placeholder="Short description (optional)" />
          </div>

          {!isEdit && (
            <>
              <div className="divider" />
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-3)', marginBottom: 12, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Guided context (optional, helps extraction accuracy)
              </div>
              <div className="form-row">
                <label>Industry</label>
                <input className="input" value={industry} onChange={e => setIndustry(e.target.value)} placeholder="e.g. Indian manufacturing" />
              </div>
              <div className="form-row">
                <label>Typical document types</label>
                <input className="input" value={docTypes} onChange={e => setDocTypes(e.target.value)} placeholder="e.g. tax invoices, delivery challans" />
              </div>
              <div className="form-row">
                <label>Extraction notes</label>
                <textarea className="input" rows={3} value={notes} onChange={e => setNotes(e.target.value)} placeholder="Anything the extractor should know about these documents" />
              </div>
            </>
          )}
        </div>
      </div>

      <div className="row gap-2" style={{ justifyContent: 'flex-end', marginTop: 18 }}>
        <button className="btn btn-ghost" onClick={() => navigate('projects')}>Cancel</button>
        <button className="btn btn-primary" onClick={handleSubmit} disabled={saving}>
          {saving
            ? <><span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span> Saving…</>
            : isEdit ? 'Save changes' : 'Create project'}
        </button>
      </div>
    </div>
  );
}
