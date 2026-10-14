import { Ic } from './icons';

const PAGE_SIZES = [10, 20, 50, 100];

function pageList(current, count) {
  if (count <= 7) return Array.from({ length: count }, (_, i) => i);
  const pages = new Set([0, count - 1, current - 1, current, current + 1]);
  const sorted = [...pages].filter(p => p >= 0 && p < count).sort((a, b) => a - b);
  const out = [];
  sorted.forEach((p, i) => {
    if (i > 0 && p - sorted[i - 1] > 1) out.push('…' + p);
    out.push(p);
  });
  return out;
}

/** Zero-based page pagination footer for tables. */
export default function Pagination({ page, pageSize, total, onPageChange, onPageSizeChange, loading }) {
  const count = Math.max(Math.ceil(total / pageSize), 1);
  const from = total === 0 ? 0 : page * pageSize + 1;
  const to = Math.min((page + 1) * pageSize, total);
  return (
    <div className="pager">
      <div className="pager-info">
        {from.toLocaleString('en-IN')}–{to.toLocaleString('en-IN')} of {total.toLocaleString('en-IN')}
        {onPageSizeChange && (
          <select className="pager-size" value={pageSize} onChange={e => onPageSizeChange(+e.target.value)} aria-label="Rows per page">
            {PAGE_SIZES.map(s => <option key={s} value={s}>{s} / page</option>)}
          </select>
        )}
      </div>
      <div className="pager-btns">
        <button className="pager-btn" disabled={page === 0 || loading} onClick={() => onPageChange(page - 1)} aria-label="Previous page">
          <Ic.chevronR style={{ width: 13, height: 13, transform: 'rotate(180deg)' }} />
        </button>
        {pageList(page, count).map(p => typeof p === 'string'
          ? <span key={p} className="pager-gap">…</span>
          : <button key={p} className={`pager-btn ${p === page ? 'active' : ''}`} disabled={loading} onClick={() => onPageChange(p)}>{p + 1}</button>
        )}
        <button className="pager-btn" disabled={page >= count - 1 || loading} onClick={() => onPageChange(page + 1)} aria-label="Next page">
          <Ic.chevronR style={{ width: 13, height: 13 }} />
        </button>
      </div>
    </div>
  );
}
