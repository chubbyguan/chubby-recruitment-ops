# Execution profiles

Choose one profile before any connector call. A run cannot change profiles after
it starts.

## `quick`

Use by default for an interactive request about one candidate, one interview,
one job campaign, one publication, one decision notification, the next Gmail or
Feishu Hire watermark batch, or an ad-hoc recommendation/digest of data that is
already synchronized.

- Targeted candidate/interview/metric/notification runs: 120-second budget.
- Gmail or Feishu Hire watermark batch: 180-second budget; Gmail is limited to
  at most five messages.
- Ad-hoc digest: use the current synchronized snapshot; do not refresh every
  upstream source first.
- Campaign and social content preparation remain manual-publication only.
- Verify only identities and permissions required by that target.
- Use cached Base schemas for 24 hours unless a field/version error proves the
  cache stale.
- Batch writes, perform one readback boundary, and retry a transient connector
  failure at most once.
- Do not inspect or edit the plugin repository, run the package test suite, or
  push GitHub.
- If a code defect is discovered, finish or safely stop the business item,
  record a maintenance backlog entry, and report it separately.

## `scheduled`

Use only for timer-triggered incremental cycles.

- Eight-minute budget.
- Read from saved watermarks instead of scanning all historical data.
- Synchronize Feishu Hire when enabled, process Gmail in bounded batches, run
  recommendations only for changed candidates, and reconcile interviews at
  configured hours.
- Generate the redacted digest only at the configured digest hour.
- Isolate record-level failures and continue independent work.
- Never edit the repository, reinstall the plugin, run package tests, or push
  GitHub.

## `maintenance`

Use only when the user explicitly asks to modify the plugin, documentation,
tests, installation, or GitHub release.

- Repository-only scope.
- Do not read Gmail, change candidate Base records, send candidate
  notifications, or advance production watermarks.
- Run the required package checks and release gates.
- Git commit/push is allowed only when the user requested repository sync or
  release.

## Hard routing examples

| User request | Profile |
|---|---|
| “补充丁雪的面试记录” | `quick/interview` |
| “看看邮箱有没有新简历” | `quick/inbox` |
| “把这个岗位生成小红书招聘内容” | `quick/campaign` |
| “同步下一批飞书招聘更新” | `quick/hire_sync` |
| “推荐高分但尚未面试的人” | `quick/recommendation` |
| “重新发这位候选人的完整通知” | `quick/notification` |
| Timer wakes at 11:00, 17:00, or 19:00 | `scheduled/cycle` |
| “优化工作流并同步 GitHub” | `maintenance/release` |

If one user message contains both production processing and plugin
maintenance, complete the time-sensitive production item under `quick`, then
start a separate `maintenance` run. Do not merge the two execution plans.
