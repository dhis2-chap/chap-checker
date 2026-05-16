<!--
Title format: <type>(<scope>): <description>
Examples:
  feat(alerts): generic webhook alerter
  fix(checks): empty error message on httpx.ReadTimeout
  docs(serve): systemd unit example
Types: feat, fix, docs, chore, refactor, test, ci, build, perf, style, revert.
-->

## Summary

<!-- 1-3 sentences. What does this change, and why? Link the issue if one exists. -->

## Test plan

<!-- Tick what applies. Add commands / manual steps where useful. -->

- [ ] `make test` passes locally
- [ ] `make check` passes locally (ruff format, ruff lint, mypy, pyright)
- [ ] CLI surface change? `make docs-cli` regenerated
- [ ] User-visible change? `CHANGELOG.md` "Unreleased" entry added
- [ ] Manual verification (commands / screenshots):

## Breaking changes

<!-- Skip if none. Otherwise: what breaks, how callers migrate. -->

## Notes for the reviewer

<!-- Anything non-obvious about the diff, risky paths, things you'd like a second opinion on. -->
