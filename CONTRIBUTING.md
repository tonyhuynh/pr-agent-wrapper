# Contributing

Bug reports and pull requests are welcome.

## Report a problem

Open an issue for bugs and feature requests. Say what you ran, what you expected, and what happened. A link to the failed run and the text of its `PR-Agent wrapper` annotation are the most useful details.

Do not open an issue for a security problem. Follow [SECURITY.md](SECURITY.md).

Never paste keys, private diffs, or private review output into an issue or pull request.

## Propose a change

1. For anything larger than a small fix, open an issue first so we can agree on the approach.
2. Set up the checks as described in [Developing this repository](README.md#developing-this-repository).
3. Make the change, with a test for it. Tests live in `tests/` and run without provider keys.
4. Run every check in that section. CI runs the same ones.
5. Open a pull request that says what changed and why.

## What a change needs

- **Tests.** A change to the workflow's embedded Python needs a test in `tests/test_wrapper.py`. A change that depends on PR-Agent's behavior needs one in `tests/test_upstream.py`, which runs the pinned PR-Agent source.
- **Fixed wording in logs.** Error messages are written in this repository and never repeat a caller's input or a provider's response. Keep it that way.
- **Separate privileges.** No job may hold both a provider key and a GitHub write token.
- **Pinned actions.** Reference every action by full commit SHA.
- **Docs.** A new or changed input needs a row in the README's inputs table and a line in [CHANGELOG.md](CHANGELOG.md).

## Live testing

CI never calls a model provider. If your change affects what the workflow sends or posts, run it from a repository of your own: point a caller workflow at your branch's commit SHA and open a pull request there.
