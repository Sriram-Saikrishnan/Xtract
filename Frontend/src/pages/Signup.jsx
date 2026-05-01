import { useState } from 'react';
import { useAuth } from '../context/AuthContext';

export default function Signup({ onSwitch, toast }) {
  const { signup } = useAuth();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (password !== confirm) { setError('Passwords do not match'); return; }
    if (password.length < 8) { setError('Password must be at least 8 characters'); return; }
    setLoading(true);
    try {
      await signup(email, password, name);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="brand-dot"></span>
          <span className="brand-name">Xtract</span>
        </div>
        <h1 className="auth-title">Create account</h1>
        <p className="auth-sub">Start extracting data from your invoices.</p>
        <form onSubmit={handleSubmit} className="auth-form">
          {error && <div className="auth-error">{error}</div>}
          <div className="form-row">
            <label>Full name</label>
            <input
              className="input"
              type="text"
              required
              autoFocus
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Your name"
            />
          </div>
          <div className="form-row">
            <label>Email</label>
            <input
              className="input"
              type="email"
              required
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>
          <div className="form-row">
            <label>Password</label>
            <input
              className="input"
              type="password"
              required
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="Min. 8 characters"
            />
          </div>
          <div className="form-row">
            <label>Confirm password</label>
            <input
              className="input"
              type="password"
              required
              value={confirm}
              onChange={e => setConfirm(e.target.value)}
              placeholder="Repeat password"
            />
          </div>
          <button
            className="btn btn-primary"
            type="submit"
            disabled={loading}
            style={{ width: '100%', justifyContent: 'center', marginTop: 4 }}
          >
            {loading
              ? <span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span>
              : 'Create account'}
          </button>
        </form>
        <p className="auth-switch">
          Already have an account?{' '}
          <button className="link-btn" onClick={onSwitch}>Sign in</button>
        </p>
      </div>
    </div>
  );
}
