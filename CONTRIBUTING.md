# Contributing

Contributions are welcome through GitHub issues and pull requests.

## Never submit private data

Use synthetic fixtures only. Do not commit or paste:

- API keys, access tokens, cookies, passwords, or credential files;
- client, account, employee, customer, or participant names;
- real campaign metrics, budgets, verdicts, workflow notes, or source URLs;
- production transcripts, media, screenshots, reports, or local absolute paths;
- copyrighted or third-party source copied without a compatible license.

If a bug needs real media to reproduce, reduce it to a synthetic fixture or coordinate a private disclosure through GitHub Security.

## Development setup

```sh
git clone https://github.com/FernGuard/reelly.git
cd reelly
uv sync
uv run --with pytest python -m pytest tests/ -q
```

Add tests for behavior changes. Tests must not make paid network calls or require production credentials.

## Effects

New effects are a `.glsl` file plus a preset (see [docs/EFFECTS.md](docs/EFFECTS.md)).
Add a case to `reelly/golden.py`, record references with
`python -m reelly.golden --update` on your machine, and say which renderer
tier you recorded in the pull request. A change that alters an existing
effect's look must update its references in the same pull request. CI checks
the Linux, macOS and Windows runners' references on every pull request.

If you change a setting, keep `reelly/settings.py` current and regenerate
`docs/SETTINGS.md` (`python -m reelly.settings --markdown > docs/SETTINGS.md`).

## Before opening a pull request

```sh
python -m compileall -q reelly
uv run --with pytest python -m pytest tests/ -q
git diff --check
```

Review the diff for personal paths, identifiers, and generated files. Do not include local `~/.reelly` configuration or anything under the project workspace.

By contributing, you agree that your contribution is licensed under the MIT License in this repository.
