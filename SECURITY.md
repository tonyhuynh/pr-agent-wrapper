# Security policy

The workflow trusts the caller workflow, wrapper, pinned upstream source, dependencies, GitHub runner, and Docker. Maintainers who can change workflows can affect secret handling. Fork and draft checks cannot stop a malicious trusted maintainer or compromised dependency.

## Secret handling

Capture uses a read token to fetch fixed base and head SHAs into a bare repository and make the diff. It runs no PR code, filters, submodules, or LFS processes.

Review has no GitHub token. It builds source without credentials; only the run step receives provider keys. It checks every configured provider's key before starting the container. The default needs only `XAI_API_KEY`; fallback runs only when configured. Missing-key errors name the secret without printing its value.

The container runs as a nonroot user with a read-only root filesystem, dropped capabilities, resource limits, read-only diff input, and separate writable storage. It receives no host credentials. The workflow suppresses provider output and disables callbacks and telemetry.

Publish has a separate write token and no provider keys. It validates artifacts and rechecks PR state before posting a new comment. It never approves, requests changes, edits old comments, labels, or merges. Do not make it a required check.

## Remaining risks

There is no network egress allowlist. Malicious dependencies or a compromised runner or Docker daemon could expose provider keys. Output screening can miss encoded or transformed secrets; GitHub log masking can miss them too. Builds still depend on registries, packages, and tooling.

Providers receive the diff and instructions under their own data policies. Artifacts retain the diff and output for 3 days. People with artifact access can read them. Do not send material that must stay within the repository.

Prompt injection remains possible. Check model findings, links, and Markdown before acting. Ignored files, binary patches, missing context, and token limits reduce coverage. PR state can change after the publisher's final check; reruns can add duplicate comments.

Report vulnerabilities through GitHub's private vulnerability reporting feature when available. Never put credentials or private source in public issues. For a suspected leak, stop the caller workflow, revoke affected provider keys, inspect artifact and comment access, and report privately.
