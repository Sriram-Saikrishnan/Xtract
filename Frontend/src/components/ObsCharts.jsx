import { useEffect, useRef, useState } from 'react';

// Categorical series colors — validated (CVD + contrast) against the light surface.
// Fixed order: slot 1 → slot 3. Never cycle; assign by entity, not rank.
export const SERIES = ['#3B6D11', '#C97A1A', '#2A6FB0'];
const GRID = 'var(--border)';
const AXIS_TEXT = 'var(--text-3)';

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth || 600);
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width || 600));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

function niceMax(v) {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / exp;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * exp;
}

// Rect with only the top corners rounded — data-end anchored to the baseline.
function topRounded(x, y, w, h, r) {
  if (h <= 0) return '';
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
}

export function Legend({ items }) {
  return (
    <div className="obs-legend">
      {items.map(it => (
        <span key={it.label} className="obs-legend-item">
          <span className="obs-legend-sw" style={{ background: it.color, borderRadius: it.line ? 2 : 3, height: it.line ? 3 : 10 }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}

function Tooltip({ tip }) {
  if (!tip) return null;
  return (
    <div className="obs-tip" style={{ left: tip.x, top: tip.y }}>
      <div className="obs-tip-title">{tip.title}</div>
      {tip.rows.map(r => (
        <div key={r.label} className="obs-tip-row">
          {r.color && <span className="obs-legend-sw" style={{ background: r.color }} />}
          <span className="obs-tip-label">{r.label}</span>
          <span className="obs-tip-val">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * Stacked (or single-series) vertical bars.
 * data: [{ label, ...values }], series: [{ key, label, color }]
 */
export function BarChart({ data, series, height = 200, format = v => v, xLabel = d => d.label, ariaLabel }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const padL = 48, padR = 8, padT = 10, padB = 24;
  const innerW = Math.max(width - padL - padR, 10), innerH = height - padT - padB;
  const totals = data.map(d => series.reduce((s, se) => s + (d[se.key] || 0), 0));
  const max = niceMax(Math.max(...totals, 0));
  const slot = innerW / Math.max(data.length, 1);
  const barW = Math.max(Math.min(slot - 2, 28), 2);
  const y = v => padT + innerH - (v / max) * innerH;
  const ticks = [0, max / 2, max];
  const labelEvery = Math.ceil(data.length / Math.max(Math.floor(innerW / 56), 1));

  return (
    <div ref={ref} className="obs-chart" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} role="img" aria-label={ariaLabel}>
        {ticks.map(t => (
          <g key={t}>
            <line x1={padL} x2={width - padR} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth="1" />
            <text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="10.5" fill={AXIS_TEXT} fontFamily="var(--mono)">{format(t)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = padL + i * slot + (slot - barW) / 2;
          let acc = 0;
          const segs = series.map(se => ({ se, v: d[se.key] || 0 })).filter(s => s.v > 0);
          return (
            <g key={i} opacity={hover === null || hover === i ? 1 : 0.55}>
              {segs.map((s, si) => {
                const y0 = y(acc), y1 = y(acc + s.v);
                acc += s.v;
                const top = si === segs.length - 1;
                // 2px surface gap between stacked segments
                const h = Math.max(y0 - y1 - (si > 0 ? 2 : 0), 1);
                return top
                  ? <path key={s.se.key} d={topRounded(x, y1, barW, h, 4)} fill={s.se.color} />
                  : <rect key={s.se.key} x={x} y={y1} width={barW} height={h} fill={s.se.color} />;
              })}
              {i % labelEvery === 0 && (
                <text x={x + barW / 2} y={height - 6} textAnchor="middle" fontSize="10.5" fill={AXIS_TEXT}>{xLabel(d)}</text>
              )}
              {/* hit target: full slot height, wider than the mark */}
              <rect x={padL + i * slot} y={padT} width={slot} height={innerH} fill="transparent"
                onMouseEnter={() => setHover(i)} />
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <Tooltip tip={{
          x: Math.min(padL + hover * slot + slot / 2 + 10, width - 170),
          y: 8,
          title: xLabel(data[hover], true),
          rows: [
            ...series.map(se => ({ label: se.label, value: format(data[hover][se.key] || 0), color: series.length > 1 ? se.color : null })),
            ...(series.length > 1 ? [{ label: 'Total', value: format(totals[hover]) }] : []),
          ],
        }} />
      )}
    </div>
  );
}

/**
 * Multi-series line chart with crosshair tooltip.
 * data: [{ label, ...values }], series: [{ key, label, color }]
 */
export function LineChartMulti({ data, series, height = 200, format = v => v, xLabel = d => d.label, ariaLabel }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const padL = 48, padR = 12, padT = 10, padB = 24;
  const innerW = Math.max(width - padL - padR, 10), innerH = height - padT - padB;
  const max = niceMax(Math.max(...data.flatMap(d => series.map(se => d[se.key] || 0)), 0));
  const x = i => padL + (data.length > 1 ? (i / (data.length - 1)) * innerW : innerW / 2);
  const y = v => padT + innerH - (v / max) * innerH;
  const ticks = [0, max / 2, max];
  const labelEvery = Math.ceil(data.length / Math.max(Math.floor(innerW / 56), 1));

  const onMove = e => {
    const rect = e.currentTarget.getBoundingClientRect();
    const rel = (e.clientX - rect.left - padL) / innerW;
    setHover(Math.max(0, Math.min(data.length - 1, Math.round(rel * (data.length - 1)))));
  };

  return (
    <div ref={ref} className="obs-chart" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} role="img" aria-label={ariaLabel} onMouseMove={onMove}>
        {ticks.map(t => (
          <g key={t}>
            <line x1={padL} x2={width - padR} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth="1" />
            <text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="10.5" fill={AXIS_TEXT} fontFamily="var(--mono)">{format(t)}</text>
          </g>
        ))}
        {data.map((d, i) => i % labelEvery === 0 && (
          <text key={i} x={x(i)} y={height - 6} textAnchor="middle" fontSize="10.5" fill={AXIS_TEXT}>{xLabel(d)}</text>
        ))}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={padT} y2={padT + innerH} stroke="var(--border-strong)" strokeWidth="1" />}
        {series.map(se => (
          <path key={se.key} fill="none" stroke={se.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
            d={'M' + data.map((d, i) => `${x(i)},${y(d[se.key] || 0)}`).join(' L')} />
        ))}
        {hover !== null && series.map(se => (
          <circle key={se.key} cx={x(hover)} cy={y(data[hover][se.key] || 0)} r="4.5" fill={se.color} stroke="var(--bg)" strokeWidth="2" />
        ))}
        <rect x={padL} y={padT} width={innerW} height={innerH} fill="transparent" />
      </svg>
      {hover !== null && (
        <Tooltip tip={{
          x: Math.min(x(hover) + 10, width - 170),
          y: 8,
          title: xLabel(data[hover], true),
          rows: series.map(se => ({ label: se.label, value: format(data[hover][se.key] || 0), color: se.color })),
        }} />
      )}
    </div>
  );
}

/** Ranked horizontal bars in plain HTML — for breakdowns by model/operation/project. */
export function HBarList({ rows, labelKey, valueKey, format = v => v, sub, color = SERIES[0] }) {
  const max = Math.max(...rows.map(r => r[valueKey] || 0), 0) || 1;
  if (!rows.length) return <div className="muted" style={{ fontSize: 12.5 }}>No data in this range.</div>;
  return (
    <div className="obs-hbars">
      {rows.map(r => (
        <div key={r[labelKey]} className="obs-hbar" title={`${r[labelKey]}: ${format(r[valueKey])}`}>
          <div className="row between mb-1">
            <span className="obs-hbar-label">{r[labelKey]}</span>
            <span className="text-mono" style={{ fontSize: 12.5, fontWeight: 700 }}>{format(r[valueKey])}</span>
          </div>
          <div className="obs-hbar-track">
            <div className="obs-hbar-fill" style={{ width: `${((r[valueKey] || 0) / max) * 100}%`, background: color }} />
          </div>
          {sub && <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>{sub(r)}</div>}
        </div>
      ))}
    </div>
  );
}
