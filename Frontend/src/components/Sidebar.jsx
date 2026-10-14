import { Ic } from './icons';
import { useAuth } from '../context/AuthContext';

const PROJECTS_PAGES = new Set(['projects', 'project-form', 'project-detail', 'blueprint-builder']);
const RESULTS_PAGES = new Set(['results', 'result-detail']);

export default function Sidebar({ page, navigate, activeJobId }) {
  const { user, logout } = useAuth();
  const isActive = (id) => {
    if (id === 'projects') return PROJECTS_PAGES.has(page);
    if (id === 'dashboard') return page === 'dashboard' || RESULTS_PAGES.has(page);
    return page === id;
  };

  const top = [
    { id: 'dashboard', label: 'Dashboard', icon: Ic.dashboard },
    { id: 'projects', label: 'Projects', icon: Ic.extract },
    { id: 'upload', label: 'Upload', icon: Ic.upload },
  ];
  const bot = [
    { id: 'profile', label: 'Profile', icon: Ic.user },
  ];

  const initials = user?.name
    ? user.name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2)
    : (user?.email?.[0] || 'U').toUpperCase();

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-dot"></span>
        <span className="brand-name">Xtract</span>
      </div>
      <div className="nav-section-label">Workspace</div>
      <nav className="nav">
        {activeJobId && (
          <button
            className={`nav-item ${page === 'processing' ? 'active' : ''}`}
            onClick={() => navigate('processing', { jobId: activeJobId })}
          >
            <Ic.upload className="nav-icon" />
            <span>Processing</span>
            <span className="pulse-dot" style={{ marginLeft: 'auto', width: 6, height: 6 }}></span>
          </button>
        )}
        {top.map(it => {
          const I = it.icon;
          return (
            <button key={it.id} className={`nav-item ${isActive(it.id) ? 'active' : ''}`} onClick={() => navigate(it.id)}>
              <I className="nav-icon" />
              <span>{it.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="nav-section-label">Account</div>
      <nav className="nav">
        {bot.map(it => {
          const I = it.icon;
          return (
            <button key={it.id} className={`nav-item ${page === it.id || (it.id === 'profile' && page === 'observability') ? 'active' : ''}`} onClick={() => navigate(it.id)}>
              <I className="nav-icon" />
              <span>{it.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <div className="user-chip" onClick={() => navigate('profile')}>
          <div className="avatar">{initials}</div>
          <div className="user-chip-info">
            <div className="name">{user?.name || 'Xtract User'}</div>
            <div className="email">{user?.email || ''}</div>
          </div>
        </div>
        <button
          className="btn btn-ghost btn-sm"
          style={{ width: '100%', justifyContent: 'center', marginTop: 6, color: 'var(--text-3)' }}
          onClick={logout}
        >
          Sign out
        </button>
      </div>
    </aside>
  );
}
