import { Ic } from './icons';

export default function Sidebar({ page, navigate }) {
  const top = [
    { id: 'dashboard', label: 'Dashboard', icon: Ic.dashboard },
    { id: 'extractions', label: 'Extractions', icon: Ic.extract },
    { id: 'upload', label: 'Upload', icon: Ic.upload },
  ];
  const bot = [
    { id: 'profile', label: 'Profile', icon: Ic.user },
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-dot"></span>
        <span className="brand-name">Xtract</span>
      </div>
      <div className="nav-section-label">Workspace</div>
      <nav className="nav">
        {top.map(it => {
          const I = it.icon;
          return (
            <button key={it.id} className={`nav-item ${page === it.id ? 'active' : ''}`} onClick={() => navigate(it.id)}>
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
            <button key={it.id} className={`nav-item ${page === it.id ? 'active' : ''}`} onClick={() => navigate(it.id)}>
              <I className="nav-icon" />
              <span>{it.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <div className="user-chip" onClick={() => navigate('profile')}>
          <div className="avatar">U</div>
          <div className="user-chip-info">
            <div className="name">Xtract User</div>
            <div className="email">admin@xtract.app</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
