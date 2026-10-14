import { useEffect, useState } from 'react';
import { Ic } from '../components/icons';
import StatusBadge from '../components/StatusBadge';
import Pagination from '../components/Pagination';
import { BarChart, LineChartMulti, HBarList, Legend, SERIES } from '../components/ObsCharts';
import { apiFetch, fmtDate, fmtUsd, fmtCompact, fmtMs } from '../utils/formatters';

const RANGES = [7, 30, 90];
const TOKEN_SERIES = [
  { key: 'input_tokens', label: 'Input', color: SERIES[0] },
  { key: 'output_tokens', label: 'Output', color: SERIES[1] },
  { key: 'thinking_tokens', label: 'Thinking', color: SERIES[2] },
];
const LATENCY_SERIES = [
  { key: 'p50_latency_ms', label: 'p50', color: SERIES[0] },
  { key: 'p95_latency_ms', label: 'p95', color: SERIES[1] },
];
const CALL_SERIES = [
  { key: 'ok', label: 'Success', color: 'var(--text-3)' },
  { key: 'errors', label: 'Errors', color: 'var(--red)' },
];
const OP_LABELS = { extract: 'Extraction', generate_fields: 'Field generation', test: 'Blueprint test' };
const ERR_LABELS = {
  rate_limit: 'Rate limit (429)', unavailable: 'Unavailable (503)', timeout: 'Timeout',
  model_not_found: 'Model not found', other: 'Other',
};

const dayLabel = (d, full) => {
  const dt = new Date(d.date + 'T00:00:00');
  return full ? fmtDate(dt.toISOString()) : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
};

// Backend timestamps are naive UTC.
const fmtDateTime = iso => !iso ? '—' : new Date(iso + (iso.endsWith('Z') ? '' : 'Z'))
  .toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });

const fetchJson = (path, what) => apiFetch(path).then(r => {
  if (!r.ok) throw new Error(`Failed to load ${what}`);
  return r.json();
});

// Thinking setting as sent to Gemini; label comes from the backend (model-aware).
const ThinkingBadge = ({ budget, label }) => {
  if (budget === null || budget === undefined) return <span className="muted" style={{ fontSize: 12 }}>{label || 'N/A'}</span>;
  return <span className={`badge badge-thinking ${budget === -1 ? 'badge-blue' : 'badge-gray'}`}>{label}</span>;
};

const Spinner = () => (
  <div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading…</div>
);

