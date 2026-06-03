# Raw Repository Data Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users save raw fetched repository data to a local file (`fetch` subcommand) and regenerate reports from it offline (`report --from-raw`), removing GitHub round-trips from the report-iteration loop.

**Architecture:** A new `cache.py` defines a `RawRepositories` envelope (metadata + raw repos), `save_raw`/`load_raw`, and a `RawFileSource` implementing the existing `GitHubSource` protocol. The CLI gains a `fetch` subcommand and a `report --from-raw` option; both reuse a shared error-handling/logging context manager.

**Tech Stack:** Python 3.13+, Pydantic v2, Click, pytest. See `docs/superpowers/specs/2026-05-28-raw-data-cache-design.md`.

**Conventions (must hold after every task):** `ruff check`, `ruff format --check`, and `mypy python` all pass; 53 existing tests stay green. Use `~/pyenv/bin/python -m <tool>`. Public classes and module-level functions need numpy-style docstrings. Imports go at module top level. The repo's pre-commit ruff-format may reformat files on commit; if a commit aborts with "files were modified by this hook", re-`git add` and re-commit.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `python/lsst/github_summarizer/cache.py` | `RawRepositories`, `CacheError`, `save_raw`, `load_raw`, `RawFileSource` (new) |
| `python/lsst/github_summarizer/cli.py` | Shared `_cli_context`; `report --from-raw`; new `fetch` subcommand (modify) |
| `tests/test_cache.py` | Tests for the cache module (new) |
| `tests/test_cli.py` | Add `fetch` and `--from-raw` tests (modify) |

---

## Task 1: Cache module

**Files:**
- Create: `python/lsst/github_summarizer/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cache.py`:

```python
"""Tests for the raw repository cache."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lsst.github_summarizer.cache import (
    CacheError,
    RawFileSource,
    RawRepositories,
    load_raw,
    save_raw,
)
from lsst.github_summarizer.models import Repository

FETCHED = datetime(2026, 6, 1, tzinfo=UTC)


def _repos() -> list[Repository]:
    return [
        Repository(
            name="afw",
            url="https://github.com/lsst/afw",
            topics=["pipelines"],
            pushed_at=datetime(2026, 1, 1, tzinfo=UTC),
            is_archived=True,
        ),
        Repository(name="dmtn-001", url="https://github.com/lsst/dmtn-001"),
    ]


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "raw.json"
    save_raw(path, _repos(), org="lsst", fetched_at=FETCHED)
    data = load_raw(path)
    assert data.org == "lsst"
    assert data.fetched_at == FETCHED
    assert [r.name for r in data.repositories] == ["afw", "dmtn-001"]
    assert data.repositories[0].topics == ["pipelines"]
    assert data.repositories[0].pushed_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert data.repositories[0].is_archived is True


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CacheError):
        load_raw(tmp_path / "nope.json")


def test_load_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "raw.json"
    path.write_text("{not json")
    with pytest.raises(CacheError):
        load_raw(path)


def test_load_unknown_schema_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "raw.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 999,
                "org": "lsst",
                "fetched_at": FETCHED.isoformat(),
                "repositories": [],
            }
        )
    )
    with pytest.raises(CacheError):
        load_raw(path)


def test_raw_file_source_returns_repos() -> None:
    data = RawRepositories(org="lsst", fetched_at=FETCHED, repositories=_repos())
    source = RawFileSource(data)
    assert [r.name for r in source.fetch_repositories("lsst")] == ["afw", "dmtn-001"]


def test_raw_file_source_warns_on_org_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    data = RawRepositories(org="lsst", fetched_at=FETCHED, repositories=_repos())
    source = RawFileSource(data)
    with caplog.at_level(logging.WARNING, logger="lsst.github_summarizer.cache"):
        result = source.fetch_repositories("other")
    assert [r.name for r in result] == ["afw", "dmtn-001"]
    assert any("other" in record.getMessage() for record in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/pyenv/bin/python -m pytest tests/test_cache.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.github_summarizer.cache'`.

- [ ] **Step 3: Write the implementation**

Create `python/lsst/github_summarizer/cache.py`:

