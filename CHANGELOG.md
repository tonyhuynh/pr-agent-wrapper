# Changelog

Callers pin a full commit SHA, so every entry here is a change you opt into by moving that SHA. Breaking changes come first in each entry.

## 0.2.0 (unreleased)

- A failed review now adds a short notice to the PR comment: the commit, a fixed reason, and a link to the run. The last good review stays below it. Before, a failure posted nothing.
- Dependency lockfiles are never reviewed: `package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml`, and `go.sum` join `vendor/**` as paths that are always left out. You can drop them from `ignore_globs`.
- `ai_timeout` defaults to 300 seconds, up from 120. A stalled provider now holds a run for up to five minutes; set `ai_timeout: 120` to keep the old limit.
- "Re-run failed jobs" works. It failed before because the rerun could not find the captured diff.
- The `capture` job's summary shows the size of the diff, its largest files, and what `ignore_globs` leaves out.
- The timeout error names the settings to change.

## 0.1.0 (2026-11-13)

Initial public release. Pins PR-Agent v0.50.0.
