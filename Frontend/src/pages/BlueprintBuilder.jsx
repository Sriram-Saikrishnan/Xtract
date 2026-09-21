import { useState, useEffect } from 'react';
import { Ic } from '../components/icons';
import { apiFetch } from '../utils/formatters';

const FIELD_TYPES = ['string', 'number', 'date', 'currency', 'boolean', 'enum', 'table'];
const INPUT_TYPES = ['pdf', 'image', 'mixed'];

function parseColumns(str) {
  if (!str || !str.trim()) return null;
  return str.split(',').map(s => s.trim()).filter(Boolean).map(pair => {
    const [key, type] = pair.split(':').map(x => x.trim());
    return { key, type: type || 'string' };
  });
}

function columnsToStr(cols) {
  if (!cols || !cols.length) return '';
  return cols.map(c => `${c.key}:${c.type}`).join(', ');
}

let tempId = 0;
function blankField(sortOrder) {
  return {
    _tempId: `new-${++tempId}`, id: null,
    field_key: '', display_label: '', field_type: 'string',
    description: '', example_value: '', is_required: false,
    enum_values: '', table_columns: null, sort_order: sortOrder,
    _columnsStr: '',
  };
}

export default function BlueprintBuilder({ navigate, toast, blueprintId, projectId }) {
  const [blueprint, setBlueprint] = useState(null);
  const [fields, setFields] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingMeta, setSavingMeta] = useState(false);
  const [mode, setMode] = useState('manual'); // manual | prompt
  const [prompt, setPrompt] = useState('');
  const [generating, setGenerating] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [rowBusy, setRowBusy] = useState(null);
  const [savingAll, setSavingAll] = useState(false);
  const unsavedCount = fields.filter(f => !f.id).length;

  const load = () => {
    if (!blueprintId) { navigate('projects'); return; }
    apiFetch(`/blueprints/${blueprintId}`)
      .then(r => r.json())
      .then(bp => {
        setBlueprint(bp);
        setFields((bp.fields || []).map(f => ({ ...f, _tempId: f.id, _columnsStr: columnsToStr(f.table_columns) })));
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(load, [blueprintId]);

  const updateMeta = async (patch) => {
    setSavingMeta(true);
    try {
      const res = await apiFetch(`/blueprints/${blueprintId}`, { method: 'PATCH', body: JSON.stringify(patch) });
      if (!res.ok) throw new Error('Update failed');
      const bp = await res.json();
      setBlueprint(prev => ({ ...prev, ...bp }));
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setSavingMeta(false);
    }
  };

  const updateRow = (tempId_, patch) => {
    setFields(prev => prev.map(f => f._tempId === tempId_ ? { ...f, ...patch } : f));
  };

  const addRow = () => {
    setFields(prev => [...prev, blankField(prev.length)]);
  };

  // Shared persist path for a single row — POST if new, PATCH if it already
  // has an id. Throws on failure/validation error so callers (single-row
  // save vs. bulk save-all) can decide how to report it.
  const persistField = async (row) => {
    if (!row.field_key.trim() || !row.display_label.trim()) {
      throw new Error(`"${row.field_key || row.display_label || 'field'}": field key and display label are required`);
    }
    const payload = {
      field_key: row.field_key.trim(),
      display_label: row.display_label.trim(),
      field_type: row.field_type,
      description: row.description || null,
      example_value: row.example_value || null,
      is_required: !!row.is_required,
      enum_values: row.field_type === 'enum' ? (row.enum_values || null) : null,
      table_columns: row.field_type === 'table' ? parseColumns(row._columnsStr) : null,
      sort_order: row.sort_order,
    };
    const res = await apiFetch(
      row.id ? `/blueprints/${blueprintId}/fields/${row.id}` : `/blueprints/${blueprintId}/fields`,
      { method: row.id ? 'PATCH' : 'POST', body: JSON.stringify(payload) },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `"${row.field_key}": save failed`);
    }
    return res.json();
  };

  const saveRow = async (row) => {
    setRowBusy(row._tempId);
    try {
      const saved = await persistField(row);
      updateRow(row._tempId, { ...saved, _columnsStr: columnsToStr(saved.table_columns) });
      toast('Field saved');
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setRowBusy(null);
    }
  };

  // Persists every row that isn't already saved — the safety net for both
  // manually-added rows and prompt-generated rows a user forgot to save
  // individually. Without this, unsaved rows only ever exist in local
  // component state and vanish on navigation, silently leaving the
  // blueprint with zero persisted fields at extraction time.
  const saveAllUnsaved = async (rows) => {
    const unsaved = rows.filter(f => !f.id);
    if (unsaved.length === 0) return true;
    setSavingAll(true);
    let okCount = 0;
    try {
      for (const row of unsaved) {
        try {
          const saved = await persistField(row);
          setFields(prev => prev.map(f => f._tempId === row._tempId ? { ...saved, _tempId: saved.id, _columnsStr: columnsToStr(saved.table_columns) } : f));
          okCount++;
        } catch (e) {
          toast('Error: ' + e.message);
        }
      }
      if (okCount > 0) toast(`${okCount} field(s) saved`);
      return okCount === unsaved.length;
    } finally {
      setSavingAll(false);
    }
  };

  const deleteRow = async (row) => {
    if (!row.id) { setFields(prev => prev.filter(f => f._tempId !== row._tempId)); return; }
    setRowBusy(row._tempId);
    try {
      const res = await apiFetch(`/blueprints/${blueprintId}/fields/${row.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Delete failed');
      setFields(prev => prev.filter(f => f._tempId !== row._tempId));
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setRowBusy(null);
    }
  };

  const moveRow = async (index, dir) => {
    const target = index + dir;
    if (target < 0 || target >= fields.length) return;
    const reordered = [...fields];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    if (reordered.some(f => !f.id)) { toast('Save all fields before reordering'); return; }
    setFields(reordered);
    try {
      const res = await apiFetch(`/blueprints/${blueprintId}/fields/reorder`, {
        method: 'PATCH',
        body: JSON.stringify({ field_ids: reordered.map(f => f.id) }),
      });
      if (!res.ok) throw new Error('Reorder failed');
      const saved = await res.json();
      setFields(saved.map(f => ({ ...f, _tempId: f.id, _columnsStr: columnsToStr(f.table_columns) })));
    } catch (e) {
      toast('Error: ' + e.message);
      load();
    }
  };

  const handleGenerate = async () => {
    if (!prompt.trim()) { toast('Describe what to extract first'); return; }
    setGenerating(true);
    try {
      const res = await apiFetch(`/blueprints/${blueprintId}/generate-fields`, {
        method: 'POST', body: JSON.stringify({ prompt: prompt.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Generation failed');
      }
      const data = await res.json();
      const startOrder = fields.length;
      const newRows = (data.fields || []).map((f, i) => ({
        _tempId: `gen-${++tempId}`, id: null,
        field_key: f.field_key || '', display_label: f.display_label || f.field_key || '',
        field_type: FIELD_TYPES.includes(f.field_type) ? f.field_type : 'string',
        description: f.description || '', example_value: f.example_value || '',
        is_required: false, enum_values: '',
        table_columns: f.field_type === 'table' ? (f.table_columns || null) : null,
        sort_order: startOrder + i,
        _columnsStr: f.field_type === 'table' ? columnsToStr(f.table_columns) : '',
      }));
      setFields(prev => [...prev, ...newRows]);
      setPrompt('');

      // Persist immediately — a generated field the user never explicitly
      // clicked "Save" on used to only exist in local state and silently
      // disappeared on navigation, leaving the blueprint with zero fields
      // at extraction time even though "fields" appeared populated on screen.
      let saved = 0;
      for (const row of newRows) {
        try {
          const result = await persistField(row);
          setFields(prev => prev.map(f => f._tempId === row._tempId ? { ...result, _tempId: result.id, _columnsStr: columnsToStr(result.table_columns) } : f));
          saved++;
        } catch (e) {
          toast(`Could not save generated field: ${e.message}`);
        }
      }
      toast(`${saved} of ${newRows.length} generated field(s) saved — edit or delete below as needed`);
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setGenerating(false);
    }
  };

  const handleTest = async (file) => {
    if (!file) return;
    setTesting(true);
    setTestResult(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('fields', JSON.stringify(fields.map(f => ({
        field_key: f.field_key, field_type: f.field_type, description: f.description,
        example_value: f.example_value, is_required: f.is_required,
        enum_values: f.field_type === 'enum' ? f.enum_values : null,
        table_columns: f.field_type === 'table' ? parseColumns(f._columnsStr) : null,
      }))));
      const res = await apiFetch(`/blueprints/${blueprintId}/test`, { method: 'POST', body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Test failed');
      }
      const data = await res.json();
      setTestResult(data);
    } catch (e) {
      toast('Error: ' + e.message);
    } finally {
      setTesting(false);
    }
  };

  if (loading) return (
    <div className="page"><div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Loading…</div></div>
  );
  if (!blueprint) return (
    <div className="page"><div className="empty"><p>Blueprint not found.</p><button className="btn btn-primary mt-2" onClick={() => navigate('projects')}>Back</button></div></div>
  );

  return (
    <div className="page">
      <div className="breadcrumb">
        <a onClick={() => navigate('projects')}>Projects</a>
        <span className="sep">/</span>
        <a onClick={() => navigate('project-detail', { projectId: projectId || blueprint.project_id })}>Project</a>
        <span className="sep">/</span>
        <span className="current">{blueprint.name}</span>
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">{blueprint.name}</h1>
          <p className="page-sub">Define the fields Gemini should extract from each document.</p>
        </div>
        <div className="row gap-2">
          <button
            className="btn btn-primary"
            title={unsavedCount > 0 ? 'Save all fields first — unsaved fields are not used during extraction' : undefined}
            onClick={() => {
              if (unsavedCount > 0) { toast('Save all fields first — unsaved fields will not be extracted'); return; }
              if (fields.length === 0) { toast('Add at least one field before uploading'); return; }
              navigate('upload', { projectId: projectId || blueprint.project_id, blueprintId });
            }}
          >
            <Ic.upload style={{ width: 14, height: 14 }} /> Upload with this blueprint
          </button>
        </div>
      </div>

      <div className="card mb-3">
        <div className="card-pad row gap-4" style={{ flexWrap: 'wrap' }}>
          <div className="form-row" style={{ marginBottom: 0, minWidth: 160 }}>
            <label>Input type</label>
            <select className="select" value={blueprint.input_type} disabled={savingMeta}
              onChange={e => updateMeta({ input_type: e.target.value })}>
              {INPUT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div style={{ flex: 1, minWidth: 240 }}>
            <div className="form-row" style={{ marginBottom: 0 }}>
              <label>Extraction prompt override (optional)</label>
              <textarea
                className="input" rows={2}
                defaultValue={blueprint.extraction_prompt_override || ''}
                placeholder="Leave blank to auto-build the prompt from project context + field descriptions"
                onBlur={e => updateMeta({ extraction_prompt_override: e.target.value || null })}
              />
            </div>
          </div>
        </div>
      </div>

      <div className="card mb-3">
        <div className="card-head">
          <h3 className="card-title">Fields</h3>
          <div className="seg" role="tablist">
            <button className={`seg-btn ${mode === 'manual' ? 'active' : ''}`} onClick={() => setMode('manual')}>Manual</button>
            <button className={`seg-btn ${mode === 'prompt' ? 'active' : ''}`} onClick={() => setMode('prompt')}>Generate from prompt</button>
          </div>
        </div>

        {mode === 'prompt' && (
          <div className="card-pad" style={{ borderBottom: '0.5px solid var(--border)' }}>
            <div className="row gap-2">
              <textarea
                className="input" rows={2} style={{ flex: 1 }}
                placeholder="Describe what to extract, e.g. 'supplier name, invoice number, grand total, and a line items table'"
                value={prompt} onChange={e => setPrompt(e.target.value)}
              />
              <button className="btn btn-primary" onClick={handleGenerate} disabled={generating} style={{ alignSelf: 'flex-start' }}>
                {generating ? <span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }}></span> : 'Generate'}
              </button>
            </div>
          </div>
        )}

        <div style={{ overflowX: 'auto' }}>
          <table className="line-tbl" style={{ minWidth: 900 }}>
            <thead>
              <tr>
                <th style={{ width: 40 }}></th>
                <th>Field key</th>
                <th>Display label</th>
                <th>Type</th>
                <th>Description</th>
                <th>Example</th>
                <th>Columns (table type)</th>
                <th style={{ textAlign: 'center' }}>Req.</th>
                <th style={{ width: 100 }}></th>
              </tr>
            </thead>
            <tbody>
              {fields.map((row, idx) => (
                <tr key={row._tempId} style={!row.id ? { background: 'rgba(251,233,204,0.35)' } : undefined}>
                  <td>
                    <div className="row" style={{ flexDirection: 'column', gap: 2 }}>
                      <button className="icon-btn" style={{ width: 20, height: 20 }} disabled={idx === 0} onClick={() => moveRow(idx, -1)}>▲</button>
                      <button className="icon-btn" style={{ width: 20, height: 20 }} disabled={idx === fields.length - 1} onClick={() => moveRow(idx, 1)}>▼</button>
                    </div>
                  </td>
                  <td><input className="input" style={{ minWidth: 120 }} value={row.field_key} onChange={e => updateRow(row._tempId, { field_key: e.target.value })} /></td>
                  <td><input className="input" style={{ minWidth: 120 }} value={row.display_label} onChange={e => updateRow(row._tempId, { display_label: e.target.value })} /></td>
                  <td>
                    <select className="select" style={{ minWidth: 100 }} value={row.field_type} onChange={e => updateRow(row._tempId, { field_type: e.target.value })}>
                      {FIELD_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </td>
                  <td><input className="input" style={{ minWidth: 160 }} value={row.description || ''} onChange={e => updateRow(row._tempId, { description: e.target.value })} /></td>
                  <td><input className="input" style={{ minWidth: 100 }} value={row.example_value || ''} onChange={e => updateRow(row._tempId, { example_value: e.target.value })} /></td>
                  <td>
                    {row.field_type === 'table' ? (
                      <input
                        className="input" style={{ minWidth: 160 }}
                        placeholder="description:string, amount:currency"
                        value={row._columnsStr} onChange={e => updateRow(row._tempId, { _columnsStr: e.target.value })}
                      />
                    ) : row.field_type === 'enum' ? (
                      <input
                        className="input" style={{ minWidth: 160 }}
                        placeholder="allowed,values,here"
                        value={row.enum_values || ''} onChange={e => updateRow(row._tempId, { enum_values: e.target.value })}
                      />
                    ) : <span className="muted">—</span>}
                  </td>
                  <td style={{ textAlign: 'center' }}>
                    <input type="checkbox" checked={!!row.is_required} onChange={e => updateRow(row._tempId, { is_required: e.target.checked })} />
                  </td>
                  <td>
                    <div className="row gap-2">
                      <button className="btn btn-secondary btn-sm" disabled={rowBusy === row._tempId} onClick={() => saveRow(row)}>
                        {rowBusy === row._tempId ? <span className="spinner" style={{ width: 10, height: 10 }} /> : 'Save'}
                      </button>
                      <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={() => deleteRow(row)}>
                        <Ic.trash style={{ width: 13, height: 13 }} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card-pad row gap-2" style={{ justifyContent: 'space-between' }}>
          <button className="btn btn-secondary" onClick={addRow}>
            <Ic.plus style={{ width: 14, height: 14 }} /> Add field
          </button>
          {unsavedCount > 0 && (
            <div className="row gap-2">
              <span className="badge badge-amber">{unsavedCount} unsaved</span>
              <button className="btn btn-primary btn-sm" onClick={() => saveAllUnsaved(fields)} disabled={savingAll}>
                {savingAll ? <span className="spinner" style={{ width: 10, height: 10, borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }} /> : 'Save all'}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3 className="card-title">Test with a sample file</h3></div>
        <div className="card-pad">
          <p style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 0 }}>
            Runs one extraction against the fields above (including unsaved edits) — nothing is written to the database.
          </p>
          <input
            type="file" accept=".pdf,.jpg,.jpeg,.png,.heic"
            onChange={e => handleTest(e.target.files[0])}
            disabled={testing}
          />
          {testing && <div className="loading"><span className="spinner" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--green)' }}></span> Running extraction…</div>}
          {testResult && (
            <pre style={{
              marginTop: 14, background: 'var(--surface)', border: '0.5px solid var(--border)',
              borderRadius: 10, padding: 14, fontSize: 12, overflowX: 'auto', maxHeight: 400,
            }}>
              {JSON.stringify(testResult, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