```python
"""Save and reload raw repository data for offline report iteration."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .models import Repository

__all__ = [
    "CacheError",
    "RawFileSource",
    "RawRepositories",
    "load_raw",
    "save_raw",
]

_LOG = logging.getLogger(__name__)
_SCHEMA_VERSION = 1


class CacheError(Exception):
    """Raised when a raw cache file cannot be written, read, or validated."""


class RawRepositories(BaseModel):
    """Envelope of raw repository data saved to disk."""

    schema_version: int = _SCHEMA_VERSION
    org: str
    fetched_at: datetime
    repositories: list[Repository]


def save_raw(
    path: Path, repositories: list[Repository], *, org: str, fetched_at: datetime
) -> None:
    """Write repositories to a `RawRepositories` envelope at ``path``.

    Parameters
    ----------
    path : `pathlib.Path`
        Destination file.
    repositories : `list` [ `Repository` ]
        Raw repositories to save.
    org : `str`
        Organization the data came from.
    fetched_at : `datetime.datetime`
        When the data was fetched.

    Raises
    ------
    CacheError
        If the file cannot be written.
    """
    envelope = RawRepositories(org=org, fetched_at=fetched_at, repositories=repositories)
    try:
        path.write_text(json.dumps(envelope.model_dump(mode="json"), indent=2))
    except OSError as exc:
        raise CacheError(f"cannot write cache {path}: {exc}") from exc


def load_raw(path: Path) -> RawRepositories:
    """Read and validate a raw cache file.

    Parameters
    ----------
    path : `pathlib.Path`
        Cache file to read.

    Returns
    -------
    data : `RawRepositories`
        The validated envelope.

    Raises
    ------
    CacheError
        If the file is missing, not valid JSON, carries an unknown
        ``schema_version``, or fails schema validation.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise CacheError(f"cannot read cache {path}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CacheError(f"invalid JSON in cache {path}: {exc}") from exc

    if isinstance(data, dict) and data.get("schema_version") != _SCHEMA_VERSION:
        raise CacheError(
            f"unsupported cache schema_version {data.get('schema_version')!r} "
            f"in {path} (expected {_SCHEMA_VERSION})"
        )

    try:
        return RawRepositories.model_validate(data)
    except ValidationError as exc:
        raise CacheError(f"invalid cache {path}: {exc}") from exc


class RawFileSource:
    """A `GitHubSource` backed by a loaded `RawRepositories` envelope."""

    def __init__(self, data: RawRepositories) -> None:
        self._data = data

    def fetch_repositories(self, org: str) -> list[Repository]:
        """Return the cached repositories.

        Parameters
        ----------
        org : `str`
            The organization the caller expects; only used to warn on a
            mismatch with the cached organization.

        Returns
        -------
        repos : `list` [ `Repository` ]
            The cached repositories.
        """
        if org != self._data.org:
            _LOG.warning(
                "Requested org %r but cache holds org %r; using cached data",
                org,
                self._data.org,
            )
        return self._data.repositories
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/pyenv/bin/python -m pytest tests/test_cache.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
~/pyenv/bin/python -m ruff check . && ~/pyenv/bin/python -m mypy python
git add python/lsst/github_summarizer/cache.py tests/test_cache.py
git commit -m "Add raw repository cache module"
```

---

## Task 2: Refactor CLI error handling into a shared context manager

This is a behavior-preserving refactor so `report` and the new `fetch` command share logging setup and error translation. The 4 existing `test_cli.py` tests must still pass unchanged.

**Files:**
- Modify: `python/lsst/github_summarizer/cli.py`

- [ ] **Step 1: Replace `cli.py` with the refactored version**

Replace the entire contents of `python/lsst/github_summarizer/cli.py` with:

