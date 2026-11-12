# PR-Agent wrapper

A reusable GitHub Actions workflow that reviews pull requests with [PR-Agent](https://github.com/The-PR-Agent/pr-agent) and keeps the LLM key, the GitHub write token, and the PR's code in three separate jobs.

It builds PR-Agent v0.49.0 from pinned source, sends the model the PR diff and nothing else, and posts the review as one PR comment. It never approves, requests changes, labels, merges, or touches anyone else's comments. Do not make it a required check.

## Why not run PR-Agent's own action

PR-Agent's action is simpler and does more: it reads the repository, answers comment commands, and can describe or improve a PR. Use it if that is what you want.

This wrapper trades those features for a smaller blast radius:

- No job holds both an LLM key and a GitHub token. A prompt injection in a diff can waste a review. It cannot push, comment, or read the repository.
- The PR's code is never checked out or run, and PR-Agent ignores configuration files in the repository, so a PR cannot change how it is reviewed.
- PR-Agent runs from a pinned commit with verified file hashes, in a container with no root, no writable filesystem, and no capabilities.

The cost is context. The model sees the diff with three lines around each change, so it will miss problems that need the rest of the codebase.

## Use it

Add this caller workflow and an Actions secret for your provider. Replace `<MAIN_SHA>` with a full commit SHA of this repository. The YAML matches [examples/caller.yml](examples/caller.yml).

```yaml
name: PR-Agent review
on:
  pull_request_target:
    types: [opened, reopened, synchronize, ready_for_review]
permissions:
  contents: read
concurrency:
  group: pr-agent-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  review:
    permissions:
      contents: read
      pull-requests: write
    uses: tonyhuynh/pr-agent-wrapper/.github/workflows/review.yml@<MAIN_SHA>
    with:
      model: xai/grok-4.7
      extra_instructions: Focus on the workflow, package.json and docs.
    secrets:
      XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
```

The caller job needs `pull-requests: write`. Pass only the secrets your models need; never use `secrets: inherit`. The `concurrency` block cancels a running review when a new push arrives, so you pay for one review per burst of pushes.

The workflow accepts `pull_request_target` and `pull_request` events for opened, reopened, synchronize, and ready-for-review actions. It skips forks, drafts, closed PRs, and comment commands.

## Models and providers

`model` is required and has the form `<provider>/<model>`. There is no default, because a default model goes stale and then breaks every caller pinned to it.

| Provider prefix | Workflow secret | Example |
| --- | --- | --- |
| `xai/` | `XAI_API_KEY` | `xai/grok-4.7` |
| `openai/` | `OPENAI_API_KEY` | `openai/gpt-6.1-sol` |
| `anthropic/` | `ANTHROPIC_API_KEY` | `anthropic/claude-sonnet-5-5` |
| `gemini/` | `GEMINI_API_KEY` | `gemini/<model>` |
| `openrouter/` | `OPENROUTER_API_KEY` | `openrouter/x-ai/grok-4.6` |
| `groq/` | `GROQ_API_KEY` | `groq/<model>` |
| `deepseek/` | `DEEPSEEK_API_KEY` | `deepseek/<model>` |
| `mistral/` | `MISTRAL_API_KEY` | `mistral/<model>` |

The names after the prefix are the provider's own model IDs. Every provider in your chain needs its key. A missing key fails before the container starts, with a message naming the secret.

Any other OpenAI-compatible service, including a self-hosted one, works through `api_base`: set `model: openai/<its-model-name>`, `api_base: https://your-endpoint/v1`, and put its key in `OPENAI_API_KEY`. PR-Agent has to know the model's context size. If the pinned version does not, the review fails, and you fix it by setting `custom_model_max_tokens`. The same applies to a model released after the pinned PR-Agent version.

Bedrock, Vertex AI, and Azure are not supported yet. They need more than one credential each.

`fallback_models` is a JSON array of up to four more models, tried in order when the one before fails with an error PR-Agent treats as retryable. The review footer names the model that answered.

## Inputs

| Input | Default | Limits |
| --- | --- | --- |
| `model` | Required | See above |
| `fallback_models` | `'[]'` | JSON array string; up to 4 models, no duplicates or repeated primary |
| `extra_instructions` | Empty | Up to 8 KiB of text, added to the review prompt |
| `num_max_findings` | `3` | Integer from 1 to 10 |
| `ignore_globs` | `'["vendor/**"]'` | JSON array string; up to 32 patterns, 256 bytes each. `vendor/**` always stays excluded |
| `comment_mode` | `update` | `update` edits this workflow's previous comment; `new` adds a comment per run |
| `api_base` | Empty | HTTPS URL for an OpenAI-compatible endpoint. Needs an `openai/` model |
| `custom_model_max_tokens` | `0` | Context size for a model PR-Agent does not know; 1000 to 2000000, or 0 to leave unset |
| `max_model_tokens` | `32000` | Cap on tokens PR-Agent sends to the model, 4000 to 2000000 |
| `reasoning_effort` | Empty | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`; empty keeps PR-Agent's default |
| `response_language` | `en-US` | A locale such as `pt-BR` |
| `ai_timeout` | `120` | Seconds per model call, 30 to 540 |

Text inputs cannot start with an at sign or contain a Dynaconf directive such as `@json` or `@format`, because PR-Agent's settings loader would evaluate it. Mentions like `@Override` or `@types/node` inside a sentence are fine.

An invalid input fails the `capture` job with an annotation that names the input and the rule it broke. The message never repeats the value.

## How it runs

- `capture` uses a read token to fetch the event's base and head commits into a bare repository and make the diff. It runs no PR code.
- `review` builds the pinned source without credentials, then runs PR-Agent in the locked-down container. Only the run step receives provider keys. The job has no GitHub token.
- `publish` uses a write token and no provider keys. It validates the result, rechecks the PR, and writes one comment stamped with the captured base SHA, head SHA, and diff hash.

In `update` mode the workflow edits its own latest comment, which it recognizes by a hidden marker and the `github-actions[bot]` author. If the PR head moved after capture, it writes nothing and leaves the comment to the newer run. In `new` mode every run adds a comment, and a rerun adds another.

When a review fails, the job fails and the reason goes to the job summary. Nothing is posted and the last good review stays in place. The statuses are `key_validation_failed`, `inference_failed`, `empty_output`, `oversized_output`, and `missing_model_details`. Provider error text is never logged, since it can contain credentials, so `inference_failed` covers a wrong model name, an unknown context size, a rejected key, and a timeout alike. The workflow withholds any output that contains a provider key.

Each run builds the PR-Agent image from source, so the build adds to every run's time.

## Updating

The review job builds PR-Agent v0.49.0 from commit `efdde4afe14aa401a3889012c7f3e9ef3081a430`. To move it, run `python scripts/bump_upstream.py v0.50.0`, read the upstream diff it links, and run the checks below. Dependabot proposes updates to the pinned actions.

Callers choose when to take a change by moving their SHA; [CHANGELOG.md](CHANGELOG.md) lists what each move brings. To roll back, point the caller at an earlier SHA or remove the job. See [SECURITY.md](SECURITY.md) for risks and reporting.

## Local checks

Run the same checks as [.github/workflows/test.yml](.github/workflows/test.yml), without provider keys:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
go install github.com/zricethezav/gitleaks/v8@v8.24.3
"$(go env GOPATH)/bin/actionlint"
.venv/bin/yamllint -d '{extends: relaxed, rules: {line-length: disable, truthy: disable}}' .github/workflows examples
.venv/bin/python scripts/check_tree.py
.venv/bin/python scripts/scan_public.py
"$(go env GOPATH)/bin/gitleaks" git --redact --no-banner .
.venv/bin/python scripts/fetch_upstream.py
.venv/bin/python -m unittest discover -s tests -v
```

The fetch script verifies pinned source files. For offline tests, supply those files in a fixture directory and set `PRW_UPSTREAM_DIR`, for example `PRW_UPSTREAM_DIR=.test-upstream .venv/bin/python -m unittest discover -s tests -v`. Tests cover wrapper behavior and stubbed upstream calls. They do not cover live provider calls or real GitHub publishing.

This wrapper uses the [MIT license](LICENSE); dependencies retain their own licenses.
