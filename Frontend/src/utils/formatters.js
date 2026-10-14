export const API_BASE = import.meta.env.VITE_API_URL || 'https://xtract-nftf.onrender.com';

const TOKEN_KEY = 'xtract_token';

export function fmt(n) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// USD cost (LLM spend) — Indian digit grouping, more decimals for sub-cent values
export function fmtUsd(n) {
  if (n === null || n === undefined) return '—';
  const v = Number(n);
  const digits = v !== 0 && Math.abs(v) < 1 ? 4 : 2;
  return '$' + v.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// Compact counts for tokens: 1.2K, 3.4M
export function fmtCompact(n) {
  if (n === null || n === undefined) return '—';
  const v = Number(n);
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + 'M';
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(v >= 1e4 ? 0 : 1) + 'K';
  return String(Math.round(v));
}

export function fmtMs(ms) {
  if (ms === null || ms === undefined) return '—';
  return ms >= 1000 ? (ms / 1000).toFixed(ms >= 10000 ? 0 : 1) + 's' : Math.round(ms) + 'ms';
}

export function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch {
    return iso;
  }
}

// Authenticated fetch — auto-injects Bearer token and dispatches 'xtract:unauthorized' on 401
export function apiFetch(path, options = {}) {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = { ...(options.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  return fetch(`${API_BASE}${path}`, { ...options, headers }).then(res => {
    if (res.status === 401) window.dispatchEvent(new Event('xtract:unauthorized'));
    return res;
  });
}

// Fetch an Excel file as a blob and trigger browser download
export async function downloadExcel(jobId) {
  const res = await apiFetch(`/download/${jobId}`);
  if (!res.ok) throw new Error('Download failed');
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'Bill_Extracted.xlsx';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
