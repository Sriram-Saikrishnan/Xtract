import { useState, useEffect } from 'react';
import { Ic } from '../components/icons';
import { apiFetch, fmtDate } from '../utils/formatters';

export default function ProjectDetail({ navigate, toast, projectId }) {
  const [project, setProject] = useState(null);
  const [blueprints, setBlueprints] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [busyId, setBusyId] = useState(null);

  const load = () => {
    if (!projectId) { navigate('projects'); return; }
    Promise.all([
      apiFetch(`/projects/${projectId}`).then(r => r.json()),
      apiFetch(`/projects/${projectId}/blueprints`).then(r => r.json()),
    ]).then(([p, bps]) => {
      setProject(p);
      setBlueprints(bps);
      setLoading(false);
    }).catch(() => setLoading(false));
  };

  useEffect(load, [projectId]);

  const handleCreate = async () => {
    if (!newName.trim()) { toast('Blueprint name is required'); return; }
    try {
      const res = await apiFetch(`/projects/${projectId}/blueprints`, {
        method: 'POST',
        body: JSON.stringify({ name: newName.trim() }),
      });
      if (!res.ok) throw new Error('Create failed');
      const bp = await res.json();
      setNewName('');
      setCreating(false);
      navigate('blueprint-builder', { blueprintId: bp.id, projectId });
    } catch (e) {
      toast('Error: ' + e.message);
    }
  };

  const handleDelete = async (bp) => {
    setBusyId(bp.id);
    try {
      const res = await apiFetch(`/blueprints/${bp.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Delete failed');
      const data = await res.json();
      if (data.archived) {
        toast('Blueprint archived (has extraction history)');
        setBlueprints(prev => prev.map(b => b.id === bp.id ? { ...b, is_active: false } : b));
      } else {
        toast('Blueprint deleted');
        setBlueprints(prev => prev.filter(b => b.id !== bp.id));
      }
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return (
    <div className="page"><div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading…</div></div>
  );

  if (!project) return (
    <div className="page"><div className="empty"><p>Project not found.</p><button className="btn btn-primary mt-2" onClick={() => navigate('projects')}>Back</button></div></div>
  );

  return (
    <div className="page">
      <div className="breadcrumb">
        <a onClick={() => navigate('projects')}>Projects</a>
        <span className="sep">/</span>
        <span className="current">{project.name}</span>
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">{project.name}</h1>
          <p className="page-sub">{project.description || 'No description'}</p>
        </div>
        <div className="row gap-2">
          <button className="btn btn-secondary" onClick={() => navigate('project-form', { projectId })}>
            <Ic.edit style={{ width: 14, height: 14 }} /> Edit
          </button>
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            <Ic.plus style={{ width: 14, height: 14 }} /> New blueprint
          </button>
        </div>
      </div>

      {creating && (
        <div className="card mb-3">
          <div className="card-pad row gap-2">
            <input
              className="input"
              placeholder="Blueprint name"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              autoFocus
            />
            <button className="btn btn-primary" onClick={handleCreate}>Create</button>
            <button className="btn btn-ghost" onClick={() => { setCreating(false); setNewName(''); }}>Cancel</button>
          </div>
        </div>
      )}

      {blueprints.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon"><Ic.extract style={{ width: 22, height: 22 }} /></div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>No blueprints yet</div>
            <div style={{ fontSize: 12.5 }}>Create one to define what fields to extract.</div>
          </div>
        </div>
      ) : (
        <div className="file-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
          {blueprints.map(bp => (
            <div key={bp.id} className="file-card" onClick={() => navigate('blueprint-builder', { blueprintId: bp.id, projectId })}>
              <div className="file-card-head">
                <div className="file-card-name">{bp.name}</div>
                <div className="row gap-2">
                  {!bp.is_active && <span className="badge badge-gray">Archived</span>}
                  <span className="badge badge-blue">{bp.input_type}</span>
                </div>
              </div>
              {bp.description && <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginBottom: 10 }}>{bp.description}</div>}
              <div className="file-card-foot">
                <span className="muted" style={{ fontSize: 11.5 }}>Created {fmtDate(bp.created_at)}</span>
                <button
                  className="icon-btn"
                  style={{ width: 28, height: 28 }}
                  title="Delete blueprint"
                  disabled={busyId === bp.id}
                  onClick={e => { e.stopPropagation(); handleDelete(bp); }}
                >
                  {busyId === bp.id
                    ? <span className="spinner" style={{ width: 12, height: 12 }} />
                    : <Ic.trash style={{ width: 13, height: 13 }} />}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
