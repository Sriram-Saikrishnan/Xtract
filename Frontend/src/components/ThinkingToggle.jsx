export const THINKING_HELP = 'Turn on for handwritten or complex, table-heavy documents. Slower and ~3x costlier.';

/** Blueprint "Thinking" switch with helper text — used on create and in blueprint settings. */
export default function ThinkingToggle({ checked, onChange, disabled }) {
  return (
    <div className="thinking-toggle">
      <button
        type="button"
        role="switch"
        aria-checked={!!checked}
        aria-label="Thinking"
        disabled={disabled}
        className={`toggle ${checked ? 'on' : ''}`}
        onClick={() => onChange(!checked)}
      />
      <div>
        <div className="lbl">Thinking <span className="muted">{checked ? 'On' : 'Off'}</span></div>
        <div className="lbl-sub">{THINKING_HELP}</div>
      </div>
    </div>
  );
}
