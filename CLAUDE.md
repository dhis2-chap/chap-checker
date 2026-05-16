# Project rules

1. **No emojis.** Never use emojis anywhere — code, comments, docs, commit messages, PR descriptions, chat output.
2. **No Claude Code attribution.** Do not add `Co-Authored-By: Claude ...`, "Generated with Claude Code", or any similar attribution to commits, PRs, or files.
3. **Conventional Commits** for all git activity — commit messages, branch names, and PR titles.
   - Format: `<type>(<scope>)?: <description>` (e.g. `feat(ci): add docker publish workflow`, `fix(main): correct db path creation`).
   - Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `build`, `perf`, `style`, `revert`.
   - Branch names: `<type>/<short-description>` (e.g. `feat/makefile-and-ci`, `fix/sqlite-path`).
4. **uv only — no pip.** Document and run everything through `uv`: `uvx chap-checker ...` for one-shots, `uv tool install / uv tool upgrade chap-checker` for persistent installs, `uv add chap-checker` for embedding in another project. Never write `pip install` in code, docs, or chat output.
5. **Keep the housekeeping files in sync with what ships.** Several files at the repo root document the project's state — they go stale silently if nobody touches them. When making a change, ask which of these need an update *in the same PR* (not as a follow-up):

   - **`CHANGELOG.md`** — append to the `## [Unreleased]` section on *every* user-visible PR (CLI changes, config schema, alert / check additions, behaviour changes, breaking changes). Skip only for pure-internal refactors and chore/CI PRs. The CHANGELOG sections to use: `Added`, `Changed`, `Fixed`, `Removed`, `Deprecated`, `Security`. Mark breaking changes with `(**breaking**)`.
   - **`README.md`** — Quick start, surface list, built-in checks table, badges. Update when any of those shift. The "built-in checks" bullet list especially: it's been out-of-sync more than once.
   - **`SECURITY.md`** — the "Supported versions" table lists the currently-patched line. Bump on every major / minor release.
   - **`CONTRIBUTING.md`** — when `make` targets change, when the `src/chap_checker/` layout moves, or when the "Adding things" recipe (a new check / alerter) changes shape.
   - **`docs/guides/*.md`** — anything in the docs site that names a CLI command, flag, config field, or file path. `make docs-cli` regenerates `docs/cli-reference.md` automatically; the prose guides are manual.
   - **`chap-checker.toml.example`** + the `DEFAULT_INIT_TEMPLATE` constant in `src/chap_checker/cli.py` — when adding a new config block (`[alerts.foo]`, `[retry]`, `[ui]`, ...). The init template is what `chap-checker init` writes; both should mirror each other.

   If a PR adds a new "thing operators discover at runtime" (a check, an alerter, a CLI flag, a config option), the rule of thumb is: it shows up in **at least three** places — code, CHANGELOG entry, docs/guide section. Verify all three before requesting review.

6. **Releasing — bump → tag → push.** Releases ship to PyPI + a GitHub Release via `.github/workflows/release.yml` on every `v*` tag push. The workflow only fires for tags; merging to `main` never publishes by itself.

   To cut a release:

   ```bash
   git checkout main && git pull
   # 1) Bump [project].version in pyproject.toml following SemVer.
   # 2) Move CHANGELOG's `[Unreleased]` block into a new `[<version>] — <date>` section
   #    and reset `[Unreleased]` to empty. Update the bottom-of-file compare-link.
   # 3) For minor/major bumps, update SECURITY.md's "Supported versions" table.
   # 4) Commit ("chore(release): vX.Y.Z") and push to main.
   git tag -a v<NEW_VERSION> -m "v<NEW_VERSION>: short summary"
   git push origin v<NEW_VERSION>
   ```

   Rules and gotchas:

   - **The tag MUST match `pyproject.toml`'s `version` exactly** (minus the `v` prefix). The workflow's first job fails fast otherwise, so a mismatched tag never reaches PyPI.
   - **PyPI versions are immutable.** Once `v0.2.0` is uploaded you cannot re-upload it. To fix a broken release, bump to the next patch (`v0.2.1`) and re-tag — or yank the bad version from the PyPI UI.
   - **The `pypi` GitHub Environment may gate publish on manual approval.** If you don't see the upload in 30s, check the workflow run page for a "Review deployments" prompt.
   - **Don't delete a successful tag.** Tags are public history and the GitHub Release attaches to them. Use a follow-up patch tag instead.
   - The full operator playbook lives at [`docs/guides/releasing.md`](docs/guides/releasing.md) — keep it in sync whenever the workflow changes.
