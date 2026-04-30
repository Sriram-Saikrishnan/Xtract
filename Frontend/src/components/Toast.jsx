import { useEffect } from 'react';
import { Ic } from './icons';

export default function Toast({ message, onClose }) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onClose, 3000);
    return () => clearTimeout(t);
  }, [message]);

  if (!message) return null;
  return (
    <div className="toast">
      <Ic.check style={{ width: 16, height: 16, color: 'var(--green)' }} />
      <span>{message}</span>
    </div>
  );
}
