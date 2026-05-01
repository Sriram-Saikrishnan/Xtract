import { useState, useRef } from 'react';
import { Ic } from '../components/icons';
import FileIcon from '../components/FileIcon';
import { apiFetch } from '../utils/formatters';

export default function Upload({ navigate, toast }) {
  const [files, setFiles] = useState([]);
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef();

  const addFiles = (newFiles) => {
    const arr = Array.from(newFiles).filter(f => /\.(pdf|jpg|jpeg|png|heic)$/i.test(f.name));
    setFiles(prev => {
      const existing = new Set(prev.map(f => f.name));
      return [...prev, ...arr.filter(f => !existing.has(f.name))];
    });
    if (newFiles.length > arr.length) toast('Some files skipped — only PDF, JPG, PNG, HEIC allowed');
  };

  const remove = (i) => setFiles(files.filter((_, x) => x !== i));

  const handleDrop = (e) => {
    e.preventDefault();
    setDrag(false);
    addFiles(e.dataTransfer.files);
  };

  const handleSubmit = async () => {
    if (!files.length) return;
    setUploading(true);
    try {
      const fd = new FormData();
      files.forEach(f => fd.append('files', f));
      const res = await apiFetch('/upload', { method: 'POST', body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Upload failed');
      }
      const data = await res.json();
      toast('Upload started!');
      navigate('processing', {
        jobId: data.job_id,
        uploadedFiles: files.map(f => ({ name: f.name, size: f.size, type: f.name.split('.').pop().toLowerCase() })),
      });
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setUploading(false);
    }
  };

  const totalMB = (files.reduce((s, f) => s + f.size, 0) / 1024 / 1024).toFixed(1);

  return (
    <div className="page">
      <div className="breadcrumb">
        <a onClick={() => navigate('dashboard')}>Dashboard</a>
        <span className="sep">/</span>
        <span className="current">New Extraction</span>
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Upload documents</h1>
          <p className="page-sub">Drop invoices, bills and receipts. We'll extract structured data in seconds.</p>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 18 }}>
        <div>
          <div
            className={`dropzone ${drag ? 'drag' : ''}`}
            onDragOver={e => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={handleDrop}
            onClick={() => inputRef.current.click()}
            style={{ cursor: 'pointer' }}
          >
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".pdf,.jpg,.jpeg,.png,.heic"
              style={{ display: 'none' }}
              onChange={e => addFiles(e.target.files)}
            />
            <div className="drop-icon"><Ic.upload style={{ width: 26, height: 26 }} /></div>
            <div className="drop-title">Drop your invoices &amp; bills here</div>
            <div className="drop-sub">PDF, PNG, JPG, HEIC · Up to 10 MB per file</div>
            <button className="btn btn-secondary" onClick={e => { e.stopPropagation(); inputRef.current.click(); }}>Browse files</button>
          </div>

          <div className="card mt-3">
            <div className="card-head">
              <div>
                <h3 className="card-title">Queue</h3>
                <div className="card-sub">{files.length} files · {totalMB} MB</div>
              </div>
              {files.length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => setFiles([])}>Clear all</button>}
            </div>
            <div className="scroll-list">
              {files.length === 0 ? (
                <div className="empty">
                  <div className="empty-icon"><Ic.inbox style={{ width: 22, height: 22 }} /></div>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>Queue is empty</div>
                  <div style={{ fontSize: 12.5 }}>Drop files above or click Browse.</div>
                </div>
              ) : files.map((f, i) => (
                <div key={i} className="queue-row">
                  <FileIcon type={f.name.split('.').pop().toLowerCase()} />
                  <div style={{ minWidth: 0 }}>
                    <div className="fname">{f.name}</div>
                    <div className="fmeta">{(f.size / 1024).toFixed(0)} KB</div>
                  </div>
                  <span className="badge badge-gray">Ready</span>
                  <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={() => remove(i)}>
                    <Ic.close style={{ width: 14, height: 14 }} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div>
          <div className="card">
            <div className="card-head"><h3 className="card-title">Info</h3></div>
            <div className="card-pad" style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.6 }}>
              <p style={{ margin: '0 0 10px' }}>Each page of a multi-page PDF is extracted as a <strong>separate invoice</strong> automatically.</p>
              <p style={{ margin: '0 0 10px' }}>Supported: Tax Invoice, Delivery Challan, Receipt, Proforma.</p>
              <p style={{ margin: 0 }}>Results are exported to Excel with line items, GST summary, and flagged rows.</p>
            </div>
          </div>
        </div>
      </div>

      <div className="sticky-bar">
        <div>
          <div style={{ fontWeight: 600 }}>{files.length} {files.length === 1 ? 'file' : 'files'} ready · {totalMB} MB</div>
          <div className="muted" style={{ fontSize: 12 }}>Est. ~{Math.max(1, Math.round(files.length * 0.5))} min processing</div>
        </div>
        <div className="row gap-2">
          <button className="btn btn-ghost" onClick={() => navigate('dashboard')}>Cancel</button>
          <button className="btn btn-primary btn-lg" disabled={files.length === 0 || uploading} onClick={handleSubmit}>
            {uploading
              ? <><span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span> Uploading…</>
              : <>Extract {files.length} {files.length === 1 ? 'file' : 'files'} <Ic.arrow style={{ width: 14, height: 14 }} /></>}
          </button>
        </div>
      </div>
    </div>
  );
}
