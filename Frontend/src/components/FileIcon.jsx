export default function FileIcon({ type, size }) {
  const cls = type === 'pdf' ? 'pdf' : 'img';
  const lbl = (type || 'file').toUpperCase().slice(0, 4);
  return <div className={`file-icon ${cls} ${size === 'lg' ? 'lg' : ''}`}>{lbl}</div>;
}
