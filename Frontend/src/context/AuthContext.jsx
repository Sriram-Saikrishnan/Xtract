import { createContext, useContext, useState, useEffect } from 'react';
import { API_BASE } from '../utils/formatters';

const AuthContext = createContext(null);

const TOKEN_KEY = 'xtract_token';
const USER_KEY = 'xtract_user';

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
  });

  // Auto-logout when apiFetch receives a 401 (expired/invalid token)
  useEffect(() => {
    const handler = () => _clear();
    window.addEventListener('xtract:unauthorized', handler);
    return () => window.removeEventListener('xtract:unauthorized', handler);
  }, []);

  function _store(tok, usr) {
    localStorage.setItem(TOKEN_KEY, tok);
    localStorage.setItem(USER_KEY, JSON.stringify(usr));
    setToken(tok);
    setUser(usr);
  }

  function _clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  }

  async function signup(email, password, name) {
    const res = await fetch(`${API_BASE}/auth/signup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, name }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Signup failed');
    _store(data.access_token, data.user);
    return data.user;
  }

  async function login(email, password) {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Login failed');
    _store(data.access_token, data.user);
    return data.user;
  }

  function logout() { _clear(); }

  function updateUser(userData) {
    localStorage.setItem(USER_KEY, JSON.stringify(userData));
    setUser(userData);
  }

  return (
    <AuthContext.Provider value={{ user, token, signup, login, logout, updateUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
