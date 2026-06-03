# Raw Repository Data Cache — Design

- **Date:** 2026-05-28
- **Status:** Approved for planning
- **Repo:** `lsst-dm/github-summarizer`
- **Builds on:** `docs/superpowers/specs/2026-05-28-github-summarizer-design.md`

## 1. Purpose

Allow the raw repository data fetched from GitHub to be saved to a local file
and replayed into the report pipeline, so report generation can be iterated on
(grouping rules, filters, output formats) without re-querying GitHub each time.
A full `lsst-dm` fetch is ~45s over 11 paginated requests; caching removes that
cost from the development loop.

## 2. Key decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Command shape | New `fetch` subcommand to save; `report --from-raw PATH` to replay | Clean separation, composable, matches the Click-subcommands design. |
| File contents | Envelope: `schema_version`, `org`, `fetched_at`, `repositories` | Provenance, org-mismatch warning, and forward-compatible staleness/version detection. |
| What gets saved | All raw repos, **unfiltered** | Filtering/grouping/activity/format all stay at `report` time, so one cache serves many report variations. |
| Module placement | New `cache.py` | Keeps `source.py` focused on GitHub; the cache is a separate concern. |
| Replay mechanism | `RawFileSource` implementing the `GitHubSource` protocol | Reuses the existing swappable-source boundary; `build_summaries` is unchanged. |

## 3. New module: `cache.py`

```python
"""Save and reload raw repository data for offline report iteration."""

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
    """Write repositories to ``path`` as a RawRepositories envelope (indented JSON)."""


def load_raw(path: Path) -> RawRepositories:
    """Read and validate a raw cache file.

    Raises
    ------
    CacheError
        If the file is missing, not valid JSON, carries an unknown
        ``schema_version``, or fails schema validation.
    """


class RawFileSource:
    """A GitHubSource backed by a loaded RawRepositories envelope."""

    def __init__(self, data: RawRepositories) -> None: ...

    def fetch_repositories(self, org: str) -> list[Repository]:
        """Return the cached repositories.

        Logs a warning if ``org`` differs from the envelope's ``org`` (the
        cache was taken from a different organization) but still returns the
        cached data.
        """
```

- `save_raw` builds a `RawRepositories` and writes `model_dump(mode="json")`
  via `json.dumps(..., indent=2)`. Wraps `OSError` in `CacheError`.
- `load_raw` reads the file, `json.loads`, checks `schema_version == _SCHEMA_VERSION`
  (raise `CacheError` otherwise, naming the found and expected versions), then
  `RawRepositories.model_validate`. Wraps `OSError`, `json.JSONDecodeError`, and
  `pydantic.ValidationError` in `CacheError`.
- `RawFileSource.fetch_repositories` ignores the requested `org` for data
  selection (a cache holds one org's data) but compares it to `data.org` to
  emit the mismatch warning.

## 4. CLI changes (`cli.py`)

### 4.1 New `fetch` subcommand

```
github-summarizer fetch --config FILE --output PATH
    [--org ORG] [--token TOKEN] [--timeout N] [--verbose]
```

Behavior:
1. Configure logging from `--verbose` (as `report` does).
2. `load_config`; apply `--org` override.
3. `resolve_token(token)`; build `GitHubGraphQLSource(resolved, timeout=timeout)`.
4. `repos = source.fetch_repositories(config.org)` — **all** raw repos, no
   archived/disabled filtering.
5. `save_raw(output, repos, org=config.org, fetched_at=datetime.now(UTC))`.
6. Log an info line with the count and destination.

`--output` is required (a `click.Path` with `path_type=Path`).

### 4.2 `report --from-raw PATH`

Add:

```python
@click.option(
    "--from-raw",
    "from_raw",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="Render from a saved raw cache file instead of querying GitHub.",
)
```

In `report`, choose the source:

```python
if from_raw is not None:
    source: GitHubSource = RawFileSource(load_raw(from_raw))
else:
    source = GitHubGraphQLSource(resolve_token(token), timeout=timeout)
```

When `--from-raw` is given, token resolution and GitHub are skipped entirely.
`--token`/`--timeout` are accepted but unused in this mode. The rest of the
pipeline (`build_summaries`, filtering, rendering) is unchanged.

### 4.3 Error handling

The CLI's caught-exception tuple becomes `(ConfigError, GitHubError, CacheError)`.
Clean `error: <message>` to stderr and exit 1; full traceback only under
`--verbose`. Applies to both `report` and `fetch`.

## 5. Data flow

```
fetch:
  load_config -> resolve_token -> GitHubGraphQLSource.fetch_repositories(org)
    -> save_raw(envelope)                                   (cache.py)

report --from-raw cache.json:
  load_config -> load_raw(path) -> RawFileSource            (cache.py)
    -> build_summaries(source, config, ...)                 (unchanged)
    -> render_<format>                                      (unchanged)
```

## 6. Testing

Mocked at the source boundary; no network.

- **cache:**
  - `save_raw` then `load_raw` round-trips fields including `pushed_at`,
    `topics`, and booleans.
  - `load_raw` raises `CacheError` on a missing file.
  - `load_raw` raises `CacheError` on malformed JSON.
  - `load_raw` raises `CacheError` on an unknown `schema_version`.
  - `RawFileSource.fetch_repositories` returns the cached repos.
  - `RawFileSource.fetch_repositories` logs a warning when the requested org
    differs from the envelope's org.
- **cli:**
  - `fetch` writes a file that `load_raw` accepts, with the expected `org` and
    repository names (fake `fetch_repositories`).
  - `fetch` saves archived/disabled repos too (no filtering at fetch time).
  - `report --from-raw` renders successfully **without** calling
    `resolve_token` (monkeypatch it to raise) — proving the offline path.
  - `report --from-raw` on a corrupt cache file exits non-zero with `error:`.

## 7. Out of scope (YAGNI)

- Automatic cache invalidation / TTL (the user decides when to re-`fetch`).
- Incremental/partial updates to a cache.
- Caching anything beyond the raw repository list (no rendered-report cache).
