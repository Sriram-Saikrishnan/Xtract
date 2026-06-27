import { useState, useEffect, useRef } from 'react';
import { Ic } from '../components/icons';
import { API_BASE, apiFetch, downloadExcel } from '../utils/formatters';

const ACTIVE_JOB_KEY = 'billscan_active_job_id';
const ACTIVE_JOB_EVENT = 'active-job-changed';
const TERMINAL_STATUSES = ['done', 'completed_with_errors', 'error'];
const STATUS_POLL_MS = 3000;

const STAGES = [
  { id: 'extraction', label: 'Extraction',          desc: 'Extracting data from documents' },
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

export default function Processing({ navigate, toast, jobId, uploadedFiles, retryMode = false, retryCount = 0 }) {
  const [stages, setStages] = useState({
    extraction: { ...INIT_STAGE },
    compliance: { ...INIT_STAGE },
    excel:      { ...INIT_STAGE },
  });
  const [completionData, setCompletionData] = useState(null);
  const [jobStatus, setJobStatus] = useState('processing'); // processing | done | completed_with_errors | error
  const [finalDuration, setFinalDuration] = useState(null);
  const [pageProgress, setPageProgress] = useState({ completed: 0, total: 0 });
  const [now, setNow] = useState(Date.now());
  const [localRetryMode, setLocalRetryMode] = useState(false);
  const [localRetryCount, setLocalRetryCount] = useState(0);
  const [sseRevision, setSseRevision] = useState(0);
  const [retryLoading, setRetryLoading] = useState(false);
  const timerRef = useRef(null);
  const esRef = useRef(null);
  const pollRef = useRef(null);
  const finishedRef = useRef(false);

  const inRetryMode = retryMode || localRetryMode;
  const retryTotal = retryCount || localRetryCount;

  const isDone = jobStatus === 'done' || jobStatus === 'completed_with_errors';
  const isPartial = jobStatus === 'completed_with_errors';
  const isError = jobStatus === 'error';

  const handleInlineRetry = async () => {
    setRetryLoading(true);
    try {
      const res = await apiFetch(`/jobs/${jobId}/retry`, { method: 'POST' });
      if (!res.ok) { toast('Retry failed'); return; }
      const data = await res.json();
      if (data.retrying === 0) { toast('No pages to retry'); return; }
      setStages({ extraction: { ...INIT_STAGE }, compliance: { ...INIT_STAGE }, excel: { ...INIT_STAGE } });
      setCompletionData(null);
      setJobStatus('processing');
      setFinalDuration(null);
      setPageProgress({ completed: 0, total: data.retrying });
      finishedRef.current = false;
      setLocalRetryMode(true);
      setLocalRetryCount(data.retrying);
      setSseRevision(prev => prev + 1);
    } catch {
      toast('Retry failed');
    } finally {
      setRetryLoading(false);
    }
  };

  // Single entry point for reaching a terminal state, whichever source (SSE
  // or DB poll) detects it first. Guarded so it only runs once.
  const finish = (status, data) => {
    if (finishedRef.current) return;
    finishedRef.current = true;
    setJobStatus(status);
    setCompletionData(data);
    if (data?.duration_ms != null) setFinalDuration(data.duration_ms);
    localStorage.removeItem(ACTIVE_JOB_KEY);
    window.dispatchEvent(new Event(ACTIVE_JOB_EVENT));
    clearInterval(timerRef.current);
    clearInterval(pollRef.current);
    if (esRef.current) esRef.current.close();
  };

  // ── SSE: live per-stage narration ───────────────────────────────────────
  useEffect(() => {
    if (!jobId) { navigate('dashboard'); return; }

    // Re-affirm persistence — covers the case where jobId arrived via
    // navigation state but Upload.jsx's write hasn't happened (e.g. resumed
    // from an older tab) and the case where the user navigates away and back.
    localStorage.setItem(ACTIVE_JOB_KEY, jobId);
    window.dispatchEvent(new Event(ACTIVE_JOB_EVENT));

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
      // SSE has no notion of completed_with_errors — fall back to "failed
      // pages > 0" as a best-effort guess; the DB poll below will correct
      // this if it lands first or shortly after with the authoritative
      // status string. The backend now includes failed_pages/total_pages on
      // this event too, so the summary card sums correctly either way.
      const status = data.error ? 'error' : ((data.failed_pages || 0) > 0 ? 'completed_with_errors' : 'done');
      finish(status, data);
    });

    es.onerror = () => {
      if (finishedRef.current) return;
      es.close();
      setTimeout(() => {
        if (!finishedRef.current) setSseRevision(prev => prev + 1);
      }, 3000);
    };

    return () => {
      es.close();
      clearInterval(timerRef.current);
    };
  }, [jobId, sseRevision]);

  // ── DB-backed status poll: progress bar + authoritative terminal state ──
  useEffect(() => {
    if (!jobId) return;

    const poll = async () => {
      try {
        const res = await apiFetch(`/status/${jobId}`);
        if (res.status === 404) {
          localStorage.removeItem(ACTIVE_JOB_KEY);
          window.dispatchEvent(new Event(ACTIVE_JOB_EVENT));
          clearInterval(pollRef.current);
          toast('Previous job not found');
          navigate('dashboard');
          return;
        }
        if (!res.ok) return; // transient — retry next tick

        const data = await res.json();
        setPageProgress({ completed: data.completed_pages, total: data.total_pages });

        if (TERMINAL_STATUSES.includes(data.status)) {
          finish(data.status, {
            verified: data.verified_count,
            flagged: data.flagged_count,
            errors: data.error_count,
            completed_pages: data.completed_pages,
            failed_pages: data.failed_pages,
            total_pages: data.total_pages,
          });
        }
      } catch {
        // network blip — next poll retries
      }
    };

    poll();
    pollRef.current = setInterval(poll, STATUS_POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [jobId, sseRevision]);

  const globalElapsed = finalDuration != null
    ? fmtFinal(finalDuration)
    : null;

  // Every page lands in exactly one terminal bucket — verified, flagged, or
  // failed — and these three must sum to total_pages. "errors" alone used to
  // be the only failure signal shown here, which hid pages that failed
  // before ever producing a bill (e.g. unreadable files). Show failed_pages
  // explicitly so the displayed counts are always reconcilable with total.
  const failedCount = completionData?.failed_pages ?? completionData?.errors ?? 0;
  const summary = completionData
    ? `${completionData.verified} verified · ${completionData.flagged} flagged · ${failedCount} failed`
    : '';

  const progressCurrent = inRetryMode ? stages.extraction.current : pageProgress.completed;
  const progressTotal = inRetryMode ? (retryTotal || stages.extraction.total || 0) : pageProgress.total;
  const progressPct = progressTotal > 0 ? Math.round((progressCurrent / progressTotal) * 100) : 0;

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
            {inRetryMode && !isDone && !isError
              ? `Retrying ${retryTotal} failed page${retryTotal !== 1 ? 's' : ''}`
              : isPartial ? 'Extraction completed with errors'
              : isDone ? 'Extraction complete'
              : isError ? 'Extraction failed'
              : 'Extracting documents'}
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
            {isPartial && (
              <button className="btn btn-secondary" onClick={handleInlineRetry} disabled={retryLoading}>
                {retryLoading
                  ? <><span className="spinner" style={{ width: 12, height: 12, borderWidth: 1.5 }}></span> Retrying…</>
                  : `Retry Failed Pages (${completionData?.failed_pages ?? 0})`}
              </button>
            )}
            <button className="btn btn-secondary" onClick={() => downloadExcel(jobId).catch(() => toast('Download failed'))}>
              <Ic.download style={{ width: 14, height: 14 }} /> Download Excel
            </button>
            <button className="btn btn-primary btn-lg" onClick={() => navigate('extractions', { jobId })}>
              View results <Ic.arrow style={{ width: 14, height: 14 }} />
            </button>
          </div>
        )}
      </div>

      {isPartial && (
        <div className="card mb-3" style={{ borderColor: 'var(--orange, #d97706)' }}>
          <div className="card-pad" style={{ fontSize: 13.5 }}>
            <strong>Partial success:</strong>{' '}
            {completionData?.completed_pages ?? pageProgress.completed} of {completionData?.total_pages ?? pageProgress.total} pages extracted successfully,{' '}
            {completionData?.failed_pages ?? 0} failed. The Excel report includes everything that succeeded — download is still available above.
          </div>
        </div>
      )}

      {/* Overall progress bar — driven by the DB-backed /status endpoint */}
      {!isDone && !isError && (
        <div className="card mb-3">
          <div className="card-pad">
            <div className="row between" style={{ marginBottom: 6, fontSize: 12.5 }}>
              <span className="muted">{inRetryMode ? 'Retry progress' : 'Overall progress'}</span>
              <span className="muted">{progressCurrent} / {progressTotal || '…'} pages</span>
            </div>
            <div style={{ height: 8, borderRadius: 4, background: 'var(--bg-2, #eee)', overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${progressPct}%`,
                background: 'var(--accent, #2563eb)',
                transition: 'width 0.3s ease',
              }} />
            </div>
          </div>
        </div>
      )}

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
