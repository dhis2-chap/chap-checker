# Release process

`chap-checker` ships to [PyPI](https://pypi.org/project/chap-checker/)
via a GitHub Actions workflow using PyPI's
[trusted publishing](https://docs.pypi.org/trusted-publishers/) flow.
No long-lived API tokens are stored anywhere - PyPI verifies an OIDC
token GitHub mints for the specific workflow file + repository +
environment combination.

## One-time setup (done already)

On https://pypi.org/manage/account/publishing/ a trusted publisher is
configured with:

| Field        | Value             |
| ------------ | ----------------- |
| Project name | `chap-checker`    |
| Owner        | `dhis2-chap`      |
| Repository   | `chap-checker`    |
| Workflow     | `release.yml`     |
| Environment  | `pypi`            |

The corresponding GitHub Environment lives at
`Settings -> Environments -> pypi` on the repo. Add reviewers there if
you want to gate publish on manual approval.

## Cutting a release

1. **Bump the version.** Edit `pyproject.toml` -> `[project].version`
   following [SemVer](https://semver.org/):

   - `MAJOR` for breaking changes
   - `MINOR` for backwards-compatible features
   - `PATCH` for backwards-compatible bug fixes

2. **Update the changelog / docs** if you keep one (none yet, but
   tag annotations work as a starting point).

3. **Open a PR**, get CI green, merge to `main`.

4. **Tag from `main`** with a `v`-prefixed semver tag matching the
   new version:

   ```bash
   git checkout main && git pull
   git tag -a v0.2.0 -m "v0.2.0: short summary"
   git push origin v0.2.0
   ```

   The release workflow:

   1. Verifies the tag (`v0.2.0`) matches `pyproject.toml` version
      (`0.2.0`); fails fast otherwise.
   2. Runs `uv build` to produce both an sdist and a wheel.
   3. Uploads to PyPI via the trusted-publisher OIDC exchange.
   4. Creates a GitHub Release for the tag with auto-generated
      notes (from merged PRs since the previous tag) and the wheel +
      sdist attached as assets.

5. **Verify** within ~30s of the workflow completing:

   ```bash
   pip install chap-checker --upgrade
   chap-checker --version
   ```

   Both pages update on success:
   - https://pypi.org/project/chap-checker/
   - https://github.com/dhis2-chap/chap-checker/releases

## Dry-run / hotfix tips

- **Test the build locally** before pushing the tag:

  ```bash
  uv build
  ls dist/
  ```

  Inspect the wheel contents to confirm `src/chap_checker/web_ui/`
  is included if you've changed anything there.

- **Bad tag, no upload yet?** Delete it before the workflow lands:

  ```bash
  git push --delete origin v0.2.0
  git tag -d v0.2.0
  ```

  PyPI versions are immutable once uploaded. If you've already
  published, you must bump to the next patch (`v0.2.1`) - you cannot
  re-upload `v0.2.0`.

- **Yank a bad release** from the PyPI web UI under the project's
  "Manage" page. Yanking hides the version from new installs but
  doesn't break pins.

## Auth model (why no secrets)

The publish step uses [`pypa/gh-action-pypi-publish`](https://github.com/pypa/gh-action-pypi-publish)
with the workflow's `id-token: write` permission. At runtime, the
GitHub runner asks for an OIDC token that encodes:

- repo (`dhis2-chap/chap-checker`)
- workflow filename (`release.yml`)
- environment name (`pypi`)
- branch / tag context

PyPI's pre-configured trusted publisher entry says "I trust uploads
signed by exactly that combination," so no API tokens, deploy keys,
or `PYPI_TOKEN` secrets exist in the repo. Compromising a token
isn't a risk because there is no token to compromise; only a malicious
edit to `.github/workflows/release.yml` (which CODEOWNERS / branch
protection can gate) could trick PyPI into accepting a bad upload.
