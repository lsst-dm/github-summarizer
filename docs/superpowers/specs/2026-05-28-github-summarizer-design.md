# GitHub Organization Summarizer — Design

- **Date:** 2026-05-28
- **Status:** Approved for planning
- **Repo:** `lsst-dm/github-summarizer`
- **Source spec:** `GOALS.md`

## 1. Purpose

A Python command-line tool that scans all repositories in a GitHub
organization and generates a report summarizing each repository's metadata,
activity status, and semantic grouping. The design favors a prototype that is
easy to extend (new fields, new output formats) and easy to reorganize later.

## 2. Key decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| GraphQL client | `httpx` (sync) + hand-written query, behind a swappable `GitHubSource` interface | Minimal deps, full control over pagination/rate limits, easy to mock. Source boundary lets us swap backends later. |
| Data models | Pydantic throughout; raw `Repository` separated from derived `RepositorySummary` | Free YAML validation with friendly errors; trivial JSON output. Matches the `pydantic.mypy` plugin already in `pyproject.toml`. Keeping derived data out of the raw record means a summary is never half-populated. |
| CLI surface | `github-summarizer` group + `report` subcommand | Matches repo name; leaves room for future subcommands. |
| Name-pattern grouping | Both `glob:` (documented default) and `regex:`, glob case-insensitive | Globs are friendlier and auto-anchored for prefix patterns like `DMTN-*`; regex retained for power cases and GOALS compatibility. |
| Auto-topic grouping | Cluster leftover repos by shared topic; enabled by default, `min_repos = 3` | Related repos surface as a group instead of all landing in Uncategorized. Default lives in one field so it is trivial to flip. |
| Token discovery | `--token` → `GITHUB_TOKEN`/`GH_TOKEN` → `gh auth token` → `git credential fill` → error | Picks up tokens automatically the way developers already authenticate. |
| Architecture | Linear pipeline of pure stages with a thin orchestrator | Each stage maps to a GOALS module, has one job, tests in isolation. |

This is a prototype; modules may be reorganized as it matures.

## 3. Module layout

```
python/lsst/github_summarizer/
  __init__.py        # version only
  models.py          # Pydantic: Repository (raw), RepositorySummary (derived), ActivityStatus
  config.py          # Pydantic: Config + sub-models; load_config()
  source.py          # GitHubSource Protocol + GitHubGraphQLSource (httpx); token resolution
  activity.py        # classify_activity(repo, cfg, *, now) -> ActivityStatus
  grouping.py        # Grouper(config).assign(repos) -> sets group + reason
  report.py          # render_markdown / render_csv / render_json
  orchestrator.py    # wiring: fetch -> enrich -> enriched repos
  cli.py             # Click group `github-summarizer` + `report` subcommand
```

## 4. Data flow

```
CLI parses args
  -> load_config(path)                       (config.py)
  -> resolve_token(flag, env, gh, git)       (source.py)
  -> GitHubSource.fetch_repositories(org)    (source.py)   [swappable boundary]
  -> filter out archived/disabled unless --include-* given (orchestrator.py)
  -> classify_activity + Grouper.assign
        -> list[RepositorySummary]           (orchestrator.py)
  -> report.render_<format>(summaries, config, generated_at)   (report.py)
  -> write to --output or stdout             (cli.py)
```

Data flows one direction through pure stages. Everything downstream of the
source is independent of GitHub/GraphQL/httpx. The orchestrator builds each
`RepositorySummary` from a `Repository` by calling `classify_activity` and the
`Grouper` (which needs the full set for the auto-topic pass).

## 5. Data models (`models.py`)

```python
class ActivityStatus(StrEnum):
    ACTIVE = "active"
    WARM = "warm"
    QUIET = "quiet"
    DORMANT = "dormant"
    ARCHIVED = "archived"
    DISABLED = "disabled"

class Repository(BaseModel):
    """Raw repository data as returned by the source. No derived fields."""
    name: str
    url: str
    description: str | None = None
    primary_language: str | None = None
    topics: list[str] = []
    pushed_at: datetime | None = None
    is_archived: bool = False
    is_disabled: bool = False
    default_branch: str | None = None

class RepositorySummary(BaseModel):
    """A repository plus the data derived by the pipeline."""
    repo: Repository
    activity: ActivityStatus
    group: str
    grouping_reason: str
```

Two models keep concerns separated: `Repository` is the pure, untouched record
the source produces; `RepositorySummary` is what the pipeline derives from it.
Because the derived fields are always computed, they are required on
`RepositorySummary` (not `Optional`), so a summary is never half-populated. The
source returns `list[Repository]`; the orchestrator returns
`list[RepositorySummary]`. Both serialize cleanly via `model_dump`
(`RepositorySummary` nests the repo, which JSON/CSV rendering flattens).

