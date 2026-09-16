/**
 * Smoke test for the Agent Runner monitoring dashboard.
 *
 * Validates the local-snapshot dashboard without touching real GitHub: the
 * first paint and polling come from `GET /overview/snapshots`, manual refreshes
 * go through `/overview/per-repo` jobs, and the sync settings panel talks to
 * its canonical endpoint. All read-only API is stubbed at the browser boundary.
 */

import { expect, test } from '../../fixtures/session.fixture'
import {
  AgentRunnerMonitorPage,
  type MonitoringRepositoryOverview,
  type MonitorSnapshotsResponse,
} from '../../page-objects/AgentRunnerMonitorPage'

const KEDA_TEST_OVERVIEW: MonitoringRepositoryOverview = {
  repo_id: 'zata/keda-test',
  display_name: 'Keda Test',
  enabled: true,
  base_branch: 'main',
  remote: 'origin',
  health: {
    gh_available: true,
    repo_path_exists: true,
    publish_remote_exists: true,
  },
  queue_counts: {
    ready: 0,
    running: 0,
    supervising: 1,
    review: 0,
    failed: 0,
    blocked: 0,
  },
  labels: {
    ready: 'agent/ready',
    running: 'agent/running',
    supervising: 'agent/supervising',
    review: 'agent/review',
    failed: 'agent/failed',
    blocked: 'agent/blocked',
  },
  issues: [
    {
      number: 100,
      title: 'Test Issue with Anomaly',
      url: 'https://github.com/zata/keda-test/issues/100',
      labels: ['agent/supervising', 'source/prd'],
      state: 'open',
      primary_label: 'agent/supervising',
      pr: {
        number: 101,
        url: 'https://github.com/zata/keda-test/pull/101',
        branch: 'issue-100',
        head_sha: 'a1b2c3d',
        base_sha: 'b2c3d4e',
        mergeable: true,
        checks_state: 'SUCCESS',
        checks_summary: [],
      },
      worktree: {
        exists: true,
        path: '/tmp/wt-issue-100',
        branch: 'issue-100',
        head_sha: 'a1b2c3d',
        is_clean: false,
        dirty_files: ['tasks/pending/foo.md'],
      },
      timeline: [
        {
          phase: 'claimed',
          cycle: 1,
          comment_index: 0,
          action: null,
          head_sha: 'a1b2c3d',
          pr_branch: 'issue-100',
          checks_state: null,
          mergeable: null,
          raw_marker: '<!-- iar:event version=1 phase=claimed cycle=1 -->',
        },
        {
          phase: 'draft_pr_created',
          cycle: 2,
          comment_index: 1,
          action: null,
          head_sha: 'a1b2c3d',
          pr_branch: 'issue-100',
          checks_state: null,
          mergeable: null,
          raw_marker: '<!-- iar:event version=1 phase=draft_pr_created cycle=2 -->',
        },
      ],
      latest_event: {
        version: 1,
        phase: 'draft_pr_created',
        cycle: 2,
        head_sha: 'a1b2c3d',
        base_sha: null,
        pr_branch: 'issue-100',
        action: null,
        checks_state: null,
        mergeable: null,
        issue_comments_count: null,
        pr_comments_count: null,
      },
      anomalies: [
        {
          type: 'dirty_worktree_mismatch',
          severity: 'warning',
          message:
            'Worktree has uncommitted changes but Issue is not in running state.',
          suggested_cli: ['iar run --dry-run', 'git status'],
        },
      ],
      suggested_cli_commands: ['iar run --dry-run', 'git status'],
      has_anomaly: true,
      anomaly_types: ['dirty_worktree_mismatch'],
    },
  ],
  anomaly_count: 1,
  anomaly_summary: { warning: 1, error: 0 },
  scanned_at: '2026-05-24T12:00:00+00:00',
}

const SNAPSHOTS_READY: MonitorSnapshotsResponse = {
  repositories: [
    {
      repo_id: 'zata/keda-test',
      scanned_at: '2026-05-24T12:00:00+00:00',
      overview: KEDA_TEST_OVERVIEW,
    },
  ],
  missing_repo_ids: [],
  sync_status: 'ready',
  scanned_at: '2026-05-24T12:00:00+00:00',
  unreachable_repositories: [],
}

test.describe('smoke: agent-runner monitor', () => {
  test('first paint renders local snapshots, last-sync time, anomaly timeline and copyable CLI', async ({
    page,
  }) => {
    const monitor = new AgentRunnerMonitorPage(page)
    await monitor.mockSnapshots(SNAPSHOTS_READY)
    await monitor.goto()

    await monitor.expectHeading()
    await monitor.expectRepositoryVisible('Keda Test')
    await monitor.expectAnomalyCount(1)
    await monitor.expectLastSyncVisible()

    await monitor.openIssue(100)
    await monitor.expectAnomalyCard('dirty_worktree_mismatch')
    await monitor.expectSuggestedCommand('iar run --dry-run')
  })

  test('empty snapshots show the backend sync status and never create a scan job', async ({
    page,
  }) => {
    const monitor = new AgentRunnerMonitorPage(page)
    const scanRequests = monitor.trackScanRequests()
    await monitor.mockSnapshots({
      repositories: [],
      missing_repo_ids: ['zata/keda-test'],
      sync_status: 'pending_first_sync',
      scanned_at: null,
      unreachable_repositories: [],
    })

    await monitor.goto()

    await monitor.expectHeading()
    await monitor.expectEmptyState()
    await page.waitForTimeout(500)
    // 首扫由后端负责：浏览器不得自行创建 overview job。
    expect(scanRequests).toEqual([])
  })

  test('settings panel patches the canonical monitor settings path', async ({
    page,
  }) => {
    const monitor = new AgentRunnerMonitorPage(page)
    await monitor.mockSnapshots(SNAPSHOTS_READY)
    const settings = await monitor.mockMonitorSettings({
      sync_enabled: true,
      sync_interval_seconds: 300,
      updated_at: '',
    })

    await monitor.goto()
    await monitor.openSettings()
    await monitor.selectInterval(15)

    await expect.poll(() => settings.patchUrls.length).toBe(1)
    expect(settings.patchUrls[0]).toMatch(
      /\/api\/v1\/agent-runner\/console\/monitor\/settings$/,
    )
    expect(settings.patchBodies[0]).toEqual({
      sync_enabled: true,
      sync_interval_seconds: 900,
    })
    await monitor.expectSettingsSaved()
    await monitor.expectSelectedInterval(15)
  })
})
