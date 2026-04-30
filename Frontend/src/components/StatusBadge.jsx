export default function StatusBadge({ status }) {
  const map = {
    done: 'completed', completed: 'completed', VERIFIED: 'completed', verified: 'completed',
    processing: 'processing', queued: 'queued',
    failed: 'failed', error: 'failed',
    NEEDS_REVIEW: 'flagged', flagged: 'flagged', FLAGGED: 'flagged',
    DUPLICATE: 'flagged',
  };
  const norm = map[status] || 'queued';
  const clsMap = { completed: 'badge-green', processing: 'badge-amber', queued: 'badge-gray', failed: 'badge-red', flagged: 'badge-amber' };
  const lblMap = { completed: 'Completed', processing: 'Processing', queued: 'Queued', failed: 'Failed', flagged: 'Flagged' };
  return (
    <span className={`badge ${clsMap[norm]}`}>
      {norm === 'processing' && <span className="spinner" style={{ width: 9, height: 9, borderWidth: 1.5 }}></span>}
      {norm !== 'processing' && <span className="badge-dot-mini" style={{ background: 'currentColor', opacity: 0.7 }}></span>}
      {lblMap[norm]}
    </span>
  );
}
