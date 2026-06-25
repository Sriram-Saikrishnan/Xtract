import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const ACTIVE_JOB_KEY = 'billscan_active_job_id';

const apiFetchMock = vi.fn();
vi.mock('../utils/formatters', () => ({
  apiFetch: (...args) => apiFetchMock(...args),
}));

const { default: Upload } = await import('../pages/Upload');

describe('Upload.jsx — job id persistence on successful upload', () => {
  beforeEach(() => {
    localStorage.clear();
    apiFetchMock.mockReset();
  });

  it('writes jobId to localStorage under the fixed key after a successful upload', async () => {
    apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ job_id: 'job-abc-123' }),
    });

    const navigate = vi.fn();
    const toast = vi.fn();
    const user = userEvent.setup();

    render(<Upload navigate={navigate} toast={toast} />);

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
    apiFetchMock.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: 'Upload failed' }),
    });

    const navigate = vi.fn();
    const toast = vi.fn();
    const user = userEvent.setup();

    render(<Upload navigate={navigate} toast={toast} />);

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
});