## 6. Configuration (`config.py`)

Validated YAML mirroring the GOALS example.

```python
class ActivityConfig(BaseModel):
    active_days: int = 90
    warm_days: int = 180
    quiet_days: int = 365

class GroupRule(BaseModel):
    name: str
    topics: list[str] = []
    glob: list[str] = []      # case-insensitive fnmatch patterns
    regex: list[str] = []     # validated/compiled in a field validator
    description: str | None = None

class Override(BaseModel):
    group: str
    notes: str | None = None

class AutoGroupConfig(BaseModel):
    enabled: bool = True       # single place to flip the default
    min_repos: int = 3
    ignore_topics: list[str] = []

class Config(BaseModel):
    org: str
    activity: ActivityConfig = ActivityConfig()
    groups: list[GroupRule] = []
    overrides: dict[str, Override] = {}      # keyed by repo name
    auto_group_by_topic: AutoGroupConfig = AutoGroupConfig()
    fallback_group: str = "Uncategorized"

def load_config(path: Path) -> Config: ...
```

- Regex patterns are compiled in a Pydantic validator so a bad pattern fails
  fast at load time with a clear message.
- `load_config` raises a typed `ConfigError` on malformed YAML / schema errors.
- CLI flags (`--org`, `--token`) override config values after loading.

Example YAML:

```yaml
org: lsst

activity:
  active_days: 90
  warm_days: 180
  quiet_days: 365

groups:
  - name: Pipelines
    topics: [pipelines]
    description: Virtual monorepo / coordinated pipelines repositories
  - name: Data Management Tech Notes
    glob: ["DMTN-*"]            # case-insensitive; also matches dmtn-*
  - name: Documentation
    topics: [documentation, docs]

auto_group_by_topic:
  enabled: true
  min_repos: 3
  ignore_topics: []

overrides:
  special-repo-name:
    group: Pipelines
    notes: Manual override
```

## 7. Activity classification (`activity.py`)

```python
def classify_activity(
    repo: Repository, cfg: ActivityConfig, *, now: datetime
) -> ActivityStatus: ...
```

Precedence:

1. `repo.is_disabled` -> `DISABLED`
2. `repo.is_archived` -> `ARCHIVED`
3. otherwise by age of `pushed_at`:
   - `<= active_days` -> `ACTIVE`
   - `<= warm_days` -> `WARM`
   - `<= quiet_days` -> `QUIET`
   - else -> `DORMANT`
4. `pushed_at is None` -> `DORMANT`

`now` is injected so tests are deterministic.

## 8. Grouping (`grouping.py`)

`Grouper(config).assign(repos: list[Repository]) -> dict[str, tuple[str, str]]`
returns the `(group, grouping_reason)` for each repo (keyed by name). It takes
the full list because the auto-topic pass needs to see all ungrouped repos at
once. The orchestrator combines this with the activity status to build each
`RepositorySummary`. Precedence is first-match:

1. **Override** — name in `overrides:` -> `reason = "override"`
2. **Topic rule** — repo topic in a group's `topics:` -> `reason = "topic:<topic>"`
3. **Glob** — name matches a group's `glob:` (case-insensitive `fnmatch`) -> `reason = "glob:<pattern>"`
4. **Regex** — name matches a group's `regex:` -> `reason = "regex:<pattern>"`
5. **Auto-topic** — clustering pass (below) -> `reason = "auto-topic:<topic>"`
6. **Fallback** — `group = fallback_group`, `reason = "fallback"`

### Auto-topic grouping (step 5)

Runs only on repos still ungrouped after steps 1–4, when
`auto_group_by_topic.enabled`:

1. Build a `topic -> {repos}` index over the ungrouped pool, skipping
   `ignore_topics`.
2. Pick the topic covering the most ungrouped repos; break ties
   alphabetically (reproducible reports).
3. If that count `>= min_repos`, form a group named after the topic, assign
   those repos (`auto-topic:<topic>`), and remove them from the pool.
4. Repeat until no remaining topic meets `min_repos`. Leftovers -> fallback.

Greedy assign-then-remove means a multi-topic repo lands in its largest
cluster, deterministically. Pure function over the repo list; no mocks needed.

## 9. Report writers (`report.py`)

Three pure functions over `list[RepositorySummary]`, `Config`, and a
`generated_at` timestamp, each returning `str`:

- `render_json` — `model_dump` of the summaries plus a summary-counts block;
  stable field order.
- `render_csv` — one row per repo; columns are the raw + derived fields
  (flattened from the nested `repo`); topics joined with `;`.
