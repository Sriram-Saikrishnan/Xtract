export default function ConfBadge({ value }) {
  const pct = Math.round((value || 0) * 100);
  const cls = pct >= 95 ? 'badge-green' : pct >= 80 ? 'badge-amber' : 'badge-red';
  return <span className={`badge ${cls} text-mono`}>{pct}%</span>;
}
