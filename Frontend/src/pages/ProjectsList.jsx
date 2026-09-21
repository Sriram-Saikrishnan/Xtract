import { useState, useEffect } from 'react';
import { Ic } from '../components/icons';
import { apiFetch, fmtDate } from '../utils/formatters';

export default function ProjectsList({ navigate, toast }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleteModal, setDeleteModal] = useState(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    apiFetch('/projects')
      .then(r => r.json())
      .then(d => { setProjects(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const handleDelete = async () => {
    if (!deleteModal) return;
    setDeleting(true);
    try {
      const res = await apiFetch(`/projects/${deleteModal.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Delete failed');
      setProjects(prev => prev.filter(p => p.id !== deleteModal.id));
      setDeleteModal(null);
      toast('Project deleted');
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
          <h1 className="page-title">Projects</h1>
          <p className="page-sub">Group blueprints and extraction jobs by project.</p>
        </div>
        <button className="btn btn-primary btn-lg" onClick={() => navigate('project-form')}>
          <Ic.plus style={{ width: 16, height: 16 }} /> New Project
        </button>
      </div>

      {loading ? (
        <div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading projects…</div>
      ) : projects.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon"><Ic.extract style={{ width: 22, height: 22 }} /></div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>No projects yet</div>
            <div style={{ fontSize: 12.5 }}>Create a project to start building blueprints.</div>
          </div>
        </div>
      ) : (
        <div className="file-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
          {projects.map(p => (
            <div key={p.id} className="file-card" onClick={() => navigate('project-detail', { projectId: p.id })}>
              <div className="file-card-head">
                <div className="file-card-name">{p.name}</div>
                <button
                  className="icon-btn"
                  style={{ width: 28, height: 28 }}
                  title="Delete project"
                  onClick={e => { e.stopPropagation(); setDeleteModal(p); }}
                >
                  <Ic.trash style={{ width: 13, height: 13 }} />
                </button>
              </div>
              {p.description && <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginBottom: 10 }}>{p.description}</div>}
              <div className="file-card-foot">
                <span className="muted" style={{ fontSize: 11.5 }}>Created {fmtDate(p.created_at)}</span>
                <span className="link-green">Open →</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {deleteModal && (
        <div className="modal-overlay" onClick={() => !deleting && setDeleteModal(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="row gap-3" style={{ marginBottom: 20 }}>
              <div style={{ width: 42, height: 42, borderRadius: 11, background: 'var(--red-soft)', display: 'grid', placeItems: 'center', flexShrink: 0 }}>
                <Ic.trash style={{ width: 18, height: 18, color: 'var(--red)' }} />
              </div>
              <div>
                <div style={{ fontWeight: 700, fontSize: 16, letterSpacing: '-0.02em', marginBottom: 2 }}>Delete "{deleteModal.name}"?</div>
                <div style={{ color: 'var(--text-3)', fontSize: 12.5 }}>This deletes all its blueprints and jobs too.</div>
              </div>
            </div>
            <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" onClick={() => setDeleteModal(null)} disabled={deleting}>Cancel</button>
              <button className="btn btn-danger" onClick={handleDelete} disabled={deleting} style={{ minWidth: 100, justifyContent: 'center' }}>
                {deleting ? <span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span> : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
