# PR-Agent wrapper

A reusable GitHub Actions workflow that reviews pull requests with [PR-Agent](https://github.com/The-PR-Agent/pr-agent) and an LLM you choose. It keeps the LLM key, the GitHub write token, and the PR's code in three separate jobs, so no single job can both read your key and write to your repository.

On each push to a PR it sends the diff to the model and writes the review as one PR comment, updated in place. It never approves, requests changes, labels, merges, or touches anyone else's comments.

## Should you use this or PR-Agent's own action

PR-Agent's action is simpler and does more: it reads the repository, answers comment commands, and can describe or improve a PR. Use it if that is what you want.

This wrapper trades those features for a smaller blast radius:

- No job holds both an LLM key and a GitHub token. A prompt injection in a diff can waste a review. It cannot push, comment, or read the repository.
- The PR's code is never checked out or run, and PR-Agent ignores configuration files in the repository, so a PR cannot change how it is reviewed.
- PR-Agent runs from a pinned commit with verified file hashes, in a container with no root, no writable filesystem, and no capabilities.

The cost is context. The model sees the diff with three lines around each change, so it will miss problems that need the rest of the codebase. It also skips PRs from forks.

## Set it up

1. **Add your provider's API key as an Actions secret** in the repository that will be reviewed (Settings, then Secrets and variables, then Actions). The secret name depends on the provider; see [Models and providers](#models-and-providers). For xAI:

   ```sh
   gh secret set XAI_API_KEY
   ```

2. **Pick a commit of this repository to pin.** Callers pin a full commit SHA, not a branch, so the workflow cannot change under you:

   ```sh
   gh api repos/tonyhuynh/pr-agent-wrapper/commits/main --jq .sha
   ```

3. **Add `.github/workflows/pr-agent-review.yml`** to your repository with the SHA in place of `<MAIN_SHA>`. This is [examples/caller.yml](examples/caller.yml):

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

4. **Merge that file to your default branch, then open a PR.** Three jobs run: `capture`, `review`, and `publish`. When they finish, a comment from `github-actions[bot]` appears on the PR. Later pushes update the same comment.

What the pieces of the caller do:

- `pull-requests: write` lets the `publish` job write the comment. Nothing else gets that permission.
- `secrets:` passes only the keys your models need. Do not use `secrets: inherit`.
- `concurrency` cancels a running review when a new push arrives, so you pay for one review per burst of pushes.

Do not make the review a required check. A provider outage would then block every merge.

### Which trigger

The workflow accepts both PR triggers. It runs on opened, reopened, synchronize, and ready-for-review, and skips drafts, forks, and closed PRs under either one.

| Trigger | Caller file is read from | Choose it when |
| --- | --- | --- |
| `pull_request_target` | Your default branch | You want the review settings fixed by the default branch, so a PR cannot edit its own reviewer. This is the recommended setup. |
| `pull_request` | The PR's own branch | You want to try the workflow in the PR that adds it, before anything is merged. |

Under `pull_request`, GitHub withholds secrets from Dependabot PRs, so those runs fail with a missing-key error.

## Models and providers

`model` is required and has the form `<provider>/<model>`, where the model part is the provider's own model ID. There is no default, because a default model goes stale and then breaks every caller pinned to it.

| Provider prefix | Secret to pass | Example |
| --- | --- | --- |
| `xai/` | `XAI_API_KEY` | `xai/grok-4.7` |
| `openai/` | `OPENAI_API_KEY` | `openai/gpt-6.1-sol` |
| `anthropic/` | `ANTHROPIC_API_KEY` | `anthropic/claude-sonnet-5-5` |
| `gemini/` | `GEMINI_API_KEY` | `gemini/gemini-2.5-pro` |
| `openrouter/` | `OPENROUTER_API_KEY` | `openrouter/x-ai/grok-4.6` |
| `groq/` | `GROQ_API_KEY` | `groq/openai/gpt-oss-120b` |
| `deepseek/` | `DEEPSEEK_API_KEY` | `deepseek/deepseek-chat` |
| `mistral/` | `MISTRAL_API_KEY` | `mistral/mistral-large-latest` |

The model part is the ID your provider uses in its own API, so look it up in the provider's model list. The examples above are IDs the pinned PR-Agent knows.

PR-Agent also has to know the model's context size. It reads that from the version of LiteLLM it pins, and LiteLLM publishes its list at [models.litellm.ai](https://models.litellm.ai). That page shows the newest list, so a recent model can appear there and still be unknown here. For a model the pinned version does not know, set `custom_model_max_tokens` to its context size.

Bedrock, Vertex AI, and Azure are not supported yet. They need more than one credential each.

### Fallback models

`fallback_models` lists up to four more models, tried in order when the one before fails with an error PR-Agent treats as retryable. It is a JSON array written as a string, and every provider in the chain needs its secret:

```yaml
    with:
      model: anthropic/claude-sonnet-5-5
      fallback_models: '["openai/gpt-6.1-sol"]'
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

The footer of the review names the model that answered and says when it was a fallback.

### Other OpenAI-compatible services

Any service that speaks the OpenAI API, including one you host, works through `api_base`. Use the `openai/` prefix and put the service's key in `OPENAI_API_KEY`:

```yaml
    with:
      model: openai/my-model
      api_base: https://llm.example.com/v1
      custom_model_max_tokens: 128000
    secrets:
      OPENAI_API_KEY: ${{ secrets.MY_LLM_KEY }}
```

`custom_model_max_tokens` tells PR-Agent the model's context size. You need it whenever the pinned PR-Agent does not know the model, which includes models released after the pinned version. Without it, the review fails with `inference_failed`.

## Inputs

Only `model` is required.

| Input | Default | What it does and its limits |
| --- | --- | --- |
| `model` | Required | The model to use, as `<provider>/<model>` |
| `fallback_models` | `'[]'` | JSON array string of up to 4 more models; no duplicates, and not the primary |
| `extra_instructions` | Empty | Text added to the review prompt, up to 8 KiB |
| `num_max_findings` | `3` | Most findings to report, 1 to 10 |
| `ignore_globs` | `'["vendor/**"]'` | JSON array string of paths to leave out of the review; up to 32 patterns of 256 bytes each. `vendor/**` always stays excluded. The review prompt names these patterns so the model does not report the hidden files as missing |
| `comment_mode` | `update` | `update` edits this workflow's previous comment; `new` adds a comment per run |
| `response_language` | `en-US` | Language of the review, as a locale such as `pt-BR`. PR-Agent also translates the comment's fixed labels for the languages it ships, currently Spanish |
| `reasoning_effort` | Empty | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`; empty keeps PR-Agent's default |
| `max_model_tokens` | `64000` | Cap on tokens PR-Agent sends to the model, 4000 to 2000000. PR-Agent's own default is 32000 |
| `ai_timeout` | `120` | Seconds allowed per model call, 30 to 540 |
| `large_pr_chunking` | `false` | When a diff does not fit `max_model_tokens`, split it and review it in up to 3 model calls instead of leaving files out |
| `api_base` | Empty | HTTPS URL of an OpenAI-compatible endpoint, on a public host name. IP addresses and local names are rejected. Needs an `openai/` model |
| `custom_model_max_tokens` | `0` | Context size for a model PR-Agent does not know, 1000 to 2000000; 0 leaves it unset |

Text inputs cannot start with an at sign or contain a Dynaconf directive such as `@json` or `@format`, because PR-Agent's settings loader would evaluate it. Mentions like `@Override` or `@types/node` inside a sentence are fine.

## What to expect on a PR

- **One comment.** It starts with the repository, PR number, base SHA, head SHA, and a hash of the diff that was reviewed, followed by PR-Agent's review. In `update` mode the workflow edits its own latest comment, which it recognizes by a hidden marker and the `github-actions[bot]` author. In `new` mode every run, including a rerun, adds a comment.
- **Pushes during a review.** If the PR moved while a review was running, the comment says so and still names the commits it covers. If a newer run already reviewed the newer head, the older run writes nothing.
- **Failures stay off the PR.** A failed review fails the job and writes the reason to the job summary. The last good review stays in place.
- **A run takes about two minutes.** Each run builds PR-Agent from source, which is about one minute of that.
- **Large PRs can outgrow the token budget.** PR-Agent then leaves some files out and lists them at the end of the comment. Raise `max_model_tokens` to cover them. Each review of a large PR then costs more and takes longer, so you may need a higher `ai_timeout` too. For a diff that outgrows even that, or a model with a small context, set `large_pr_chunking: true`. PR-Agent then reviews the diff in up to 3 slices and merges the results. That costs up to 3 calls, and an issue that spans two slices can be missed.
- **Your provider sees the diff.** The diff and review are also kept as workflow artifacts for 3 days. See [SECURITY.md](SECURITY.md) before using this on code that must not leave the repository.

### Example comment

A made-up review of a one-line change, in the format the workflow posts:

```text
PR-Agent captured comparison

Repository: example/calculator; PR: #12
Base: 1f0c...
Head: 9ab4...
Merge base: 1f0c...
Diff SHA256: 5d2e...

PR Reviewer Guide

Estimated effort to review: 1
No relevant tests
No security concerns identified
Recommended focus areas for review

  Wrong divisor
  average() now divides by len(values) - 1, so it returns 3.0 for
  [1, 2, 3] and raises ZeroDivisionError for a single value.
  Divide by len(values).

Agent run details
- Model: openai/gpt-6.1-sol
- Tokens: 1,240 in / 310 out / 1,550 total
- Time cost: 6.2s
- AI calls: 1
```

The real comment prints full SHAs and renders as Markdown, with PR-Agent's table layout and icons.

## When something goes wrong

Open the failed run. Errors appear as an annotation titled "PR-Agent wrapper", and the `publish` job's summary repeats the review status.

| What you see | Cause | Fix |
| --- | --- | --- |
| All three jobs skipped | The PR is a draft, comes from a fork, or the event is not one of the four supported actions | Mark the PR ready for review. Fork PRs are not reviewed |
| ``Invalid input `name`: ...`` in `capture` | An input broke the rule named in the message. The value is never printed | Fix that input; the [Inputs](#inputs) table has the limits |
| `Review failed: key_validation_failed` | A provider in your chain has no key, or the caller did not pass it under `secrets:`. The `review` job log names it: `Missing required workflow secret: NAME` | Add the secret to the repository and pass it in the caller |
| `Review failed: inference_failed` | PR-Agent could not produce a review. When PR-Agent recognizes the cause (rejected key, no credits, rate limit, timeout, context limit, unreachable provider), the message ends with it | Follow the reason. With no reason, check the model ID and key with the provider. For a new or self-hosted model, set `custom_model_max_tokens` |
| `Review failed: empty_output` | PR-Agent wrote no review | Rerun. If it repeats, check that `ignore_globs` does not exclude every changed file |
| `Review failed: oversized_output` | The review is larger than a GitHub comment allows | Lower `num_max_findings`. The full output is in the run's artifact |
| `Review failed: missing_model_details` | The review lacks the footer that names the model, so its origin cannot be confirmed | Rerun. The output is in the run's artifact |
| `Ambiguous or missing merge base` in `capture` | The branch history has more than one merge base with the target | Rebase or merge the target branch into the PR |
| `Diff exceeds 4 MiB` in `capture` | The PR is too large to review | Split the PR. `ignore_globs` applies later and does not lower this size |

The reason after `inference_failed` is one of a few fixed sentences. Provider error text can contain credentials, so the workflow never logs it.

## How it works

Each job gets only what it needs:

| Job | Has | Does |
| --- | --- | --- |
| `capture` | Read-only GitHub token | Fetches the event's base and head commits into a bare repository, makes the diff, validates inputs. Runs no PR code |
| `review` | Provider keys, no GitHub token | Builds PR-Agent from the pinned commit without credentials, then runs it on the diff in a container with no root, a read-only filesystem, and no capabilities |
| `publish` | Write token, no provider keys | Validates the result against the captured hashes, rechecks the PR, and writes the comment |

The Python that does this is embedded in [.github/workflows/review.yml](.github/workflows/review.yml), because a reusable workflow cannot read other files from its own repository without a checkout. The tests extract and run that same code.

[SECURITY.md](SECURITY.md) lists what this design does not protect against.

## Updating and rolling back

You take a change only by moving the SHA in your caller. [CHANGELOG.md](CHANGELOG.md) lists what each change brings, with breaking changes first. To roll back, point the caller at an earlier SHA or delete the job.

## Developing this repository

| Path | What it holds |
| --- | --- |
| `.github/workflows/review.yml` | The reusable workflow and its two embedded Python programs |
| `.github/workflows/test.yml` | CI: lint, secret scans, and tests, all without provider keys |
| `examples/caller.yml` | The caller shown above |
| `scripts/` | Loader for the embedded Python, tree and secret checks, upstream fetch and repin |
| `tests/` | Wrapper tests, and tests that run pinned PR-Agent code with stubbed providers |

The checks need Python 3.11 or newer and Go (for `actionlint` and `gitleaks`). They mirror [.github/workflows/test.yml](.github/workflows/test.yml):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
go install github.com/zricethezav/gitleaks/v8@v8.30.1
"$(go env GOPATH)/bin/actionlint"
.venv/bin/yamllint -d '{extends: relaxed, rules: {line-length: disable, truthy: disable}}' .github/workflows examples
.venv/bin/python scripts/check_tree.py
.venv/bin/python scripts/scan_public.py
"$(go env GOPATH)/bin/gitleaks" git --redact --no-banner .
.venv/bin/python scripts/fetch_upstream.py
.venv/bin/python -m unittest discover -s tests -v
```

`fetch_upstream.py` downloads a dozen pinned PR-Agent source files into `.test-upstream/` and verifies their hashes; the tests need them. To use files you already have, set `PRW_UPSTREAM_DIR` to their directory. The tests do not call a provider or publish to GitHub.

The review job builds PR-Agent v0.50.0 from commit `e673a02076ce09a4726c3d0c3cc85780e59ca52b`. To move the pin, run `.venv/bin/python scripts/bump_upstream.py <tag>` with the new release tag, read the upstream diff it links, delete `.test-upstream/`, and run the checks again. Dependabot proposes updates to the pinned actions.

[CONTRIBUTING.md](CONTRIBUTING.md) covers what a pull request needs.

## License

[MIT](LICENSE). PR-Agent and its dependencies keep their own licenses.