```python
"""Command-line interface for the GitHub organization summarizer."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import click

from .cache import CacheError
from .config import ConfigError, load_config
from .orchestrator import build_summaries
from .report import render_csv, render_json, render_markdown
from .source import GitHubError, GitHubGraphQLSource, resolve_token

__all__ = ["main"]

_LOG = logging.getLogger(__name__)


@contextmanager
def _cli_context(verbose: bool) -> Iterator[None]:
    """Configure logging and turn known errors into clean CLI failures.

    Parameters
    ----------
    verbose : `bool`
        Enable debug logging and re-raise (full traceback) on known errors.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        yield
    except (ConfigError, GitHubError, CacheError) as exc:
        if verbose:
            raise
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


@click.group()
def main() -> None:
    """Summarize the repositories in a GitHub organization."""


@main.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    help="Path to the YAML configuration file.",
)
@click.option("--org", default=None, help="Override the organization in the config.")
@click.option("--token", default=None, help="GitHub token (else discovered).")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["markdown", "csv", "json"]),
    default="markdown",
    help="Output format.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write to this path instead of stdout.",
)
@click.option("--include-archived", is_flag=True, help="Include archived repos.")
@click.option("--include-disabled", is_flag=True, help="Include disabled repos.")
@click.option("--appendix", is_flag=True, help="Append full inventory (markdown).")
@click.option(
    "--timeout",
    type=float,
    default=30.0,
    show_default=True,
    help="HTTP timeout in seconds per request.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable debug logging and show full tracebacks on error.",
)
def report(
    config_path: Path,
    org: str | None,
    token: str | None,
    output_format: str,
    output: Path | None,
    include_archived: bool,
    include_disabled: bool,
    appendix: bool,
    timeout: float,
    verbose: bool,
) -> None:
    """Generate a repository report for the configured organization."""
    with _cli_context(verbose):
        config = load_config(config_path)
        if org:
            config = config.model_copy(update={"org": org})

        resolved = resolve_token(token)
        source = GitHubGraphQLSource(resolved, timeout=timeout)
        now = datetime.now(UTC)
        summaries = build_summaries(
            source,
            config,
            now=now,
            include_archived=include_archived,
            include_disabled=include_disabled,
        )
        _LOG.info(
            "Generated report for %d repositories in %r", len(summaries), config.org
        )

        if output_format == "json":
            text = render_json(summaries, config, now)
        elif output_format == "csv":
            text = render_csv(summaries, config, now)
        else:
            text = render_markdown(summaries, config, now, include_appendix=appendix)

        if output is not None:
            output.write_text(text)
        else:
            click.echo(text, nl=False)
```

- [ ] **Step 2: Run existing CLI tests + full suite**

Run: `~/pyenv/bin/python -m pytest tests/test_cli.py -q && ~/pyenv/bin/python -m mypy python`
Expected: 4 CLI tests PASS; mypy clean.

- [ ] **Step 3: Commit**

```bash
~/pyenv/bin/python -m ruff check .
git add python/lsst/github_summarizer/cli.py
git commit -m "Refactor CLI error handling into shared context manager"
```

---

## Task 3: `fetch` subcommand

**Files:**
- Modify: `python/lsst/github_summarizer/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add these imports to the top of `tests/test_cli.py` (alongside the existing imports):

```python
from datetime import UTC, datetime

