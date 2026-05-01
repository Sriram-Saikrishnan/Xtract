import { useState, useEffect, useRef } from 'react';
import { Ic } from '../components/icons';
import FileIcon from '../components/FileIcon';
import { apiFetch, downloadExcel } from '../utils/formatters';

export default function Processing({ navigate, toast, jobId, uploadedFiles }) {
  const [status, setStatus] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const intervalRef = useRef(null);
  const elapsedRef = useRef(null);

  useEffect(() => {
    if (!jobId) { navigate('dashboard'); return; }

    const poll = async () => {
      try {
        const res = await apiFetch(`/status/${jobId}`);
        if (res.ok) {
          const d = await res.json();
          setStatus(d);
          if (d.status === 'done' || d.status === 'error') {
            clearInterval(intervalRef.current);
          }
        }
      } catch {}
    };

    poll();
    intervalRef.current = setInterval(poll, 2000);
    elapsedRef.current = setInterval(() => setElapsed(e => e + 1), 1000);
    return () => { clearInterval(intervalRef.current); clearInterval(elapsedRef.current); };
  }, [jobId]);

  const total = status?.total_files || uploadedFiles?.length || 1;
  const processed = status?.processed_files || 0;
  const pct = Math.round((processed / total) * 100);
  const isDone = status?.status === 'done';
  const isError = status?.status === 'error';
  const fmtTime = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

  const fileList = uploadedFiles || Array.from({ length: total }, (_, i) => ({ name: `File ${i + 1}`, type: 'pdf' }));

  return (
    <div className="page">
      <div className="breadcrumb">
        <a onClick={() => navigate('dashboard')}>Dashboard</a>
        <span className="sep">/</span>
        <a onClick={() => navigate('upload')}>Upload</a>
        <span className="sep">/</span>
        <span className="current">Processing</span>
      </div>

      <div className="page-header">
        <div>
          <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {isDone ? 'Extraction complete' : isError ? 'Extraction failed' : 'Extracting documents'}
            {!isDone && !isError && <span className="pulse-dot"></span>}
          </h1>
          <p className="page-sub">
            {isDone
              ? `${status.verified_count} verified · ${status.flagged_count} flagged · ${status.error_count} errors`
              : `${processed} of ${total} files processed · ${fmtTime(elapsed)} elapsed`}
          </p>
        </div>
        {isDone && (
          <div className="row gap-2">
            <button className="btn btn-secondary" onClick={() => downloadExcel(jobId).catch(() => toast('Download failed'))}>
              <Ic.download style={{ width: 14, height: 14 }} /> Download Excel
            </button>
            <button className="btn btn-primary btn-lg" onClick={() => navigate('extractions', { jobId })}>
              View results <Ic.arrow style={{ width: 14, height: 14 }} />
            </button>
          </div>
        )}
      </div>

      <div className="card card-pad mb-3">
        <div className="row between mb-1">
          <div className="text-mono" style={{ fontWeight: 700, fontSize: 15 }}>{processed} / {total}</div>
          <div className="muted text-mono" style={{ fontSize: 12.5 }}>{pct}%</div>
        </div>
        <div className="progress thick">
          <div className="fill" style={{ width: `${pct}%` }}></div>
        </div>
      </div>

      <div className="card mb-3">
        <div className="card-head">
          <h3 className="card-title">Files</h3>
          <div className="row gap-2">
            {status && <>
              <span className="badge badge-green">{status.verified_count} verified</span>
              {status.flagged_count > 0 && <span className="badge badge-amber">{status.flagged_count} flagged</span>}
              {status.error_count > 0 && <span className="badge badge-red">{status.error_count} errors</span>}
            </>}
          </div>
        </div>
        <div className="scroll-list">
          {fileList.map((f, i) => {
            const done = i < processed;
            const active = i === processed && !isDone;
            return (
              <div key={i} className={`proc-row ${done ? 'done' : ''}`}>
                <FileIcon type={f.type || 'pdf'} />
                <div className="fname">{f.name}</div>
                <div className="pages"></div>
                <div>
                  {done && <span className="badge badge-green"><Ic.check style={{ width: 11, height: 11 }} />Done</span>}
                  {active && <span className="badge badge-amber"><span className="spinner" style={{ width: 9, height: 9, borderWidth: 1.5 }}></span>Processing</span>}
                  {!done && !active && <span className="badge badge-gray">Queued</span>}
                </div>
                <div>{active && <div className="progress"><div className="fill" style={{ width: '60%', animation: 'none' }}></div></div>}</div>
                <div className="conf"></div>
                <div className="time-col"></div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="stats-strip mb-3">
        <div><div className="lbl">Files</div><div className="val">{total}</div></div>
        <div><div className="lbl">Done</div><div className="val">{processed}</div></div>
        <div><div className="lbl">Verified</div><div className="val">{status?.verified_count ?? '—'}</div></div>
        <div><div className="lbl">Flagged</div><div className="val">{status?.flagged_count ?? '—'}</div></div>
        <div><div className="lbl">Elapsed</div><div className="val">{fmtTime(elapsed)}</div></div>
      </div>

      <div className="row between">
        <div className="muted" style={{ fontSize: 12.5 }}>Job ID: <span className="text-mono">{jobId}</span></div>
        <div className="row gap-2">
          {!isDone && !isError && <button className="btn btn-ghost" onClick={() => navigate('dashboard')}>Run in background</button>}
          {(isDone || isError) && <button className="btn btn-secondary" onClick={() => navigate('upload')}>New batch</button>}
        </div>
      </div>
    </div>
  );
}
