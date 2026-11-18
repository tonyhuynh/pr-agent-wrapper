# PR-Agent wrapper

A reusable GitHub Actions workflow that reviews pull requests with [PR-Agent](https://github.com/The-PR-Agent/pr-agent) and an LLM you choose. The LLM key and the GitHub write token live in separate jobs, so no job can both read your key and write to your repository.

On each push to a PR, it sends the diff to the model and posts the review as one PR comment, updated in place. It never approves, requests changes, labels, merges, or touches anyone else's comments.

## Should you use this or PR-Agent's own GitHub Action

The PR-Agent project publishes [its own GitHub Action](https://github.com/The-PR-Agent/pr-agent/blob/main/docs/docs/installation/github.md), which runs everything in one job. It is simpler to set up and does more. It shows the model the changed files in full along with the PR's title and description, answers commands in PR comments, and can write PR descriptions and code suggestions. Choose it when you need any of that.

This wrapper gives up those features to limit what a hostile PR can do:

- No job holds both an LLM key and a GitHub token. A prompt injection in a diff can spoil that PR's review comment. It cannot push, approve, or read the rest of the repository.
- The PR's code is never checked out or run, and PR-Agent ignores configuration files in the repository, so a PR cannot change how it is reviewed.
- PR-Agent runs from a pinned commit with verified file hashes, in a container with no root, no writable filesystem, and no capabilities.

The cost is context. The model sees only the diff, with three lines around each change. It does not see the rest of each file or the PR's title and description, so it can miss problems that depend on either. PRs from forks are not reviewed.

## Set it up

1. **Add your provider's API key as an Actions secret** in the repository that will be reviewed (Settings, then Secrets and variables, then Actions). The secret name depends on the provider; see [Models and providers](#models-and-providers). For xAI:

   ```sh
   gh secret set XAI_API_KEY
   ```

2. **Get the commit SHA to pin.** A caller pins a full commit SHA, not a branch, so the workflow cannot change under you:

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
         extra_instructions: Focus on correctness and security. Skip style comments.
       secrets:
         XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
   ```

4. **Merge that file to your default branch, then open a PR.** Three jobs run: `capture`, `review`, and `publish`. When they finish, a comment from `github-actions[bot]` appears on the PR. Later pushes update the same comment.

In that file:

- `pull-requests: write` lets the `publish` job write the comment. No other job gets that permission.
- `secrets:` passes only the keys your models need. Do not use `secrets: inherit`.
- `concurrency` cancels a running review when a new push arrives, so you pay for one review per burst of pushes.

Do not make the review a required check. A provider outage would then block every merge.

### Which trigger

The workflow runs under either PR trigger when a PR is opened, reopened, pushed to, or marked ready for review. It skips drafts, forks, and closed PRs.

| Trigger | Caller file is read from | Choose it when |
| --- | --- | --- |
| `pull_request_target` | Your default branch | You want the review settings fixed by the default branch, so a PR cannot edit its own reviewer. This is the recommended setup. |
| `pull_request` | The PR's own branch | You want to try the workflow in the PR that adds it, before anything is merged. |

Under `pull_request`, GitHub withholds secrets from Dependabot PRs, so those runs fail with a missing-key error.

## Models and providers

`model` is required and has the form `<provider>/<model>`. The model part is the ID the provider uses in its own API, so look it up in the provider's model list. There is no default, because a default model goes stale and then breaks every caller pinned to it.

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

PR-Agent also has to know the model's context size. It reads that from the version of LiteLLM it pins, which knows every example above. LiteLLM publishes its list at [models.litellm.ai](https://models.litellm.ai), but that page shows the newest list, so a recent model can appear there and still be unknown here. For a model the pinned version does not know, set `custom_model_max_tokens` to its context size. Without it, the review fails with `inference_failed`.

Bedrock, Vertex AI, and Azure are not supported yet, because each needs more than one credential.

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

Set `custom_model_max_tokens` too, because PR-Agent cannot know the context size of a model you name yourself.

## Inputs

Only `model` is required.

| Input | Default | What it does and its limits |
| --- | --- | --- |
| `model` | Required | The model to use, as `<provider>/<model>` |
| `fallback_models` | `'[]'` | JSON array string of up to 4 more models; no duplicates, and not the primary |
| `extra_instructions` | Empty | Text added to the review prompt, up to 8 KiB |
| `num_max_findings` | `3` | Most findings to report, 1 to 10 |
| `ignore_globs` | `'["vendor/**"]'` | JSON array string of paths to leave out of the review; up to 32 patterns of 256 bytes each. `vendor/**` and dependency lockfiles (`package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml`, `go.sum`, and any `.lock` file) are always left out. The prompt tells the model these files were left out, so it does not report them as missing |
| `comment_mode` | `update` | `update` edits this workflow's previous comment; `new` adds a comment per run |
| `response_language` | `en-US` | Language of the review, as a locale such as `pt-BR`. PR-Agent also translates the comment's fixed labels for the languages it ships, currently Spanish |
| `reasoning_effort` | Empty | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`; empty keeps PR-Agent's default |
| `max_model_tokens` | `64000` | Cap on tokens PR-Agent sends to the model, 4000 to 2000000. PR-Agent's own default is 32000 |
| `ai_timeout` | `300` | Seconds allowed per model call, 30 to 540 |
| `large_pr_chunking` | `false` | When a diff does not fit `max_model_tokens`, split it and review it in up to 3 model calls instead of leaving files out |
| `api_base` | Empty | HTTPS URL of an OpenAI-compatible endpoint, on a public host name. IP addresses and local names are rejected. Needs an `openai/` model |
| `custom_model_max_tokens` | `0` | Context size for a model PR-Agent does not know, 1000 to 2000000; 0 leaves it unset |

`ignore_globs` patterns match the file's path from the repository root, the way `fnmatch` does: `*` also crosses `/`, and a `**/` segment matches any number of directories, including none. So `package-lock.json` matches only the root file, `**/package-lock.json` matches it at any depth, and `goals/**/evidence/**` matches `goals/evidence/a.png` and `goals/x/evidence/a.png`. PR-Agent applies the patterns after the diff is captured, so the run's artifact always holds the full diff; the `capture` job's summary shows the size that is left.

Text inputs cannot start with an at sign or contain a Dynaconf directive such as `@json` or `@format`, because PR-Agent's settings loader would evaluate it. Mentions like `@Override` or `@types/node` inside a sentence are fine.

## What to expect on a PR

- **One comment.** It starts with the repository, PR number, base SHA, head SHA, and a hash of the diff that was reviewed, followed by PR-Agent's review. In `update` mode the workflow edits its own latest comment, which it recognizes by a hidden marker and the `github-actions[bot]` author. In `new` mode every run, including a rerun, adds a comment.
- **Pushes during a review.** If the PR moved while a review was running, the comment says so and still names the commits it covers. If a newer run already reviewed the newer head, the older run writes nothing.
- **A failed review leaves a short notice.** The job fails, and the comment gets a few lines at the top that name the commit, give the reason in fixed wording, and link the run. The last good review stays below the notice, and the next good review replaces both. A failed run for a commit the PR has already moved past posts nothing.
- **A run takes about two minutes.** Each run builds PR-Agent from source, which is about one minute of that.
- **Large PRs can outgrow the token budget.** PR-Agent then leaves some files out and lists them at the end of the comment. First check the `capture` job's summary, which lists the largest files in the diff and what `ignore_globs` already leaves out, and leave generated or data files out with `ignore_globs`. To cover what remains, raise `max_model_tokens`. The review then costs more and takes longer, so it may need a higher `ai_timeout` too. If the diff still does not fit, or the model's context is small, set `large_pr_chunking: true`. PR-Agent then reviews the diff in up to 3 model calls and merges the results. It can miss an issue that spans two of them.
- **A slow review is usually a slow model, not a big diff.** A reasoning model's time depends mostly on how much it writes and reasons. If reviews time out on modest diffs, lower `reasoning_effort`, add a fallback model from another provider, or raise `ai_timeout`.
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

Open the failed run. Errors appear as an annotation titled "PR-Agent wrapper". The run's summary shows the size of the diff, its largest files, what `ignore_globs` left out, and the review status. The PR comment carries the same reason.

"Re-run failed jobs" repeats the review on the diff the run already captured. GitHub reruns a workflow with the settings the run started with, so after you change the caller file, push a new commit to the PR or close and reopen it.

| What you see | Cause | Fix |
| --- | --- | --- |
| All three jobs skipped | The PR is a draft, comes from a fork, or the event is not one of the four supported types | Mark the PR ready for review. Fork PRs are not reviewed |
| ``Invalid input `name`: ...`` in `capture` | An input broke the rule named in the message. The value is never printed | Fix that input; the [Inputs](#inputs) table has the limits |
| `Review failed: key_validation_failed` | A provider in your chain has no key, or the caller did not pass it under `secrets:`. The `review` job log names it: `Missing required workflow secret: NAME` | Add the secret to the repository and pass it in the caller |
| `Review failed: inference_failed` | PR-Agent could not produce a review. When PR-Agent recognizes the cause (rejected key, no credits, rate limit, timeout, context limit, unreachable provider), the message ends with it | Follow the reason. For a timeout, raise `ai_timeout`, lower `reasoning_effort`, or add a fallback model; rerunning unchanged often times out again. With no reason, check the model ID and key with the provider. For a new or self-hosted model, set `custom_model_max_tokens` |
| `Review failed: empty_output` | PR-Agent wrote no review | Rerun. If it repeats, check that `ignore_globs` does not exclude every changed file |
| `Review failed: oversized_output` | The review is larger than a GitHub comment allows | Lower `num_max_findings`. The full output is in the run's artifact |
| `Review failed: missing_model_details` | The review lacks the footer that names the model, so its origin cannot be confirmed | Rerun. The output is in the run's artifact |
| `Ambiguous or missing merge base` in `capture` | The branch history has more than one merge base with the target | Rebase or merge the target branch into the PR |
| `Diff exceeds 4 MiB` in `capture` | The PR is too large to review | Split the PR. `ignore_globs` applies later and does not lower this size |

The reason after `inference_failed` is one of a few fixed sentences. Provider error text can contain credentials, so the workflow never logs it or posts it.

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

`fetch_upstream.py` downloads the pinned PR-Agent source files the tests need into `.test-upstream/` and verifies their hashes. To use files you already have, set `PRW_UPSTREAM_DIR` to their directory. The tests do not call a provider or publish to GitHub.

The review job builds PR-Agent v0.50.0 from commit `e673a02076ce09a4726c3d0c3cc85780e59ca52b`. To move the pin, run `.venv/bin/python scripts/bump_upstream.py <tag>` with the new release tag, read the upstream diff it links, delete `.test-upstream/`, and run the checks again. Dependabot proposes updates to the GitHub Actions the workflows pin.

[CONTRIBUTING.md](CONTRIBUTING.md) covers what a pull request needs.

## License

[MIT](LICENSE). PR-Agent and its dependencies keep their own licenses.
