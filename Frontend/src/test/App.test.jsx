import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const ACTIVE_JOB_KEY = 'billscan_active_job_id';

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', name: 'Test User' }, logout: vi.fn() }),
}));

// Stub every page so App.jsx's own routing/boot logic is what's under test,
// not the full subtree of each real page.
vi.mock('../pages/Dashboard', () => ({ default: () => <div data-testid="page-dashboard" /> }));
vi.mock('../pages/Upload', () => ({ default: () => <div data-testid="page-upload" /> }));
vi.mock('../pages/Processing', () => ({
  default: (props) => <div data-testid="page-processing" data-jobid={props.jobId} />,
}));
vi.mock('../pages/ProjectsList', () => ({ default: () => <div data-testid="page-projects" /> }));
vi.mock('../pages/ProjectForm', () => ({ default: () => <div data-testid="page-project-form" /> }));
vi.mock('../pages/ProjectDetail', () => ({ default: () => <div data-testid="page-project-detail" /> }));
vi.mock('../pages/BlueprintBuilder', () => ({ default: () => <div data-testid="page-blueprint-builder" /> }));
vi.mock('../pages/ResultsList', () => ({ default: () => <div data-testid="page-results" /> }));
vi.mock('../pages/ResultDetail', () => ({ default: () => <div data-testid="page-result-detail" /> }));
vi.mock('../pages/Profile', () => ({ default: () => <div data-testid="page-profile" /> }));
vi.mock('../pages/Login', () => ({ default: () => <div data-testid="page-login" /> }));
vi.mock('../pages/Signup', () => ({ default: () => <div data-testid="page-signup" /> }));

const { default: App } = await import('../App');

describe('App.jsx boot-time page restoration', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('boots to the dashboard when no active job is in localStorage', () => {
    render(<App />);
    expect(screen.getByTestId('page-dashboard')).toBeInTheDocument();
    expect(screen.queryByTestId('page-processing')).not.toBeInTheDocument();
  });

  it('boots straight to Processing when a stale job id is in localStorage', () => {
    localStorage.setItem(ACTIVE_JOB_KEY, 'job-restored-123');
    render(<App />);
    const processing = screen.getByTestId('page-processing');
    expect(processing).toBeInTheDocument();
    expect(processing.dataset.jobid).toBe('job-restored-123');
  });
});