- `render_markdown` — full GOALS structure:
  1. Title + generation timestamp
  2. Summary counts: total, active, warm, quiet, dormant, archived, disabled
  3. One section per group (name, optional description, count, table)
  4. Uncategorized section
  5. Optional raw-inventory appendix (`--appendix`, off by default)

  Repository table columns: Repo, Description, Primary language, Topics,
  Last push, Activity status, Archived, Disabled, Grouping reason.

A new format later = one new function + one line in the CLI format dispatch.

## 10. Source & token discovery (`source.py`)

```python
class GitHubSource(Protocol):
    def fetch_repositories(self, org: str) -> list[Repository]: ...

class GitHubGraphQLSource:
    def __init__(self, token: str, *, base_url: str = "https://api.github.com/graphql"): ...
    def fetch_repositories(self, org: str) -> list[Repository]: ...
```

`GitHubGraphQLSource` owns:

- the GraphQL query (fields: `name`, `url`, `description`, `isArchived`,
  `isDisabled`, `primaryLanguage { name }`, `pushedAt`, `repositoryTopics`,
  `defaultBranchRef { name }`);
- cursor pagination (`pageInfo.hasNextPage` / `endCursor`, 100 per page);
- rate-limit handling: retry on `RATE_LIMITED` / HTTP 403 honoring
  `Retry-After` / `X-RateLimit-Reset`, capped backoff;
- typed `GitHubError` on hard failures.

### Token resolution (first hit wins, each step best-effort)

1. `--token` flag
2. `GITHUB_TOKEN` env, then `GH_TOKEN` env
3. `gh auth token` (if `gh` on PATH)
4. `git credential fill` for `github.com` (stdin `protocol=https`/`host=github.com`, read back `password=`)
5. error: "no GitHub token found (tried …)"

Each resolver is a `() -> str | None` function; the chain iterates. The token
is never logged (even under `--verbose`); `--verbose` logs which source
supplied it. Scopes are not verified locally — insufficient scopes surface as
a clear API error.

## 11. CLI (`cli.py`, Click)

```
github-summarizer report --config FILE
    [--org ORG] [--token TOKEN]
    [--format markdown|csv|json] [--output PATH]
    [--include-archived] [--include-disabled]
    [--appendix] [--verbose]
```

`report` loads config, applies flag overrides, resolves the token, fetches,
enriches, renders, and writes to `--output` or stdout.

**Archived and disabled repos are excluded by default** — they hold little
interest for the typical report. `--include-archived` / `--include-disabled`
(both default off) opt them back in. Filtering happens in the orchestrator
right after fetch, so excluded repos drop out of the entire report, summary
counts included; when a flag is given, those repos reappear in both the counts
and the tables. (The corresponding `ARCHIVED` / `DISABLED` activity statuses
are therefore only ever seen when the matching flag is set.)

## 12. Error handling

Typed exceptions (`ConfigError`, `GitHubError`) caught at the CLI boundary ->
clean `stderr` message + nonzero exit code. Full tracebacks only under
`--verbose`.

## 13. Testing (pytest, mocked at the source boundary)

- **activity:** each status including archived/disabled precedence and
  `pushed_at = None`.
- **grouping:** override > topic > glob > regex > auto-topic > fallback
  precedence; case-insensitive glob; auto-topic clustering including
  `min_repos` threshold, alphabetical tie-breaking, multi-topic assignment;
  `ignore_topics`.
- **config:** valid load; bad regex fails fast; defaults applied; malformed
  YAML raises `ConfigError`.
- **report:** markdown structure + summary counts on a small fixture; CSV and
  JSON shape (including the flattened nested `repo`).
- **orchestrator:** archived/disabled excluded by default and re-included when
  the matching flag is set (affecting both counts and tables), against a fake
  `GitHubSource`.
- **source:** pagination and token resolution against a fake httpx transport
  (`respx` or stdlib monkeypatch) and mocked `subprocess` — no network.

## 14. Dependencies

- **Runtime:** `httpx`, `pydantic`, `pyyaml`, `click`.
- **Dev/test:** `pytest`, `respx` (or stdlib monkeypatch).
- **Entry point:** `github-summarizer = "lsst.github_summarizer.cli:main"`.
- **CI:** GitHub Actions running `ruff check`, `ruff format --check`, `mypy`,
  and `pytest` on Python 3.13 and 3.14.

## 15. Future extensions (from GOALS, not in scope now)

Commit counts, open issue/PR counts, license, branch protection, CODEOWNERS
presence, CI workflow presence, release recency, ownership/team metadata, repo
health score. The pipeline design makes each an additive change (query field +
`Repository` field + optional column).