/** Paged list fetcher: resets to page 0 whenever `deps` change. */
function usePaged(buildPath, what, toast, deps, initialSize = 20) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(initialSize);
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);

  useEffect(() => { setPage(0); }, deps); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchJson(buildPath(pageSize, page * pageSize), what)
      .then(d => { if (!cancelled) setData(d); })
      .catch(e => toast('Error: ' + e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [page, pageSize, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    data, loading,
    pager: {
      page, pageSize, total: data.total, loading,
      onPageChange: setPage,
      onPageSizeChange: size => { setPageSize(size); setPage(0); },
    },
  };
}

// ── Jobs comparison table ─────────────────────────────────────────────────

const JOB_COLS = [
  { key: 'created_at', label: 'Project / job', sort: 'created_at' },
  { key: 'status', label: 'Status' },
  { key: 'thinking', label: 'Thinking' },
  { key: 'input', label: 'Input', num: true },
  { key: 'calls', label: 'Calls', num: true, sort: 'calls' },
  { key: 'in', label: 'In', num: true },
  { key: 'out', label: 'Out', num: true },
  { key: 'think', label: 'Think', num: true },
  { key: 'tokens', label: 'Total', num: true, sort: 'tokens' },
  { key: 'latency', label: 'Latency avg / p95', num: true, sort: 'latency' },
  { key: 'duration', label: 'Duration', num: true },
  { key: 'cost', label: 'Cost', num: true, sort: 'cost' },
  { key: 'cpp', label: 'Cost / page', num: true },
];

function JobsTable({ days, reloadKey, onOpen, toast }) {
  const [sort, setSort] = useState({ key: 'created_at', order: 'desc' });
  const { data, loading, pager } = usePaged(
    (limit, offset) => `/observability/jobs?days=${days}&sort=${sort.key}&order=${sort.order}&limit=${limit}&offset=${offset}`,
    'jobs', toast, [days, reloadKey, sort.key, sort.order],
  );
  const maxCost = Math.max(...data.items.map(j => j.cost_usd), 0) || 1;

  const toggleSort = key => setSort(s => ({ key, order: s.key === key && s.order === 'desc' ? 'asc' : 'desc' }));

  return (
    <div className="card mb-3">
      <div className="card-head">
        <div>
          <h3 className="card-title">Extraction jobs</h3>
          <div className="card-sub">AI usage per job — click a row to see every call. Sort by a column to compare.</div>
        </div>
      </div>
      {loading && data.items.length === 0 ? <Spinner /> : data.items.length === 0 ? (
        <div className="empty"><div style={{ fontSize: 12.5 }}>No extraction jobs with AI usage in the last {days} days.</div></div>
      ) : (
        <div style={{ overflowX: 'auto', opacity: loading ? 0.6 : 1 }}>
          <table className="tbl obs-tbl">
            <thead>
              <tr>
                {JOB_COLS.map(c => (
                  <th key={c.key} style={{ textAlign: c.num ? 'right' : 'left' }}
                    className={c.sort ? 'sortable' : ''} onClick={c.sort ? () => toggleSort(c.sort) : undefined}
                    aria-sort={sort.key === c.sort ? (sort.order === 'asc' ? 'ascending' : 'descending') : undefined}>
                    {c.label}
                    {c.sort && sort.key === c.sort && <span className="sort-ind">{sort.order === 'asc' ? '▲' : '▼'}</span>}
                  </th>
                ))}
                <th style={{ width: 28 }}></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map(j => (
                <tr key={j.job_id} onClick={() => onOpen(j.job_id)}>
                  <td style={{ maxWidth: 220 }}>
                    <div className="ellipsis" style={{ fontWeight: 600, fontSize: 13 }}>{j.project_name || <span className="muted">No project</span>}</div>
                    <div className="muted ellipsis" style={{ fontSize: 11.5 }}>
                      {j.blueprint_name ? `${j.blueprint_name} · ` : ''}{fmtDateTime(j.created_at)}
                    </div>
                  </td>
                  <td><StatusBadge status={j.status} /></td>
                  <td><ThinkingBadge budget={j.thinking_budget} label={j.thinking_label} /></td>
                  <td className="num" title={`${j.total_files} files → ${j.total_pages} pages (${j.completed_pages} done, ${j.failed_pages} failed)`}>
                    {j.total_files} <span className="muted">/ {j.total_pages}p</span>
                  </td>
                  <td className="num">
                    {j.calls}
                    {(j.errors > 0 || j.retries > 0) && (
                      <div style={{ fontSize: 11, color: j.errors ? 'var(--red)' : 'var(--text-3)' }}>
                        {j.errors > 0 && `${j.errors} err`}{j.errors > 0 && j.retries > 0 && ' · '}{j.retries > 0 && `${j.retries} retry`}
                      </div>
                    )}
                  </td>
                  <td className="num">{fmtCompact(j.input_tokens)}</td>
                  <td className="num">{fmtCompact(j.output_tokens)}</td>
                  <td className="num">{fmtCompact(j.thinking_tokens)}</td>
                  <td className="num" style={{ fontWeight: 700 }}>{fmtCompact(j.total_tokens)}</td>
                  <td className="num">{fmtMs(j.avg_latency_ms)} <span className="muted">/ {fmtMs(j.p95_latency_ms)}</span></td>
                  <td className="num">{j.duration_ms != null ? fmtMs(j.duration_ms) : '—'}</td>
                  <td className="num">
                    <div style={{ fontWeight: 700 }}>{fmtUsd(j.cost_usd)}</div>
                    <div className="obs-inline-track"><div style={{ width: `${(j.cost_usd / maxCost) * 100}%` }} /></div>
                  </td>
                  <td className="num">{j.cost_per_page_usd != null ? fmtUsd(j.cost_per_page_usd) : '—'}</td>
                  <td><Ic.chevronR style={{ width: 14, height: 14, color: 'var(--text-3)' }} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.total > 0 && <Pagination {...pager} />}
    </div>
  );
}

// ── Individual calls table ────────────────────────────────────────────────

function CallsTable({ jobId, reloadKey, toast, title, sub }) {
  const { data, loading, pager } = usePaged(
    (limit, offset) => `/observability/calls?limit=${limit}&offset=${offset}${jobId ? `&job_id=${jobId}` : ''}`,
    'calls', toast, [jobId, reloadKey], 25,
  );

  return (
    <div className="card mb-3">
      <div className="card-head">
        <div>
          <h3 className="card-title">{title}</h3>
          {sub && <div className="card-sub">{sub}</div>}
        </div>
      </div>
      {loading && data.items.length === 0 ? <Spinner /> : data.items.length === 0 ? (
        <div className="empty"><div style={{ fontSize: 12.5 }}>No AI calls recorded.</div></div>
      ) : (
        <div style={{ overflowX: 'auto', opacity: loading ? 0.6 : 1 }}>
          <table className="tbl obs-tbl">
            <thead>
              <tr>
                <th>Time</th><th>Operation</th><th>{jobId ? 'Page' : 'Project / page'}</th><th>Model</th><th>Mode</th><th>Thinking</th>
                <th style={{ textAlign: 'right' }}>In</th><th style={{ textAlign: 'right' }}>Out</th>
                <th style={{ textAlign: 'right' }}>Think</th><th style={{ textAlign: 'right' }}>Cached</th>
                <th style={{ textAlign: 'right' }}>Total</th><th style={{ textAlign: 'right' }}>Latency</th>
                <th style={{ textAlign: 'right' }}>Cost</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map(c => (
                <tr key={c.id} style={{ cursor: 'default' }}>
                  <td className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{fmtDateTime(c.created_at)}</td>
                  <td style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>
                    {OP_LABELS[c.operation] || c.operation}
                    {c.attempt > 1 && <span className="badge badge-gray" style={{ marginLeft: 6 }}>try {c.attempt}</span>}
                  </td>
                  <td style={{ fontSize: 12.5, maxWidth: 240 }}>
                    {!jobId && <div className="ellipsis" style={{ fontWeight: 600 }}>{c.project_name || <span className="muted">—</span>}</div>}
                    <div className={`ellipsis ${jobId ? '' : 'muted'}`} style={{ fontSize: jobId ? 12.5 : 11.5 }} title={c.page_label || ''}>{c.page_label || '—'}</div>
                  </td>
                  <td className="text-mono muted" style={{ fontSize: 11.5 }}>{c.model}</td>
                  <td className="muted" style={{ fontSize: 11.5 }}>{c.input_mode || '—'}</td>
                  <td><ThinkingBadge budget={c.thinking_budget} label={c.thinking_label} /></td>
                  <td className="num">{fmtCompact(c.input_tokens)}</td>
                  <td className="num">{fmtCompact(c.output_tokens)}</td>
                  <td className="num">{fmtCompact(c.thinking_tokens)}</td>
                  <td className="num">{fmtCompact(c.cached_tokens)}</td>
                  <td className="num" style={{ fontWeight: 700 }}>{fmtCompact(c.total_tokens)}</td>
                  <td className="num">{fmtMs(c.latency_ms)}</td>
                  <td className="num">{fmtUsd(c.cost_usd)}</td>
                  <td>
                    {c.status === 'success'
                      ? <span className="badge badge-green">Success</span>
                      : <span className="badge badge-red" title={c.error_message || ''}>{ERR_LABELS[c.error_type] || 'Error'}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.total > 0 && <Pagination {...pager} />}
    </div>
  );
}

// ── Job drill-down ────────────────────────────────────────────────────────

function JobDetail({ jobId, reloadKey, onBack, navigate, toast }) {
  const [job, setJob] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchJson(`/observability/jobs/${jobId}`, 'job usage')
      .then(d => { if (!cancelled) setJob(d); })
      .catch(e => toast('Error: ' + e.message));
    return () => { cancelled = true; };
  }, [jobId, reloadKey]);

  return (
    <>
      <div className="breadcrumb">
        <a onClick={() => navigate('profile')}>Profile</a>
        <span className="sep">/</span>
        <a onClick={onBack}>AI Observability</a>
        <span className="sep">/</span>
        <span className="current">{job?.project_name || 'Job'}</span>
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">{job?.project_name || 'Job usage'}</h1>
          <p className="page-sub">
            {job ? <>{job.blueprint_name ? `${job.blueprint_name} · ` : ''}{fmtDateTime(job.created_at)} · <span className="text-mono">{job.model}</span></> : 'Loading…'}
          </p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn btn-secondary btn-sm" onClick={() => navigate('results', { jobId })}>View results</button>
          <button className="btn btn-ghost btn-sm" onClick={onBack}>← Back</button>
        </div>
      </div>

      {!job ? <Spinner /> : (
        <div className="stat-grid obs-stat-grid-6">
          <div className="card stat">
            <div className="stat-label">Status</div>
            <div style={{ marginTop: 4 }}><StatusBadge status={job.status} /></div>
            <div className="stat-foot">
              <span className="muted" style={{ fontSize: 11.5 }}>Duration {job.duration_ms != null ? fmtMs(job.duration_ms) : '—'}</span>
              <span className="row" style={{ gap: 6, fontSize: 11.5 }}>
                <span className="muted">Thinking</span><ThinkingBadge budget={job.thinking_budget} label={job.thinking_label} />
              </span>
            </div>
          </div>
          <div className="card stat">
            <div className="stat-label">Input</div>
            <div className="stat-value">{job.total_files}<span className="obs-unit"> files</span></div>
            <div className="stat-foot">
              <span className="muted" style={{ fontSize: 11.5 }}>{job.total_pages} pages · {job.completed_pages} done · {job.failed_pages} failed</span>
            </div>
          </div>
          <div className="card stat">
            <div className="stat-label">Cost</div>
            <div className="stat-value">{fmtUsd(job.cost_usd)}</div>
            <div className="stat-foot"><span className="trend trend-flat">{job.cost_per_page_usd != null ? fmtUsd(job.cost_per_page_usd) : '—'} / page</span></div>
          </div>
          <div className="card stat">
            <div className="stat-label">Tokens</div>
            <div className="stat-value">{fmtCompact(job.total_tokens)}</div>
            <div className="stat-foot">
              <span className="muted text-mono" style={{ fontSize: 11 }}>
                in {fmtCompact(job.input_tokens)} · out {fmtCompact(job.output_tokens)} · think {fmtCompact(job.thinking_tokens)}
              </span>
            </div>
          </div>
          <div className="card stat">
            <div className="stat-label">Calls</div>
            <div className="stat-value">{job.calls}</div>
            <div className="stat-foot">
              <span className={`trend ${job.errors ? 'trend-down' : 'trend-up'}`}>
                {job.errors ? <Ic.warn style={{ width: 11, height: 11 }} /> : <Ic.check style={{ width: 11, height: 11 }} />}
                {job.errors} errors
              </span>
              <span className="muted" style={{ fontSize: 11.5 }}>{job.retries} retries</span>
            </div>
          </div>
          <div className="card stat">
            <div className="stat-label">Latency (avg)</div>
            <div className="stat-value">{fmtMs(job.avg_latency_ms)}</div>
            <div className="stat-foot">
              <span className="muted" style={{ fontSize: 11.5 }}>p50 {fmtMs(job.p50_latency_ms)} · p95 {fmtMs(job.p95_latency_ms)} · max {fmtMs(job.max_latency_ms)}</span>
            </div>
          </div>
        </div>
      )}

      <CallsTable jobId={jobId} reloadKey={reloadKey} toast={toast} title="AI calls in this job"
        sub="Every Gemini call, retries included" />
    </>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────

export default function Observability({ navigate, toast }) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [jobId, setJobId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchJson(`/observability/overview?days=${days}`, 'usage')
      .then(d => { if (!cancelled) setData(d); })
      .catch(e => toast('Error: ' + e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [days, reloadKey]);

  const openJob = id => { setJobId(id); window.scrollTo?.(0, 0); };

  if (jobId) {
    return (
      <div className="page">
        <JobDetail jobId={jobId} reloadKey={reloadKey} onBack={() => setJobId(null)} navigate={navigate} toast={toast} />
      </div>
    );
  }

  const t = data?.totals;
  const daily = (data?.daily || []).map(d => ({ ...d, ok: d.calls - d.errors }));
  const hasData = t && t.calls > 0;

  return (
    <div className="page">
      <div className="breadcrumb">
        <a onClick={() => navigate('profile')}>Profile</a>
        <span className="sep">/</span>
        <span className="current">AI Observability</span>
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">AI Observability</h1>
          <p className="page-sub">
            Gemini usage, tokens, latency and estimated cost{data ? <> · current model <span className="text-mono">{data.current_model}</span></> : null}
          </p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <div className="seg" role="group" aria-label="Date range">
            {RANGES.map(r => (
              <button key={r} className={`seg-btn ${days === r ? 'active' : ''}`} onClick={() => setDays(r)}>{r}d</button>
            ))}
          </div>
          <button className="btn btn-secondary btn-sm" onClick={() => setReloadKey(k => k + 1)} title="Refresh">
            <Ic.refresh style={{ width: 14, height: 14 }} />
          </button>
        </div>
      </div>

      {loading && !data ? <Spinner /> : !hasData ? (
        <div className="card mb-3">
          <div className="empty">
            <div className="empty-icon"><Ic.trendUp style={{ width: 22, height: 22 }} /></div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>No AI calls in the last {days} days</div>
            <div style={{ fontSize: 12.5 }}>Usage is recorded for every extraction, field generation and blueprint test.</div>
          </div>
        </div>
      ) : (
        <div style={{ opacity: loading ? 0.6 : 1, transition: 'opacity .15s' }}>
          <div className="stat-grid">
            <div className="card stat">
              <div className="stat-label">Estimated spend</div>
              <div className="stat-value">{fmtUsd(t.cost_usd)}</div>
              <div className="stat-foot">
                <span className="trend trend-flat">{fmtUsd(t.cost_per_page_usd)} / page</span>
                <span className="muted" style={{ fontSize: 11.5 }}>{t.pages_extracted.toLocaleString('en-IN')} pages</span>
              </div>
            </div>
            <div className="card stat">
              <div className="stat-label">Total tokens</div>
              <div className="stat-value">{fmtCompact(t.total_tokens)}</div>
              <div className="stat-foot">
                <span className="muted text-mono" style={{ fontSize: 11.5 }}>
                  in {fmtCompact(t.input_tokens)} · out {fmtCompact(t.output_tokens)} · think {fmtCompact(t.thinking_tokens)}
                </span>
              </div>
            </div>
            <div className="card stat">
              <div className="stat-label">AI calls</div>
              <div className="stat-value">{t.calls.toLocaleString('en-IN')}</div>
              <div className="stat-foot">
                <span className={`trend ${t.errors ? 'trend-down' : 'trend-up'}`}>
                  {t.errors ? <Ic.warn style={{ width: 11, height: 11 }} /> : <Ic.check style={{ width: 11, height: 11 }} />}
                  {(t.error_rate * 100).toFixed(1)}% errors
                </span>
                <span className="muted" style={{ fontSize: 11.5 }}>{t.errors} failed</span>
              </div>
            </div>
            <div className="card stat">
              <div className="stat-label">Latency (p50)</div>
              <div className="stat-value">{fmtMs(t.p50_latency_ms)}</div>
              <div className="stat-foot">
                <span className="trend trend-flat">p95 {fmtMs(t.p95_latency_ms)}</span>
                <span className="muted" style={{ fontSize: 11.5 }}>avg {fmtMs(t.avg_latency_ms)}</span>
              </div>
            </div>
          </div>

          <div className="obs-grid">
            <div className="card">
              <div className="card-head"><div><h3 className="card-title">Daily spend</h3><div className="card-sub">Estimated USD per day</div></div></div>
              <div className="card-pad">
                <BarChart data={daily} series={[{ key: 'cost_usd', label: 'Spend', color: SERIES[0] }]}
                  format={fmtUsd} xLabel={dayLabel} ariaLabel="Daily estimated spend" />
              </div>
            </div>
            <div className="card">
              <div className="card-head"><div><h3 className="card-title">Token usage</h3><div className="card-sub">Per day, by token type</div></div></div>
              <div className="card-pad">
                <Legend items={TOKEN_SERIES} />
                <BarChart data={daily} series={TOKEN_SERIES} format={fmtCompact} xLabel={dayLabel} ariaLabel="Daily token usage by type" />
              </div>
            </div>
            <div className="card">
              <div className="card-head"><div><h3 className="card-title">Latency</h3><div className="card-sub">Successful calls, p50 vs p95 per day</div></div></div>
              <div className="card-pad">
                <Legend items={LATENCY_SERIES.map(s => ({ ...s, line: true }))} />
                <LineChartMulti data={daily} series={LATENCY_SERIES} format={fmtMs} xLabel={dayLabel} ariaLabel="Daily p50 and p95 latency" />
              </div>
            </div>
            <div className="card">
              <div className="card-head"><div><h3 className="card-title">Calls</h3><div className="card-sub">Per day, success vs error (retries included)</div></div></div>
              <div className="card-pad">
                <Legend items={CALL_SERIES} />
                <BarChart data={daily} series={CALL_SERIES} format={v => fmtCompact(v)} xLabel={dayLabel} ariaLabel="Daily calls by outcome" />
              </div>
            </div>
          </div>

          <div className="obs-grid-3">
            <div className="card">
              <div className="card-head"><h3 className="card-title">Spend by operation</h3></div>
              <div className="card-pad">
                <HBarList rows={data.by_operation.map(r => ({ ...r, operation: OP_LABELS[r.operation] || r.operation }))}
                  labelKey="operation" valueKey="cost_usd" format={fmtUsd}
                  sub={r => `${r.calls.toLocaleString('en-IN')} calls · ${fmtCompact(r.total_tokens)} tokens`} />
              </div>
            </div>
            <div className="card">
              <div className="card-head"><h3 className="card-title">Spend by model</h3></div>
              <div className="card-pad">
                <HBarList rows={data.by_model} labelKey="model" valueKey="cost_usd" format={fmtUsd}
                  sub={r => `${r.calls.toLocaleString('en-IN')} calls · ${fmtCompact(r.total_tokens)} tokens`} />
              </div>
            </div>
            <div className="card">
              <div className="card-head"><h3 className="card-title">Top projects</h3></div>
              <div className="card-pad">
                <HBarList rows={data.by_project} labelKey="project" valueKey="cost_usd" format={fmtUsd}
                  sub={r => `${r.calls.toLocaleString('en-IN')} calls · ${fmtCompact(r.total_tokens)} tokens`} />
              </div>
            </div>
          </div>

          <div className="obs-grid">
            <div className="card">
              <div className="card-head"><div><h3 className="card-title">Latency distribution</h3><div className="card-sub">Successful calls per latency band</div></div></div>
              <div className="card-pad">
                <BarChart data={data.latency_histogram} series={[{ key: 'count', label: 'Calls', color: SERIES[2] }]}
                  format={v => fmtCompact(v)} ariaLabel="Latency distribution" />
              </div>
            </div>
            <div className="card">
              <div className="card-head"><div><h3 className="card-title">Errors by type</h3><div className="card-sub">{t.errors} failed calls</div></div></div>
              <div className="card-pad">
                {data.errors_by_type.length === 0 ? (
                  <div className="row" style={{ gap: 8, color: 'var(--green)', fontSize: 13 }}>
                    <Ic.check style={{ width: 14, height: 14 }} /> No errors in this range
                  </div>
                ) : (
                  <HBarList rows={data.errors_by_type.map(e => ({ ...e, error_type: ERR_LABELS[e.error_type] || e.error_type }))}
                    labelKey="error_type" valueKey="count" format={v => v.toLocaleString('en-IN')} color="var(--red)" />
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      <JobsTable days={days} reloadKey={reloadKey} onOpen={openJob} toast={toast} />
      <CallsTable reloadKey={reloadKey} toast={toast} title="All AI calls"
        sub="Every Gemini call across extractions, field generation and blueprint tests" />
    </div>
  );
}
