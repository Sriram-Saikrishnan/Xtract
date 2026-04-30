import { Ic } from './icons';

export default function Topbar() {
  return (
    <div className="topbar">
      <div className="search">
        <Ic.search style={{ width: 16, height: 16, color: 'var(--text-3)' }} />
        <input placeholder="Search files, vendors, batches…" />
        <kbd>⌘K</kbd>
      </div>
      <div className="topbar-spacer" />
      <button className="icon-btn"><Ic.bell style={{ width: 18, height: 18 }} /></button>
      <div style={{ width: 0.5, height: 22, background: 'var(--border)', margin: '0 4px' }} />
      <div className="avatar" style={{ width: 32, height: 32, fontSize: 13 }}>U</div>
    </div>
  );
}
