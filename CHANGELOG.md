# Changelog

Callers pin a full commit SHA, so every entry here is a change you opt into by moving that SHA.

## Unreleased

### Breaking

- `model` is now required. The workflow no longer defaults to `xai/grok-4.7`; add `model: xai/grok-4.7` to keep the old behavior.
- Reviews update one comment in place instead of adding a comment per run. Set `comment_mode: new` for the old behavior.
- Failed runs no longer comment on the PR. The job fails and the reason goes to the job summary.

### Added

- Providers: `openrouter/`, `groq/`, `deepseek/`, and `mistral/`, each with its own secret.
- `api_base` for OpenAI-compatible endpoints.
- Tuning inputs: `custom_model_max_tokens`, `max_model_tokens`, `reasoning_effort`, `response_language`, `ai_timeout`.
- Input errors name the input that failed and why, as an annotation on the run.
- `scripts/bump_upstream.py` repins PR-Agent, and Dependabot tracks the action pins.

### Changed

- Inputs may mention names such as `@Override` or `@types/node`. Only text that starts with an at sign, or that contains a Dynaconf directive such as `@json`, is rejected.
- The example caller cancels a running review when a new push arrives.
