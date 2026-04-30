export default function LineChart({ data, height = 180 }) {
  const w = 1000, h = height;
  const padL = 36, padR = 16, padT = 14, padB = 24;
  const max = Math.max(...data, 1);
  const innerW = w - padL - padR, innerH = h - padT - padB;
  const pts = data.map((v, i) => [padL + (i / (data.length - 1)) * innerW, padT + innerH - (v / max) * innerH]);
  const d = 'M' + pts.map(p => p.join(',')).join(' L');
  const fillD = `${d} L${pts[pts.length - 1][0]},${padT + innerH} L${pts[0][0]},${padT + innerH} Z`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" style={{ display: 'block' }}>
      <path d={fillD} fill="var(--green-2)" opacity="0.08" />
      <path d={d} fill="none" stroke="var(--green-2)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
