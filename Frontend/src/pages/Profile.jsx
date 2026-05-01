import { useState } from 'react';
import { Ic } from '../components/icons';
import { useAuth } from '../context/AuthContext';
import { apiFetch } from '../utils/formatters';

export default function Profile({ navigate, toast }) {
  const { user, logout, updateUser } = useAuth();
  const [name, setName] = useState(user?.name || '');
  const [saving, setSaving] = useState(false);
  const [prefs, setPrefs] = useState({ threshold: 85, emailComplete: true, emailFail: true, weeklyDigest: false });

  const initials = user?.name
    ? user.name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2)
    : (user?.email?.[0] || 'U').toUpperCase();

  const saveProfile = async () => {
    setSaving(true);
    try {
      const res = await apiFetch('/auth/profile', {
        method: 'PATCH',
        body: JSON.stringify({ name }),
      });
      if (!res.ok) throw new Error('Save failed');
      const updated = await res.json();
      updateUser(updated);
      toast('Profile saved');
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page profile-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Profile &amp; Account</h1>
          <p className="page-sub">Manage your personal info and preferences.</p>
        </div>
      </div>

      <div className="card profile-hero mb-3">
        <div className="avatar-lg">{initials}</div>
        <div className="profile-hero-info">
          <h2>{user?.name || user?.email || 'Xtract User'}</h2>
          <div className="meta">{user?.email}</div>
        </div>
      </div>

      <div className="card section mb-3">
        <h3>Personal info</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div className="form-row">
            <label>Full name</label>
            <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="Your name" />
          </div>
          <div className="form-row">
            <label>Email</label>
            <input className="input" value={user?.email || ''} disabled style={{ opacity: 0.6 }} />
          </div>
        </div>
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn btn-ghost" onClick={() => setName(user?.name || '')}>Cancel</button>
          <button className="btn btn-primary" disabled={saving} onClick={saveProfile}>
            {saving ? 'Saving…' : 'Save'}
          </button>
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
            <div style={{ fontWeight: 600, fontSize: 13.5 }}>Sign out</div>
            <div className="muted" style={{ fontSize: 12.5 }}>You will need to sign in again to access Xtract.</div>
          </div>
          <button className="btn btn-danger-outline btn-sm" onClick={logout}>Sign out</button>
        </div>
      </div>
    </div>
  );
}
