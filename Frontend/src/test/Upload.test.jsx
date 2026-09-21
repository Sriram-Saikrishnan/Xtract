import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const ACTIVE_JOB_KEY = 'billscan_active_job_id';

const apiFetchMock = vi.fn();
vi.mock('../utils/formatters', () => ({
  apiFetch: (...args) => apiFetchMock(...args),
}));

const { default: Upload } = await import('../pages/Upload');

function mockApi({ uploadOk = true, uploadBody = { job_id: 'job-abc-123' } } = {}) {
  apiFetchMock.mockImplementation((path) => {
    if (path === '/projects') {
      return Promise.resolve({ ok: true, json: async () => [{ id: 'proj-1', name: 'Project One' }] });
    }
    if (path === '/projects/proj-1/blueprints') {
      return Promise.resolve({ ok: true, json: async () => [{ id: 'bp-1', name: 'Blueprint One', is_active: true }] });
    }
    if (path === '/upload') {
      return Promise.resolve({ ok: uploadOk, json: async () => uploadBody });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
}

describe('Upload.jsx — job id persistence on successful upload', () => {
  beforeEach(() => {
    localStorage.clear();
    apiFetchMock.mockReset();
  });

  it('writes jobId to localStorage under the fixed key after a successful upload', async () => {
    mockApi({ uploadOk: true, uploadBody: { job_id: 'job-abc-123' } });

    const navigate = vi.fn();
    const toast = vi.fn();
    const user = userEvent.setup();

    render(<Upload navigate={navigate} toast={toast} />);

    await user.selectOptions(await screen.findByDisplayValue('Select a project'), 'proj-1');
    await user.selectOptions(await screen.findByDisplayValue('Select a blueprint'), 'bp-1');

    const file = new File(['dummy'], 'invoice.pdf', { type: 'application/pdf' });
    const input = document.querySelector('input[type="file"]');
    await user.upload(input, file);

    const submitButton = await screen.findByRole('button', { name: /extract 1 file/i });
    await user.click(submitButton);

    await waitFor(() => {
      expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBe('job-abc-123');
    });

    expect(navigate).toHaveBeenCalledWith('processing', expect.objectContaining({ jobId: 'job-abc-123' }));
  });

  it('does not write to localStorage when the upload fails', async () => {
    mockApi({ uploadOk: false, uploadBody: { detail: 'Upload failed' } });

    const navigate = vi.fn();
    const toast = vi.fn();
    const user = userEvent.setup();

    render(<Upload navigate={navigate} toast={toast} />);

    await user.selectOptions(await screen.findByDisplayValue('Select a project'), 'proj-1');
    await user.selectOptions(await screen.findByDisplayValue('Select a blueprint'), 'bp-1');

    const file = new File(['dummy'], 'invoice.pdf', { type: 'application/pdf' });
    const input = document.querySelector('input[type="file"]');
    await user.upload(input, file);

    const submitButton = await screen.findByRole('button', { name: /extract 1 file/i });
    await user.click(submitButton);

    await waitFor(() => {
      expect(toast).toHaveBeenCalledWith(expect.stringContaining('Error'));
    });
    expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
  });

  it('disables the submit button until a project and blueprint are selected', async () => {
    mockApi();
    render(<Upload navigate={vi.fn()} toast={vi.fn()} />);

    const file = new File(['dummy'], 'invoice.pdf', { type: 'application/pdf' });
    const input = document.querySelector('input[type="file"]');
    await userEvent.setup().upload(input, file);

    const submitButton = await screen.findByRole('button', { name: /extract 1 file/i });
    expect(submitButton).toBeDisabled();
  });
});
