export const API_BASE = 'https://xtract-nftf.onrender.com';

const TOKEN_KEY = 'xtract_token';

export function fmt(n) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
