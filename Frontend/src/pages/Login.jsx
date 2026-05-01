import { useState } from 'react';
import { useAuth } from '../context/AuthContext';

export default function Login({ onSwitch, toast }) {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(email, password);
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
        <h1 className="auth-title">Sign in</h1>
        <p className="auth-sub">Extract structured data from your invoices.</p>
        <form onSubmit={handleSubmit} className="auth-form">
          {error && <div className="auth-error">{error}</div>}
          <div className="form-row">
            <label>Email</label>
            <input
              className="input"
              type="email"
              required
              autoFocus
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
              placeholder="••••••••"
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
              : 'Sign in'}
          </button>
        </form>
        <p className="auth-switch">
          Don't have an account?{' '}
          <button className="link-btn" onClick={onSwitch}>Sign up</button>
        </p>
      </div>
    </div>
  );
}
