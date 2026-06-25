import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const ACTIVE_JOB_KEY = 'billscan_active_job_id';

const apiFetchMock = vi.fn();
const downloadExcelMock = vi.fn().mockResolvedValue(undefined);

vi.mock('../utils/formatters', () => ({
  API_BASE: 'https://test-api.example.com',
  apiFetch: (...args) => apiFetchMock(...args),
  downloadExcel: (...args) => downloadExcelMock(...args),
}));

const { default: Processing } = await import('../pages/Processing');

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type, cb) {
    (this.listeners[type] ||= []).push(cb);
  }
  emit(type, data) {
    (this.listeners[type] || []).forEach((cb) => cb({ data: JSON.stringify(data) }));
  }
  close() {
    this.closed = true;
  }
}
FakeEventSource.instances = [];

function statusResponse(overrides = {}) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      job_id: 'job-1',
      status: 'processing',
      total_files: 5,
      processed_files: 0,
      verified_count: 0,
      flagged_count: 0,
      error_count: 0,
      total_pages: 5,
      completed_pages: 0,
      failed_pages: 0,
      ...overrides,
    }),
  };
}

beforeEach(() => {
  localStorage.clear();
  apiFetchMock.mockReset();
  downloadExcelMock.mockClear();
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  localStorage.setItem('xtract_token', 'fake-token');
});

describe('Processing.jsx — jobId resolution and persistence', () => {
  it('mounts using a jobId resolved from localStorage (App.jsx already restored it as a prop) and re-affirms it', async () => {
    // App.jsx's lazy init is what reads localStorage and supplies this prop on a
    // refreshed boot — Processing.jsx's own job is to re-affirm persistence and
    // open the status poll / SSE stream from whatever jobId it was given.
    localStorage.setItem(ACTIVE_JOB_KEY, 'job-from-storage');
    apiFetchMock.mockResolvedValue(statusResponse());

    render(<Processing navigate={vi.fn()} toast={vi.fn()} jobId="job-from-storage" />);

    expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBe('job-from-storage');
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/status/job-from-storage'));
    expect(FakeEventSource.instances[0].url).toContain('/stream/job-from-storage');
  });

  it('redirects to dashboard immediately when no jobId is available at all', () => {
    const navigate = vi.fn();
    render(<Processing navigate={navigate} toast={vi.fn()} jobId={null} />);
    expect(navigate).toHaveBeenCalledWith('dashboard');
  });
});

describe('Processing.jsx — 404 handling', () => {
  it('clears localStorage, toasts, and redirects to dashboard on a 404 from /status', async () => {
    localStorage.setItem(ACTIVE_JOB_KEY, 'job-1');
    apiFetchMock.mockResolvedValue({ ok: false, status: 404 });
    const navigate = vi.fn();
    const toast = vi.fn();

    render(<Processing navigate={navigate} toast={toast} jobId="job-1" />);

    await waitFor(() => expect(toast).toHaveBeenCalledWith('Previous job not found'));
    expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
    expect(navigate).toHaveBeenCalledWith('dashboard');
  });
});

describe('Processing.jsx — terminal states', () => {
  it('clears localStorage when the job reaches done', async () => {
    localStorage.setItem(ACTIVE_JOB_KEY, 'job-1');
    apiFetchMock.mockResolvedValue(statusResponse({ status: 'done', completed_pages: 5, total_pages: 5 }));

    render(<Processing navigate={vi.fn()} toast={vi.fn()} jobId="job-1" />);

    await waitFor(() => expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBeNull());
    expect(await screen.findByText(/extraction complete/i)).toBeInTheDocument();
  });

  it('shows the partial-success banner and an enabled download button on completed_with_errors', async () => {
    localStorage.setItem(ACTIVE_JOB_KEY, 'job-1');
    apiFetchMock.mockResolvedValue(statusResponse({
      status: 'completed_with_errors',
      completed_pages: 4,
      failed_pages: 1,
      total_pages: 5,
      error_count: 1,
    }));

    render(<Processing navigate={vi.fn()} toast={vi.fn()} jobId="job-1" />);

    expect(await screen.findByText(/partial success/i)).toBeInTheDocument();
    expect(screen.getByText(/4 of 5 pages extracted successfully/i)).toBeInTheDocument();
    const downloadBtn = screen.getByRole('button', { name: /download excel/i });
    expect(downloadBtn).toBeInTheDocument();
    expect(downloadBtn).not.toBeDisabled();
    await waitFor(() => expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBeNull());
  });

  it('clears localStorage and shows the failed state when status is error', async () => {
    localStorage.setItem(ACTIVE_JOB_KEY, 'job-1');
    apiFetchMock.mockResolvedValue(statusResponse({ status: 'error' }));

    render(<Processing navigate={vi.fn()} toast={vi.fn()} jobId="job-1" />);

    expect(await screen.findByText(/extraction failed/i)).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBeNull());
  });

  it.each(['done', 'completed_with_errors', 'error'])(
    'closes the SSE EventSource when the terminal status is %s',
    async (status) => {
      localStorage.setItem(ACTIVE_JOB_KEY, 'job-1');
      apiFetchMock.mockResolvedValue(statusResponse({ status, completed_pages: 4, failed_pages: status === 'error' ? 0 : 1 }));

      render(<Processing navigate={vi.fn()} toast={vi.fn()} jobId="job-1" />);

      await waitFor(() => {
        expect(FakeEventSource.instances[0].closed).toBe(true);
      });
    }
  );
});

describe('Processing.jsx — progress bar', () => {
  it('renders progress driven by completed_pages/total_pages from /status, not SSE', async () => {
    localStorage.setItem(ACTIVE_JOB_KEY, 'job-1');
    apiFetchMock.mockResolvedValue(statusResponse({ status: 'processing', completed_pages: 3, total_pages: 12 }));

    render(<Processing navigate={vi.fn()} toast={vi.fn()} jobId="job-1" />);

    expect(await screen.findByText('3 / 12 pages')).toBeInTheDocument();
  });
});