from lsst.github_summarizer.cache import load_raw, save_raw
```

Append to `tests/test_cli.py`:

```python
def test_fetch_writes_loadable_cache(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    repos = [
        Repository(name="afw", url="https://github.com/lsst/afw", is_archived=True),
        Repository(name="x", url="https://github.com/lsst/x"),
    ]
    _patch_backend(monkeypatch, repos)
    out = tmp_path / "raw.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["fetch", "--config", str(config_file), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    data = load_raw(out)
    assert data.org == "lsst"
    # Archived repos are saved too — no filtering at fetch time.
    assert {r.name for r in data.repositories} == {"afw", "x"}


def test_fetch_requires_output(
    monkeypatch: pytest.MonkeyPatch, config_file: Path
) -> None:
    _patch_backend(monkeypatch, [])
    runner = CliRunner()
    result = runner.invoke(cli.main, ["fetch", "--config", str(config_file)])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/pyenv/bin/python -m pytest tests/test_cli.py::test_fetch_writes_loadable_cache -q`
Expected: FAIL — `fetch` is not a command yet (nonzero exit / "No such command").

- [ ] **Step 3: Add the `fetch` command**

In `python/lsst/github_summarizer/cli.py`, add `save_raw` to the cache import:

```python
from .cache import CacheError, save_raw
```

Then append this command to the end of the file:

```python
@main.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    help="Path to the YAML configuration file.",
)
@click.option("--org", default=None, help="Override the organization in the config.")
@click.option("--token", default=None, help="GitHub token (else discovered).")
@click.option(
    "--output",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Destination file for the raw repository data.",
)
@click.option(
    "--timeout",
    type=float,
    default=30.0,
    show_default=True,
    help="HTTP timeout in seconds per request.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable debug logging and show full tracebacks on error.",
)
def fetch(
    config_path: Path,
    org: str | None,
    token: str | None,
    output: Path,
    timeout: float,
    verbose: bool,
) -> None:
    """Fetch raw repository data and save it for later reporting."""
    with _cli_context(verbose):
        config = load_config(config_path)
        if org:
            config = config.model_copy(update={"org": org})

        source = GitHubGraphQLSource(resolve_token(token), timeout=timeout)
        repos = source.fetch_repositories(config.org)
        save_raw(output, repos, org=config.org, fetched_at=datetime.now(UTC))
        _LOG.info("Saved %d repositories to %s", len(repos), output)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/pyenv/bin/python -m pytest tests/test_cli.py -q && ~/pyenv/bin/python -m mypy python`
Expected: all CLI tests PASS (including the 2 new ones); mypy clean.

- [ ] **Step 5: Commit**

```bash
~/pyenv/bin/python -m ruff check .
git add python/lsst/github_summarizer/cli.py tests/test_cli.py
git commit -m "Add fetch subcommand to save raw repository data"
```

---

## Task 4: `report --from-raw`

**Files:**
- Modify: `python/lsst/github_summarizer/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_report_from_raw_is_offline(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    out = tmp_path / "raw.json"
    save_raw(
        out,
        [Repository(name="afw", url="https://github.com/lsst/afw")],
        org="lsst",
        fetched_at=datetime(2026, 6, 1, tzinfo=UTC),
    )

    def boom(token: str | None) -> str:
        raise AssertionError("resolve_token must not be called with --from-raw")

    monkeypatch.setattr(cli, "resolve_token", boom)
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["report", "--config", str(config_file), "--from-raw", str(out), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["repositories"][0]["repo"]["name"] == "afw"


def test_report_from_raw_corrupt_exits_nonzero(
    config_file: Path, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["report", "--config", str(config_file), "--from-raw", str(bad)]
    )
    assert result.exit_code != 0
    assert "error:" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/pyenv/bin/python -m pytest tests/test_cli.py::test_report_from_raw_is_offline -q`
Expected: FAIL — `--from-raw` is not a recognized option yet (nonzero exit), and `resolve_token` would be called.

- [ ] **Step 3: Add `--from-raw` to `report`**

In `python/lsst/github_summarizer/cli.py`, extend the cache import and add the `GitHubSource` type:

```python
from .cache import CacheError, RawFileSource, load_raw, save_raw
from .source import GitHubError, GitHubGraphQLSource, GitHubSource, resolve_token
```

Add this option to the `report` command (place it just before the `--verbose` option):

```python
@click.option(
    "--from-raw",
    "from_raw",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="Render from a saved raw cache file instead of querying GitHub.",
)
```

Add the `from_raw` parameter to the `report` function signature (place it just before `verbose`):

```python
    from_raw: Path | None,
```

Replace the source-construction block in `report`:

```python
        resolved = resolve_token(token)
        source = GitHubGraphQLSource(resolved, timeout=timeout)
        now = datetime.now(UTC)
```

with:

```python
        now = datetime.now(UTC)
        source: GitHubSource
        if from_raw is not None:
            source = RawFileSource(load_raw(from_raw))
        else:
            source = GitHubGraphQLSource(resolve_token(token), timeout=timeout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/pyenv/bin/python -m pytest tests/test_cli.py -q && ~/pyenv/bin/python -m mypy python`
Expected: all CLI tests PASS (including the 2 new ones); mypy clean.

- [ ] **Step 5: Full verification**

Run: `~/pyenv/bin/python -m ruff check . && ~/pyenv/bin/python -m ruff format --check . && ~/pyenv/bin/python -m mypy python && ~/pyenv/bin/python -m pytest -q`
Expected: all green (53 prior + 6 cache + 4 new CLI = 63 tests).

- [ ] **Step 6: Commit**

```bash
git add python/lsst/github_summarizer/cli.py tests/test_cli.py
git commit -m "Add report --from-raw to render from a cached file"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** `cache.py` envelope + `save_raw`/`load_raw`/`CacheError`/`RawFileSource` (Task 1, spec §3); `fetch` subcommand saving unfiltered raw data (Task 3, spec §4.1); `report --from-raw` offline replay (Task 4, spec §4.2); `CacheError` added to caught exceptions via `_cli_context` (Task 2, spec §4.3); all spec §6 tests are present across Tasks 1, 3, 4.
- **Type consistency:** `RawRepositories`, `RawFileSource`, `load_raw`, `save_raw`, `CacheError`, `_cli_context`, `GitHubSource` are used identically wherever referenced. `RawFileSource.fetch_repositories(org)` matches the `GitHubSource` protocol so the `source: GitHubSource` annotation in `report` type-checks.
- **No filtering at fetch:** `fetch` calls `source.fetch_repositories` and saves the result directly — archived/disabled included (verified by `test_fetch_writes_loadable_cache`). Filtering remains in `build_summaries` at report time.
```
