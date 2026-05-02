import { useState, useEffect, useRef } from 'react';
import { Ic } from '../components/icons';
import { API_BASE, downloadExcel } from '../utils/formatters';

const STAGES = [
  { id: 'extraction', label: 'Extraction',          desc: 'Extracting data from documents' },
  { id: 'gstin',      label: 'GSTIN Verification',  desc: 'Verifying supplier GSTINs with govt API' },
  { id: 'compliance', label: 'Compliance Checks',   desc: 'Math validation and duplicate detection' },
  { id: 'excel',      label: 'Excel Report',        desc: 'Building and uploading results' },
];

const INIT_STAGE = { status: 'pending', detail: '', current: 0, total: 0, startTime: null, duration: null };

function fmtStageElapsed(startTime, now) {
  const s = (now - startTime) / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
}

function fmtDuration(ms) {
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
}

function fmtFinal(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function Processing({ navigate, toast, jobId, uploadedFiles }) {
  const [stages, setStages] = useState({
    extraction: { ...INIT_STAGE },
    gstin:      { ...INIT_STAGE },
    compliance: { ...INIT_STAGE },
    excel:      { ...INIT_STAGE },
  });
  const [completionData, setCompletionData] = useState(null);
  const [isDone, setIsDone] = useState(false);
  const [isError, setIsError] = useState(false);
  const [finalDuration, setFinalDuration] = useState(null);
  const [now, setNow] = useState(Date.now());
  const timerRef = useRef(null);
  const esRef = useRef(null);

  useEffect(() => {
    if (!jobId) { navigate('dashboard'); return; }

    const token = localStorage.getItem('xtract_token');
    const es = new EventSource(`${API_BASE}/stream/${jobId}?token=${encodeURIComponent(token)}`);
    esRef.current = es;

    timerRef.current = setInterval(() => setNow(Date.now()), 500);

    es.addEventListener('stage_start', (e) => {
      const { stage, total } = JSON.parse(e.data);
      setStages(prev => ({
        ...prev,
        [stage]: { ...prev[stage], status: 'active', total, startTime: Date.now() },
      }));
    });

    es.addEventListener('stage_progress', (e) => {
      const { stage, detail, current, total } = JSON.parse(e.data);
      setStages(prev => ({
        ...prev,
        [stage]: { ...prev[stage], detail, current, total },
      }));
    });

    es.addEventListener('stage_complete', (e) => {
      const { stage, duration_ms } = JSON.parse(e.data);
      setStages(prev => ({
        ...prev,
        [stage]: { ...prev[stage], status: 'complete', duration: duration_ms },
      }));
    });

    es.addEventListener('processing_complete', (e) => {
      const data = JSON.parse(e.data);
      setCompletionData(data);
      setFinalDuration(data.duration_ms);
      setIsDone(!data.error);
      setIsError(!!data.error);
      clearInterval(timerRef.current);
      es.close();
    });

    es.onerror = () => {
      // SSE connection lost — UI will stay in last-known state
    };

    return () => {
      es.close();
      clearInterval(timerRef.current);
    };
  }, [jobId]);

  const globalElapsed = finalDuration != null
    ? fmtFinal(finalDuration)
    : null;

  const summary = completionData
    ? `${completionData.verified} verified · ${completionData.flagged} flagged · ${completionData.errors} errors`
    : '';

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
            {isDone && summary}
            {isDone && globalElapsed && <span style={{ marginLeft: 10 }}>· Completed in {globalElapsed}</span>}
            {!isDone && !isError && 'Processing your documents…'}
            {isError && 'An error occurred during processing.'}
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

      {/* Stage progress */}
      <div className="card mb-3">
        <div className="card-head">
          <h3 className="card-title">Pipeline</h3>
        </div>
        <div className="stages">
          {STAGES.map((s, idx) => {
            const st = stages[s.id];
            const isLast = idx === STAGES.length - 1;
            return (
              <div key={s.id} className="stage-row">
                <div className="stage-indicator">
                  {st.status === 'active'
                    ? <span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }}></span>
                    : <div className={`stage-dot ${st.status}`}></div>
                  }
                  {!isLast && <div className="stage-line"></div>}
                </div>

                <div className="stage-body">
                  {st.status === 'pending' && (
                    <>
                      <div className="stage-head">
                        <span className="stage-title muted">{s.label}</span>
                      </div>
                      <div className="stage-detail muted">{s.desc}</div>
                    </>
                  )}

                  {st.status === 'active' && (
                    <>
                      <div className="stage-head">
                        <span className="stage-title">{s.label}</span>
                        <span className="pulse-dot" style={{ width: 6, height: 6 }}></span>
                      </div>
                      <div className="stage-detail">{st.detail || s.desc}</div>
                      {st.total > 0 && (
                        <div className="stage-sub">
                          {st.current} of {st.total}
                          {st.startTime && <> · {fmtStageElapsed(st.startTime, now)} elapsed</>}
                        </div>
                      )}
                    </>
                  )}

                  {st.status === 'complete' && (
                    <div className="stage-head">
                      <span className="stage-title">{s.label}</span>
                      <span className="stage-done-badge">done</span>
                      {st.duration != null && (
                        <span className="stage-duration">{fmtDuration(st.duration)}</span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
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
