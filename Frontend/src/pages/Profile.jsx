import { useState } from 'react';
import { Ic } from '../components/icons';

export default function Profile({ navigate, toast }) {
  const [prefs, setPrefs] = useState({ threshold: 85, emailComplete: true, emailFail: true, weeklyDigest: false });

  return (
    <div className="page profile-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Profile &amp; Account</h1>
          <p className="page-sub">Manage your personal info and preferences.</p>
        </div>
      </div>

      <div className="card profile-hero mb-3">
        <div className="avatar-lg">U</div>
        <div className="profile-hero-info">
          <h2>Xtract User</h2>
          <div className="meta">admin@xtract.app · <span className="badge badge-green" style={{ verticalAlign: 'middle' }}>Admin</span></div>
        </div>
        <button className="btn btn-secondary"><Ic.edit style={{ width: 14, height: 14 }} />Edit profile</button>
      </div>

      <div className="card section mb-3">
        <h3>Personal info</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div className="form-row"><label>Full name</label><input className="input" defaultValue="Xtract User" /></div>
          <div className="form-row"><label>Email</label><input className="input" defaultValue="admin@xtract.app" /></div>
          <div className="form-row"><label>Company</label><input className="input" defaultValue="" placeholder="Your company" /></div>
          <div className="form-row"><label>Role</label><input className="input" defaultValue="Admin" /></div>
        </div>
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn btn-ghost">Cancel</button>
          <button className="btn btn-primary" onClick={() => toast('Saved')}>Save</button>
        </div>
      </div>

      <div className="card section mb-3">
        <h3>Extraction preferences</h3>
        <div className="form-row">
          <label>Confidence threshold ({prefs.threshold}%)</label>
          <div className="slider-wrap">
            <input type="range" min="50" max="100" value={prefs.threshold} className="slider"
              onChange={e => setPrefs({ ...prefs, threshold: +e.target.value })} />
            <span className="slider-val">{prefs.threshold}%</span>
          </div>
          <div style={{ color: 'var(--text-3)', fontSize: 11.5 }}>Files below this confidence are flagged for review.</div>
        </div>
      </div>

      <div className="card section mb-3">
        <h3>Notifications</h3>
        <div className="toggle-row">
          <div><div className="lbl">Email on batch complete</div></div>
          <div className={`toggle ${prefs.emailComplete ? 'on' : ''}`} onClick={() => setPrefs({ ...prefs, emailComplete: !prefs.emailComplete })}></div>
        </div>
        <div className="toggle-row">
          <div><div className="lbl">Email on extraction failure</div></div>
          <div className={`toggle ${prefs.emailFail ? 'on' : ''}`} onClick={() => setPrefs({ ...prefs, emailFail: !prefs.emailFail })}></div>
        </div>
        <div className="toggle-row">
          <div><div className="lbl">Weekly digest</div></div>
          <div className={`toggle ${prefs.weeklyDigest ? 'on' : ''}`} onClick={() => setPrefs({ ...prefs, weeklyDigest: !prefs.weeklyDigest })}></div>
        </div>
      </div>

      <div className="card section" style={{ borderColor: '#E9C5BD' }}>
        <h3 style={{ color: 'var(--red)' }}>Danger zone</h3>
        <div className="row between">
          <div>
            <div style={{ fontWeight: 600, fontSize: 13.5 }}>Delete all data</div>
            <div className="muted" style={{ fontSize: 12.5 }}>Permanently removes all jobs and extracted data.</div>
          </div>
          <button className="btn btn-danger-outline btn-sm"><Ic.trash style={{ width: 13, height: 13 }} />Delete all</button>
        </div>
      </div>
    </div>
  );
}
