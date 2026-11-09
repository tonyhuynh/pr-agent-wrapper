# PR-Agent wrapper

A reusable GitHub Actions workflow that builds PR-Agent v0.49.0 from source, runs one review per eligible PR event, and posts it as a new PR comment. It only posts comments. It never approves, requests changes, edits old comments, adds labels, or merges. Do not make it a required check.

## Use it

Add this caller workflow and an `XAI_API_KEY` Actions secret. Replace `<MAIN_SHA>` with a full commit SHA for this wrapper. The YAML matches [examples/caller.yml](examples/caller.yml).

```yaml
name: PR-Agent review
on:
  pull_request_target:
    types: [opened, reopened, synchronize, ready_for_review]
permissions:
  contents: read
jobs:
  review:
    permissions:
      contents: read
      pull-requests: write
    uses: tonyhuynh/pr-agent-wrapper/.github/workflows/review.yml@<MAIN_SHA>
    with:
      extra_instructions: Focus on the workflow, package.json and docs.
    secrets:
      XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
```

The caller job needs `pull-requests: write`. Pass secrets explicitly; never use `secrets: inherit`. The default uses only `XAI_API_KEY`.

The workflow accepts `pull_request_target` and `pull_request` events for opened, reopened, synchronize, and ready-for-review actions. It skips forks, drafts, closed PRs, and comment commands. The publish job rechecks PR state before posting. The workflow never runs PR code with secrets.

## Inputs and secrets

| Input | Default | Limits |
| --- | --- | --- |
| `model` | `xai/grok-4.7` | Prefix: `xai/`, `openai/`, `anthropic/`, or `gemini/` |
| `fallback_models` | `'[]'` | JSON array string; up to 4 models, no duplicates or repeated primary |
| `extra_instructions` | Empty | Up to 8 KiB of UTF-8 text |
| `num_max_findings` | `3` | Integer from 1 to 10 |
| `ignore_globs` | `'["vendor/**"]'` | JSON array string; up to 32 distinct patterns, 256 bytes each |

The wrapper always keeps `vendor/**` excluded. Model IDs after the provider prefix must start with a letter or digit and contain up to 128 ASCII letters, digits, dots, underscores, or hyphens. The wrapper rejects any input with an at sign followed by an ASCII letter, which blocks Dynaconf directives like `@json`.

| Provider prefix | Workflow secret |
| --- | --- |
| `xai/` | `XAI_API_KEY` |
| `openai/` | `OPENAI_API_KEY` |
| `anthropic/` | `ANTHROPIC_API_KEY` |
| `gemini/` | `GEMINI_API_KEY` |

All four secrets are optional at the workflow level. Every provider in your configured chain needs its key. A missing key fails before the container starts, with a message naming the secret.

Fallback runs only if you configure it in `fallback_models`. PR-Agent tries those models in order after eligible failures; primary success ends the review. Some errors stop the chain. The review footer names the successful configured model and marks fallback use.

## How it runs

- `capture` uses a read token to fetch the event's fixed base and head SHAs into a bare repository and make the diff. It runs no PR code.
- `review` builds the pinned source without credentials, then runs PR-Agent in a nonroot container with a read-only root filesystem, dropped capabilities, and resource limits. Only the run step receives provider keys. The job has no GitHub token.
- `publish` uses a separate write token and no provider keys. It validates the result and posts one new comment stamped with the captured base SHA, head SHA, and diff hash.

A rerun can add another comment. If the PR advances, the comment still identifies the captured diff. Failed reviews, empty or oversized output, and output missing the model footer get a status comment instead of a review. The workflow withholds output containing a supplied provider secret. A missing or invalid artifact stops the publish job without a comment.

The review job builds PR-Agent v0.49.0 from commit `efdde4afe14aa401a3889012c7f3e9ef3081a430`. Action pins are `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683`, `actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02`, and `actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093`. Review changes before updating the caller's SHA. See [SECURITY.md](SECURITY.md) for risks and reporting.

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

To roll back, point the caller at an earlier full commit SHA or remove the job. Earlier comments remain. This wrapper uses the [MIT license](LICENSE); dependencies retain their own licenses.
