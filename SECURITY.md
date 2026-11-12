# Security policy

The workflow trusts the caller workflow, wrapper, pinned upstream source, dependencies, GitHub runner, and Docker. Maintainers who can change workflows can affect secret handling. Fork and draft checks cannot stop a malicious trusted maintainer or compromised dependency.

## Secret handling

Capture uses a read token to fetch fixed base and head SHAs into a bare repository and make the diff. It runs no PR code, filters, submodules, or LFS processes.

Review has no GitHub token. It builds source without credentials; only the run step receives provider keys. It checks every configured provider's key before starting the container, and only the keys for providers in the configured model chain reach the job. Missing-key errors name the secret without printing its value. Input errors name the input and the rule it broke, in fixed wording that never repeats the value.

The container runs as a nonroot user with a read-only root filesystem, dropped capabilities, resource limits, read-only diff input, and separate writable storage. It receives no host credentials. The workflow suppresses provider output and disables callbacks and telemetry.

Publish has a separate write token and no provider keys. It validates artifacts and rechecks PR state before writing a comment. In `update` mode it edits only a comment that starts with the wrapper's marker and was written by `github-actions[bot]`; a person cannot post as that account, though another workflow in the same repository could. It never approves, requests changes, labels, merges, or edits other comments. Do not make it a required check.

## Remaining risks

There is no network egress allowlist. Malicious dependencies or a compromised runner or Docker daemon could expose provider keys. Output screening can miss encoded or transformed secrets; GitHub log masking can miss them too. Builds still depend on registries, packages, and tooling.

Providers receive the diff and instructions under their own data policies. With `api_base`, the diff and `OPENAI_API_KEY` go to the URL the caller workflow names, so treat that input like a secret destination and never set it from PR-controlled data. Artifacts retain the diff and output for 3 days. People with artifact access can read them. Do not send material that must stay within the repository.

Prompt injection remains possible. Check model findings, links, and Markdown before acting. Ignored files, binary patches, missing context, and token limits reduce coverage. PR state can change after the publisher's final check. In `new` mode, reruns add duplicate comments.

Report vulnerabilities through GitHub's private vulnerability reporting feature when available. Never put credentials or private source in public issues. For a suspected leak, stop the caller workflow, revoke affected provider keys, inspect artifact and comment access, and report privately.
