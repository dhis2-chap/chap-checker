# Project rules

1. **No emojis.** Never use emojis anywhere — code, comments, docs, commit messages, PR descriptions, chat output.
2. **No Claude Code attribution.** Do not add `Co-Authored-By: Claude ...`, "Generated with Claude Code", or any similar attribution to commits, PRs, or files.
3. **Conventional Commits** for all git activity — commit messages, branch names, and PR titles.
   - Format: `<type>(<scope>)?: <description>` (e.g. `feat(ci): add docker publish workflow`, `fix(main): correct db path creation`).
   - Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `build`, `perf`, `style`, `revert`.
   - Branch names: `<type>/<short-description>` (e.g. `feat/makefile-and-ci`, `fix/sqlite-path`).
4. **uv only — no pip.** Document and run everything through `uv`: `uvx chap-checker ...` for one-shots, `uv tool install / uv tool upgrade chap-checker` for persistent installs, `uv add chap-checker` for embedding in another project. Never write `pip install` in code, docs, or chat output.
5. **Releasing — bump → tag → push.** Releases ship to PyPI + a GitHub Release via `.github/workflows/release.yml` on every `v*` tag push. The workflow only fires for tags; merging to `main` never publishes by itself.

   To cut a release:

   ```bash
   git checkout main && git pull
   # 1) Bump [project].version in pyproject.toml following SemVer.
   # 2) Commit the bump and push to main (PR + merge, or direct if you have permission).
   git tag -a v<NEW_VERSION> -m "v<NEW_VERSION>: short summary"
   git push origin v<NEW_VERSION>
   ```

   Rules and gotchas:

   - **The tag MUST match `pyproject.toml`'s `version` exactly** (minus the `v` prefix). The workflow's first job fails fast otherwise, so a mismatched tag never reaches PyPI.
   - **PyPI versions are immutable.** Once `v0.2.0` is uploaded you cannot re-upload it. To fix a broken release, bump to the next patch (`v0.2.1`) and re-tag — or yank the bad version from the PyPI UI.
   - **The `pypi` GitHub Environment may gate publish on manual approval.** If you don't see the upload in 30s, check the workflow run page for a "Review deployments" prompt.
   - **Don't delete a successful tag.** Tags are public history and the GitHub Release attaches to them. Use a follow-up patch tag instead.
   - The full operator playbook lives at [`docs/guides/releasing.md`](docs/guides/releasing.md) — keep it in sync whenever the workflow changes.
