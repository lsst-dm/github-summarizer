# GitHub Organization Summarizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI that scans all repositories in a GitHub organization and renders a markdown/CSV/JSON report of each repo's metadata, activity status, and semantic grouping.

**Architecture:** A one-directional pipeline of pure stages — a swappable `GitHubSource` fetches raw `Repository` records, the orchestrator filters and enriches them into `RepositorySummary` objects (activity + group), and report writers render them. The CLI is a thin Click wrapper. See `docs/superpowers/specs/2026-05-28-github-summarizer-design.md`.

**Tech Stack:** Python 3.13+, Pydantic v2 (models + config), httpx (GraphQL client), PyYAML (config), Click (CLI), pytest (tests, using `httpx.MockTransport` — no network).

**Conventions (must hold after every task):** `ruff check`, `ruff format --check`, and `mypy` all pass. Public classes and module-level functions need numpy-style docstrings (ruff's `D` rules are enforced; `D102`/`D105`/`D107` for methods/magic/`__init__` are ignored). All defs are fully type-annotated (mypy strict for the package).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Dependencies, console-script entry point, pytest config (modify) |
| `python/lsst/github_summarizer/models.py` | `ActivityStatus`, raw `Repository`, derived `RepositorySummary` |
| `python/lsst/github_summarizer/config.py` | Config models + `load_config` + `ConfigError` |
| `python/lsst/github_summarizer/activity.py` | `classify_activity` pure function |
| `python/lsst/github_summarizer/grouping.py` | `Grouper` (override/topic/glob/regex/auto-topic/fallback) |
| `python/lsst/github_summarizer/source.py` | `GitHubSource` protocol, token resolution, `GitHubGraphQLSource`, `GitHubError` |
| `python/lsst/github_summarizer/orchestrator.py` | `build_summaries`: filter + enrich |
| `python/lsst/github_summarizer/report.py` | `render_markdown` / `render_csv` / `render_json` |
| `python/lsst/github_summarizer/cli.py` | Click group `main` + `report` subcommand |
| `tests/test_*.py` | One test module per source module |
| `.github/workflows/ci.yaml` | ruff + mypy + pytest CI |

Tasks are ordered so each builds only on already-completed tasks.

---

## Task 1: Project scaffolding & dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Add dependencies, entry point, and test config to `pyproject.toml`**

Replace the empty `dependencies = []` line with:

```toml
dependencies = [
    "httpx",
    "pydantic",
    "pyyaml",
    "click",
]

[project.optional-dependencies]
test = [
    "pytest",
    "types-pyyaml",
]

[project.scripts]
github-summarizer = "lsst.github_summarizer.cli:main"
```

In the existing `[tool.pytest.ini_options]` section (currently empty), set:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

Add a per-file ignore so test functions are exempt from docstring (`D`) rules
(the project already exempts tests from docstring checks in the numpydoc
config). Add this new section:

```toml
[tool.ruff.lint.per-file-ignores]
"tests/*" = ["D"]
```

- [ ] **Step 2: Write a smoke test**

Create `tests/test_smoke.py`:

```python
"""Smoke test that the package imports."""

import lsst.github_summarizer


def test_package_imports() -> None:
    assert lsst.github_summarizer is not None
```

- [ ] **Step 3: Install the package editable and run the smoke test (expect PASS)**

Run: `pip install -e '.[test]' && pytest tests/test_smoke.py -v`
Expected: PASS.

- [ ] **Step 4: Verify lint/type tooling runs clean**

Run: `ruff check . && ruff format --check . && mypy python`
Expected: all pass (no source files yet beyond the package stub).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_smoke.py
git commit -m "Add dependencies, entry point, and test scaffolding"
```

---

## Task 2: Data models

**Files:**
- Create: `python/lsst/github_summarizer/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
"""Tests for the data models."""

from datetime import UTC, datetime

from lsst.github_summarizer.models import (
    ActivityStatus,
    Repository,
    RepositorySummary,
)


def test_repository_defaults() -> None:
    repo = Repository(name="example", url="https://github.com/lsst/example")
    assert repo.description is None
    assert repo.topics == []
    assert repo.is_archived is False
    assert repo.pushed_at is None


def test_repository_parses_pushed_at_from_iso_string() -> None:
    repo = Repository(
        name="example",
        url="https://example",
        pushed_at="2026-01-02T03:04:05Z",
    )
    assert repo.pushed_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_repository_summary_requires_derived_fields() -> None:
    repo = Repository(name="example", url="https://example")
    summary = RepositorySummary(
        repo=repo,
        activity=ActivityStatus.ACTIVE,
        group="Pipelines",
        grouping_reason="topic:pipelines",
    )
    assert summary.repo.name == "example"
    assert summary.activity is ActivityStatus.ACTIVE
    assert summary.group == "Pipelines"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.github_summarizer.models'`.

- [ ] **Step 3: Write the implementation**

Create `python/lsst/github_summarizer/models.py`:

```python
"""Data models for repositories and their derived summaries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

__all__ = ["ActivityStatus", "Repository", "RepositorySummary"]


class ActivityStatus(StrEnum):
    """Activity classification for a repository."""

    ACTIVE = "active"
    WARM = "warm"
    QUIET = "quiet"
    DORMANT = "dormant"
    ARCHIVED = "archived"
    DISABLED = "disabled"


class Repository(BaseModel):
    """Raw repository record as produced by a source. No derived fields."""

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

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && ruff format --check . && mypy python
git add python/lsst/github_summarizer/models.py tests/test_models.py
git commit -m "Add Repository and RepositorySummary models"
```

---

## Task 3: Configuration models and loader

**Files:**
- Create: `python/lsst/github_summarizer/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
"""Tests for config loading and validation."""

from pathlib import Path

import pytest

from lsst.github_summarizer.config import ConfigError, load_config


def test_load_minimal_config_applies_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("org: lsst\n")

    config = load_config(path)

    assert config.org == "lsst"
    assert config.activity.active_days == 90
    assert config.activity.warm_days == 180
    assert config.activity.quiet_days == 365
    assert config.fallback_group == "Uncategorized"
    assert config.auto_group_by_topic.enabled is True
    assert config.auto_group_by_topic.min_repos == 3


def test_load_full_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
org: lsst
groups:
  - name: Pipelines
    topics: [pipelines]
    description: Coordinated pipelines
  - name: Tech Notes
    glob: ["DMTN-*"]
    regex: ["^SQR-[0-9]+$"]
overrides:
  special-repo:
    group: Pipelines
    notes: manual
"""
    )

    config = load_config(path)

    assert config.groups[0].name == "Pipelines"
    assert config.groups[0].topics == ["pipelines"]
    assert config.groups[1].glob == ["DMTN-*"]
    assert config.overrides["special-repo"].group == "Pipelines"


def test_invalid_regex_fails_fast(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
org: lsst
groups:
  - name: Bad
    regex: ["[unterminated"]
"""
    )

    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_org_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("groups: []\n")

    with pytest.raises(ConfigError):
        load_config(path)


def test_malformed_yaml_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("org: [unclosed\n")

    with pytest.raises(ConfigError):
        load_config(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `python/lsst/github_summarizer/config.py`:

```python
"""Configuration models and YAML loader."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator

__all__ = [
    "ActivityConfig",
    "AutoGroupConfig",
    "Config",
    "ConfigError",
    "GroupRule",
    "Override",
    "load_config",
]


class ConfigError(Exception):
    """Raised when configuration cannot be loaded or validated."""


class ActivityConfig(BaseModel):
    """Day thresholds for activity classification."""

    active_days: int = 90
    warm_days: int = 180
    quiet_days: int = 365


class GroupRule(BaseModel):
    """A semantic grouping rule matched by topic, glob, or regex."""

    name: str
    topics: list[str] = []
    glob: list[str] = []
    regex: list[str] = []
    description: str | None = None

    @field_validator("regex")
    @classmethod
    def _validate_regex(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return value


class Override(BaseModel):
    """A manual group assignment for a specific repository name."""

    group: str
    notes: str | None = None


class AutoGroupConfig(BaseModel):
    """Settings for clustering leftover repos by shared topic."""

    enabled: bool = True
    min_repos: int = 3
    ignore_topics: list[str] = []


class Config(BaseModel):
    """Top-level configuration for a report run."""

    org: str
    activity: ActivityConfig = ActivityConfig()
    groups: list[GroupRule] = []
    overrides: dict[str, Override] = {}
    auto_group_by_topic: AutoGroupConfig = AutoGroupConfig()
    fallback_group: str = "Uncategorized"


def load_config(path: Path) -> Config:
    """Load and validate a YAML configuration file.

    Parameters
    ----------
    path : `pathlib.Path`
        Path to the YAML configuration file.

    Returns
    -------
    config : `Config`
        The validated configuration.

    Raises
    ------
    ConfigError
        Raised if the file cannot be read, is not valid YAML, is not a
        mapping, or fails schema validation.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"config {path} must be a mapping")

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid config {path}: {exc}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && ruff format --check . && mypy python
git add python/lsst/github_summarizer/config.py tests/test_config.py
git commit -m "Add configuration models and YAML loader"
```

---

## Task 4: Activity classification

**Files:**
- Create: `python/lsst/github_summarizer/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_activity.py`:

```python
"""Tests for activity classification."""

from datetime import UTC, datetime, timedelta

from lsst.github_summarizer.activity import classify_activity
from lsst.github_summarizer.config import ActivityConfig
from lsst.github_summarizer.models import ActivityStatus, Repository

NOW = datetime(2026, 6, 1, tzinfo=UTC)
CFG = ActivityConfig()


def _repo(**kwargs: object) -> Repository:
    base: dict[str, object] = {"name": "r", "url": "https://r"}
    base.update(kwargs)
    return Repository(**base)  # type: ignore[arg-type]


def test_disabled_takes_precedence_over_recent_push() -> None:
    repo = _repo(is_disabled=True, is_archived=True, pushed_at=NOW)
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.DISABLED


def test_archived_takes_precedence_over_recent_push() -> None:
    repo = _repo(is_archived=True, pushed_at=NOW)
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.ARCHIVED


def test_active_within_threshold() -> None:
    repo = _repo(pushed_at=NOW - timedelta(days=10))
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.ACTIVE


def test_warm_between_active_and_warm_thresholds() -> None:
    repo = _repo(pushed_at=NOW - timedelta(days=120))
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.WARM


def test_quiet_between_warm_and_quiet_thresholds() -> None:
    repo = _repo(pushed_at=NOW - timedelta(days=300))
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.QUIET


def test_dormant_beyond_quiet_threshold() -> None:
    repo = _repo(pushed_at=NOW - timedelta(days=400))
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.DORMANT


def test_no_push_date_is_dormant() -> None:
    repo = _repo(pushed_at=None)
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.DORMANT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_activity.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `python/lsst/github_summarizer/activity.py`:

```python
"""Activity status classification from a repository's last push date."""

from __future__ import annotations

from datetime import datetime

from .config import ActivityConfig
from .models import ActivityStatus, Repository

__all__ = ["classify_activity"]


def classify_activity(
    repo: Repository, cfg: ActivityConfig, *, now: datetime
) -> ActivityStatus:
    """Classify a repository's activity status.

    Disabled and archived states take precedence over the push-date based
    classification.

    Parameters
    ----------
    repo : `Repository`
        The repository to classify.
    cfg : `ActivityConfig`
        Day thresholds for the date-based classification.
    now : `datetime.datetime`
        The reference time-zone-aware timestamp to measure age against.

    Returns
    -------
    status : `ActivityStatus`
        The computed activity status.
    """
    if repo.is_disabled:
        return ActivityStatus.DISABLED
    if repo.is_archived:
        return ActivityStatus.ARCHIVED
    if repo.pushed_at is None:
        return ActivityStatus.DORMANT

    age_days = (now - repo.pushed_at).days
    if age_days <= cfg.active_days:
        return ActivityStatus.ACTIVE
    if age_days <= cfg.warm_days:
        return ActivityStatus.WARM
    if age_days <= cfg.quiet_days:
        return ActivityStatus.QUIET
    return ActivityStatus.DORMANT
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_activity.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && ruff format --check . && mypy python
git add python/lsst/github_summarizer/activity.py tests/test_activity.py
git commit -m "Add activity classification"
```

---

## Task 5: Semantic grouping

**Files:**
- Create: `python/lsst/github_summarizer/grouping.py`
- Test: `tests/test_grouping.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_grouping.py`:

```python
"""Tests for semantic grouping precedence and auto-topic clustering."""

from lsst.github_summarizer.config import (
    AutoGroupConfig,
    Config,
    GroupRule,
    Override,
)
from lsst.github_summarizer.grouping import Grouper
from lsst.github_summarizer.models import Repository


def _repo(name: str, topics: list[str] | None = None) -> Repository:
    return Repository(name=name, url=f"https://github.com/lsst/{name}", topics=topics or [])


def test_override_wins_over_everything() -> None:
    config = Config(
        org="lsst",
        groups=[GroupRule(name="Pipelines", topics=["pipelines"])],
        overrides={"afw": Override(group="Special")},
    )
    repo = _repo("afw", topics=["pipelines"])
    result = Grouper(config).assign([repo])
    assert result["afw"] == ("Special", "override")


def test_topic_beats_glob() -> None:
    config = Config(
        org="lsst",
        groups=[
            GroupRule(name="Pipelines", topics=["pipelines"]),
            GroupRule(name="Tech Notes", glob=["afw*"]),
        ],
    )
    repo = _repo("afw", topics=["pipelines"])
    result = Grouper(config).assign([repo])
    assert result["afw"] == ("Pipelines", "topic:pipelines")


def test_glob_is_case_insensitive() -> None:
    config = Config(org="lsst", groups=[GroupRule(name="Tech Notes", glob=["DMTN-*"])])
    repo = _repo("dmtn-123")
    result = Grouper(config).assign([repo])
    assert result["dmtn-123"] == ("Tech Notes", "glob:DMTN-*")


def test_regex_matches_when_no_glob() -> None:
    config = Config(org="lsst", groups=[GroupRule(name="SQR", regex=["^SQR-[0-9]+$"])])
    repo = _repo("SQR-42")
    result = Grouper(config).assign([repo])
    assert result["SQR-42"] == ("SQR", "regex:^SQR-[0-9]+$")


def test_fallback_when_no_rule_matches() -> None:
    config = Config(org="lsst", fallback_group="Uncategorized")
    repo = _repo("random")
    result = Grouper(config).assign([repo])
    assert result["random"] == ("Uncategorized", "fallback")


def test_auto_topic_clusters_when_min_repos_met() -> None:
    config = Config(
        org="lsst",
        auto_group_by_topic=AutoGroupConfig(enabled=True, min_repos=3),
    )
    repos = [_repo(f"r{i}", topics=["qserv"]) for i in range(3)]
    result = Grouper(config).assign(repos)
    assert all(result[r.name] == ("qserv", "auto-topic:qserv") for r in repos)


def test_auto_topic_skipped_below_min_repos() -> None:
    config = Config(
        org="lsst",
        auto_group_by_topic=AutoGroupConfig(enabled=True, min_repos=3),
        fallback_group="Uncategorized",
    )
    repos = [_repo("r0", topics=["qserv"]), _repo("r1", topics=["qserv"])]
    result = Grouper(config).assign(repos)
    assert result["r0"] == ("Uncategorized", "fallback")


def test_auto_topic_disabled_falls_back() -> None:
    config = Config(
        org="lsst",
        auto_group_by_topic=AutoGroupConfig(enabled=False),
        fallback_group="Uncategorized",
    )
    repos = [_repo(f"r{i}", topics=["qserv"]) for i in range(5)]
    result = Grouper(config).assign(repos)
    assert all(result[r.name] == ("Uncategorized", "fallback") for r in repos)


def test_auto_topic_ignores_listed_topics() -> None:
    config = Config(
        org="lsst",
        auto_group_by_topic=AutoGroupConfig(
            enabled=True, min_repos=3, ignore_topics=["lsst"]
        ),
        fallback_group="Uncategorized",
    )
    repos = [_repo(f"r{i}", topics=["lsst"]) for i in range(5)]
    result = Grouper(config).assign(repos)
    assert all(result[r.name] == ("Uncategorized", "fallback") for r in repos)


def test_auto_topic_largest_cluster_wins_for_multi_topic_repo() -> None:
    # 'shared' covers 4 repos, 'small' covers 3 (including the overlap repo).
    # The overlap repo must land in the larger 'shared' cluster.
    config = Config(
        org="lsst", auto_group_by_topic=AutoGroupConfig(enabled=True, min_repos=3)
    )
    repos = [
        _repo("a", topics=["shared"]),
        _repo("b", topics=["shared"]),
        _repo("c", topics=["shared"]),
        _repo("d", topics=["shared", "small"]),
        _repo("e", topics=["small"]),
        _repo("f", topics=["small"]),
    ]
    result = Grouper(config).assign(repos)
    assert result["d"] == ("shared", "auto-topic:shared")
    assert result["e"] == ("small", "auto-topic:small")


def test_auto_topic_ties_broken_alphabetically() -> None:
    # 'alpha' and 'beta' each cover exactly 3 distinct repos. 'alpha' wins.
    config = Config(
        org="lsst", auto_group_by_topic=AutoGroupConfig(enabled=True, min_repos=3)
    )
    repos = [
        _repo("a", topics=["beta"]),
        _repo("b", topics=["beta"]),
        _repo("c", topics=["beta"]),
        _repo("d", topics=["alpha"]),
        _repo("e", topics=["alpha"]),
        _repo("f", topics=["alpha"]),
    ]
    result = Grouper(config).assign(repos)
    assert result["d"] == ("alpha", "auto-topic:alpha")
    assert result["a"] == ("beta", "auto-topic:beta")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_grouping.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `python/lsst/github_summarizer/grouping.py`:

```python
"""Semantic grouping of repositories by configured rules."""

from __future__ import annotations

import fnmatch
import re

from .config import Config
from .models import Repository

__all__ = ["Grouper"]

# A (group_name, grouping_reason) pair.
Assignment = tuple[str, str]


class Grouper:
    """Assign repositories to semantic groups using configured rules."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def assign(self, repos: list[Repository]) -> dict[str, Assignment]:
        """Assign every repository to a group.

        Parameters
        ----------
        repos : `list` [ `Repository` ]
            All repositories to classify. The full list is required because
            the auto-topic pass clusters across the ungrouped remainder.

        Returns
        -------
        assignments : `dict` [ `str`, `tuple` [ `str`, `str` ] ]
            Mapping of repository name to ``(group, grouping_reason)``.
        """
        result: dict[str, Assignment] = {}
        ungrouped: list[Repository] = []
        for repo in repos:
            explicit = self._match_explicit(repo)
            if explicit is not None:
                result[repo.name] = explicit
            else:
                ungrouped.append(repo)

        if self._config.auto_group_by_topic.enabled:
            self._assign_auto_topic(ungrouped, result)

        fallback = self._config.fallback_group
        for repo in ungrouped:
            if repo.name not in result:
                result[repo.name] = (fallback, "fallback")
        return result

    def _match_explicit(self, repo: Repository) -> Assignment | None:
        """Match override, then topic, then glob, then regex (in that order)."""
        config = self._config
        if repo.name in config.overrides:
            return (config.overrides[repo.name].group, "override")

        for rule in config.groups:
            for topic in rule.topics:
                if topic in repo.topics:
                    return (rule.name, f"topic:{topic}")

        for rule in config.groups:
            for pattern in rule.glob:
                if fnmatch.fnmatchcase(repo.name.lower(), pattern.lower()):
                    return (rule.name, f"glob:{pattern}")

        for rule in config.groups:
            for pattern in rule.regex:
                if re.search(pattern, repo.name):
                    return (rule.name, f"regex:{pattern}")

        return None

    def _assign_auto_topic(
        self, ungrouped: list[Repository], result: dict[str, Assignment]
    ) -> None:
        """Cluster ungrouped repos by their most-shared topic, greedily."""
        cfg = self._config.auto_group_by_topic
        ignore = set(cfg.ignore_topics)
        pool: dict[str, Repository] = {r.name: r for r in ungrouped}

        while True:
            index: dict[str, set[str]] = {}
            for name, repo in pool.items():
                for topic in repo.topics:
                    if topic in ignore:
                        continue
                    index.setdefault(topic, set()).add(name)
            if not index:
                break

            # Largest cluster wins; ties broken alphabetically by topic.
            topic = min(index, key=lambda t: (-len(index[t]), t))
            names = index[topic]
            if len(names) < cfg.min_repos:
                break

            for name in names:
                result[name] = (topic, f"auto-topic:{topic}")
                del pool[name]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_grouping.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && ruff format --check . && mypy python
git add python/lsst/github_summarizer/grouping.py tests/test_grouping.py
git commit -m "Add semantic grouping with auto-topic clustering"
```

---

## Task 6: Orchestrator (filter + enrich)

**Files:**
- Create: `python/lsst/github_summarizer/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator.py`:

```python
"""Tests for the orchestrator's filtering and enrichment."""

from datetime import UTC, datetime

from lsst.github_summarizer.config import Config
from lsst.github_summarizer.models import ActivityStatus, Repository
from lsst.github_summarizer.orchestrator import build_summaries

NOW = datetime(2026, 6, 1, tzinfo=UTC)


class FakeSource:
    """A GitHubSource that returns a fixed list of repositories."""

    def __init__(self, repos: list[Repository]) -> None:
        self._repos = repos

    def fetch_repositories(self, org: str) -> list[Repository]:
        return self._repos


def _config() -> Config:
    return Config(org="lsst")


def test_archived_and_disabled_excluded_by_default() -> None:
    source = FakeSource(
        [
            Repository(name="live", url="https://x", pushed_at=NOW),
            Repository(name="old", url="https://x", is_archived=True),
            Repository(name="dead", url="https://x", is_disabled=True),
        ]
    )
    summaries = build_summaries(source, _config(), now=NOW)
    names = {s.repo.name for s in summaries}
    assert names == {"live"}


def test_include_archived_flag_reincludes_them() -> None:
    source = FakeSource(
        [
            Repository(name="live", url="https://x", pushed_at=NOW),
            Repository(name="old", url="https://x", is_archived=True),
        ]
    )
    summaries = build_summaries(source, _config(), now=NOW, include_archived=True)
    names = {s.repo.name for s in summaries}
    assert names == {"live", "old"}
    archived = next(s for s in summaries if s.repo.name == "old")
    assert archived.activity is ActivityStatus.ARCHIVED


def test_include_disabled_flag_reincludes_them() -> None:
    source = FakeSource(
        [
            Repository(name="live", url="https://x", pushed_at=NOW),
            Repository(name="dead", url="https://x", is_disabled=True),
        ]
    )
    summaries = build_summaries(source, _config(), now=NOW, include_disabled=True)
    names = {s.repo.name for s in summaries}
    assert names == {"live", "dead"}


def test_summaries_carry_activity_and_group() -> None:
    source = FakeSource([Repository(name="live", url="https://x", pushed_at=NOW)])
    summaries = build_summaries(source, _config(), now=NOW)
    assert summaries[0].activity is ActivityStatus.ACTIVE
    assert summaries[0].group == "Uncategorized"
    assert summaries[0].grouping_reason == "fallback"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `python/lsst/github_summarizer/orchestrator.py`:

```python
"""Wire the source, filtering, and enrichment stages together."""

from __future__ import annotations

from datetime import datetime

from .activity import classify_activity
from .config import Config
from .grouping import Grouper
from .models import Repository, RepositorySummary
from .source import GitHubSource

__all__ = ["build_summaries"]


def build_summaries(
    source: GitHubSource,
    config: Config,
    *,
    now: datetime,
    include_archived: bool = False,
    include_disabled: bool = False,
) -> list[RepositorySummary]:
    """Fetch, filter, and enrich repositories into summaries.

    Archived and disabled repositories are dropped unless the corresponding
    flag is set, so excluded repositories leave the report entirely (counts
    included).

    Parameters
    ----------
    source : `GitHubSource`
        The repository source to fetch from.
    config : `Config`
        The validated configuration (its ``org`` drives the fetch).
    now : `datetime.datetime`
        Reference timestamp for activity classification.
    include_archived : `bool`, optional
        Keep archived repositories. Defaults to `False`.
    include_disabled : `bool`, optional
        Keep disabled repositories. Defaults to `False`.

    Returns
    -------
    summaries : `list` [ `RepositorySummary` ]
        Enriched repository summaries.
    """
    repos = source.fetch_repositories(config.org)
    repos = [
        repo
        for repo in repos
        if (include_archived or not repo.is_archived)
        and (include_disabled or not repo.is_disabled)
    ]

    assignments = Grouper(config).assign(repos)

    summaries: list[RepositorySummary] = []
    for repo in repos:
        group, reason = assignments[repo.name]
        summaries.append(
            RepositorySummary(
                repo=repo,
                activity=classify_activity(repo, config.activity, now=now),
                group=group,
                grouping_reason=reason,
            )
        )
    return summaries
```

> Note: this imports `GitHubSource` from `source.py`, created in Task 7. Implement Task 7 before running this task's tests, **or** temporarily run only `mypy` after Task 7. To keep tasks runnable in order, swap Task 6 and Task 7 if you prefer; the test here only needs the `GitHubSource` protocol to exist. The recommended order is to do Task 7's `GitHubSource` protocol + token resolver first.

- [ ] **Step 4: Run test to verify it passes** (after Task 7 exists)

Run: `pytest tests/test_orchestrator.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && ruff format --check . && mypy python
git add python/lsst/github_summarizer/orchestrator.py tests/test_orchestrator.py
git commit -m "Add orchestrator with archived/disabled filtering"
```

---

## Task 7: GitHub source — protocol, token resolution, GraphQL client

> Implement this task's **protocol and token resolver first** (Steps 1–6), since Task 6 imports `GitHubSource`. The GraphQL client (Steps 7–10) can follow.

**Files:**
- Create: `python/lsst/github_summarizer/source.py`
- Test: `tests/test_source.py`

- [ ] **Step 1: Write the failing test for the protocol and token resolution**

Create `tests/test_source.py`:

```python
"""Tests for token resolution and the GraphQL source."""

from collections.abc import Callable

import httpx
import pytest

from lsst.github_summarizer.source import (
    GitHubError,
    GitHubGraphQLSource,
    resolve_token,
)


def test_explicit_token_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert resolve_token("explicit", resolvers=[]) == "explicit"


def test_env_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert resolve_token(None) == "env-token"


def test_env_gh_token_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-env-token")
    assert resolve_token(None) == "gh-env-token"


def test_custom_resolver_chain_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert resolve_token(None, resolvers=[lambda: None, lambda: "from-chain"]) == "from-chain"


def test_no_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(GitHubError):
        resolve_token(None, resolvers=[lambda: None])


def _paged_handler() -> Callable[[httpx.Request], httpx.Response]:
    pages = [
        {
            "data": {
                "organization": {
                    "repositories": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "C1"},
                        "nodes": [
                            {
                                "name": "afw",
                                "url": "https://github.com/lsst/afw",
                                "description": "framework",
                                "isArchived": False,
                                "isDisabled": False,
                                "primaryLanguage": {"name": "C++"},
                                "pushedAt": "2026-01-01T00:00:00Z",
                                "repositoryTopics": {
                                    "nodes": [{"topic": {"name": "pipelines"}}]
                                },
                                "defaultBranchRef": {"name": "main"},
                            }
                        ],
                    }
                }
            }
        },
        {
            "data": {
                "organization": {
                    "repositories": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "name": "dmtn-001",
                                "url": "https://github.com/lsst/dmtn-001",
                                "description": None,
                                "isArchived": False,
                                "isDisabled": False,
                                "primaryLanguage": None,
                                "pushedAt": None,
                                "repositoryTopics": {"nodes": []},
                                "defaultBranchRef": None,
                            }
                        ],
                    }
                }
            }
        },
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    return handler


def test_fetch_repositories_paginates() -> None:
    client = httpx.Client(transport=httpx.MockTransport(_paged_handler()))
    source = GitHubGraphQLSource("token", client=client)
    repos = source.fetch_repositories("lsst")
    assert [r.name for r in repos] == ["afw", "dmtn-001"]
    assert repos[0].primary_language == "C++"
    assert repos[0].topics == ["pipelines"]
    assert repos[0].default_branch == "main"
    assert repos[1].primary_language is None
    assert repos[1].pushed_at is None


def test_graphql_errors_raise_github_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "boom"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = GitHubGraphQLSource("token", client=client)
    with pytest.raises(GitHubError):
        source.fetch_repositories("lsst")


def test_rate_limit_is_retried() -> None:
    responses = [
        httpx.Response(403, headers={"Retry-After": "0"}, json={}),
        httpx.Response(
            200,
            json={
                "data": {
                    "organization": {
                        "repositories": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [],
                        }
                    }
                }
            },
        ),
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        resp = responses[calls["n"]]
        calls["n"] += 1
        return resp

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = GitHubGraphQLSource("token", client=client, sleep=lambda _s: None)
    repos = source.fetch_repositories("lsst")
    assert repos == []
    assert calls["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_source.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `python/lsst/github_summarizer/source.py`:

```python
"""GitHub repository source: protocol, token resolution, and GraphQL client."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from typing import Protocol

import httpx

from .models import Repository

__all__ = [
    "GitHubError",
    "GitHubGraphQLSource",
    "GitHubSource",
    "resolve_token",
]

_GRAPHQL_QUERY = """
query($org: String!, $cursor: String) {
  organization(login: $org) {
    repositories(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        url
        description
        isArchived
        isDisabled
        primaryLanguage { name }
        pushedAt
        repositoryTopics(first: 50) { nodes { topic { name } } }
        defaultBranchRef { name }
      }
    }
  }
}
"""

_DEFAULT_BASE_URL = "https://api.github.com/graphql"
_MAX_RETRIES = 5


class GitHubError(Exception):
    """Raised when the GitHub source cannot produce data."""


class GitHubSource(Protocol):
    """A source of raw repository records for an organization."""

    def fetch_repositories(self, org: str) -> list[Repository]:
        """Return all repositories for the named organization."""
        ...


def _token_from_env() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def _token_from_gh() -> str | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    token = result.stdout.strip()
    return token or None


def _token_from_git_credential() -> str | None:
    request = "protocol=https\nhost=github.com\n\n"
    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input=request,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in result.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password=") :] or None
    return None


_DEFAULT_RESOLVERS: list[Callable[[], str | None]] = [
    _token_from_gh,
    _token_from_git_credential,
]


def resolve_token(
    explicit: str | None,
    *,
    resolvers: list[Callable[[], str | None]] | None = None,
) -> str:
    """Resolve a GitHub token from the flag, environment, then discovery.

    Order: explicit flag, ``GITHUB_TOKEN``/``GH_TOKEN`` env, then each
    resolver in turn (``gh auth token`` then ``git credential fill`` by
    default).

    Parameters
    ----------
    explicit : `str` or `None`
        A token passed explicitly (e.g. via ``--token``).
    resolvers : `list` [ `~collections.abc.Callable` ], optional
        Discovery callables tried after the environment. Defaults to the
        ``gh`` then ``git credential`` chain.

    Returns
    -------
    token : `str`
        The resolved token.

    Raises
    ------
    GitHubError
        Raised if no token can be found.
    """
    if explicit:
        return explicit

    env_token = _token_from_env()
    if env_token:
        return env_token

    chain = _DEFAULT_RESOLVERS if resolvers is None else resolvers
    for resolver in chain:
        token = resolver()
        if token:
            return token

    raise GitHubError(
        "no GitHub token found (tried --token, GITHUB_TOKEN/GH_TOKEN, "
        "gh auth token, git credential)"
    )


class GitHubGraphQLSource:
    """Fetch repositories via the GitHub GraphQL API."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._client = client or httpx.Client()
        self._sleep = sleep

    def fetch_repositories(self, org: str) -> list[Repository]:
        """Fetch all repositories for an organization, following pagination.

        Parameters
        ----------
        org : `str`
            The organization login.

        Returns
        -------
        repos : `list` [ `Repository` ]
            All repositories in the organization.

        Raises
        ------
        GitHubError
            Raised on transport failure, GraphQL errors, or persistent
            rate limiting.
        """
        repos: list[Repository] = []
        cursor: str | None = None
        while True:
            data = self._post({"org": org, "cursor": cursor})
            connection = data["organization"]["repositories"]
            for node in connection["nodes"]:
                repos.append(self._parse_node(node))
            page_info = connection["pageInfo"]
            if page_info["hasNextPage"]:
                cursor = page_info["endCursor"]
            else:
                return repos

    def _post(self, variables: dict[str, str | None]) -> dict:
        """POST the query, retrying on rate-limit responses."""
        headers = {"Authorization": f"Bearer {self._token}"}
        payload = {"query": _GRAPHQL_QUERY, "variables": variables}
        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.post(
                    self._base_url, json=payload, headers=headers
                )
            except httpx.HTTPError as exc:
                raise GitHubError(f"request to GitHub failed: {exc}") from exc

            if response.status_code == 403 and attempt < _MAX_RETRIES - 1:
                self._sleep(_retry_after_seconds(response))
                continue

            if response.status_code != 200:
                raise GitHubError(
                    f"GitHub returned HTTP {response.status_code}: {response.text}"
                )

            body = response.json()
            if body.get("errors"):
                raise GitHubError(f"GraphQL errors: {body['errors']}")
            return body["data"]

        raise GitHubError("exceeded retry limit due to rate limiting")

    @staticmethod
    def _parse_node(node: dict) -> Repository:
        """Convert a GraphQL repository node into a `Repository`."""
        language = node.get("primaryLanguage")
        branch = node.get("defaultBranchRef")
        topics = [
            entry["topic"]["name"]
            for entry in node["repositoryTopics"]["nodes"]
        ]
        return Repository(
            name=node["name"],
            url=node["url"],
            description=node.get("description"),
            primary_language=language["name"] if language else None,
            topics=topics,
            pushed_at=node.get("pushedAt"),
            is_archived=node["isArchived"],
            is_disabled=node["isDisabled"],
            default_branch=branch["name"] if branch else None,
        )


def _retry_after_seconds(response: httpx.Response) -> float:
    """Compute a backoff delay from rate-limit headers, capped at 60s."""
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return min(float(retry_after), 60.0)
        except ValueError:
            pass
    return 1.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_source.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the orchestrator tests now that the protocol exists**

Run: `pytest tests/test_orchestrator.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Lint, type-check, commit**

```bash
ruff check . && ruff format --check . && mypy python
git add python/lsst/github_summarizer/source.py tests/test_source.py
git commit -m "Add GitHub GraphQL source and token resolution"
```

---

## Task 8: Report writers

**Files:**
- Create: `python/lsst/github_summarizer/report.py`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report.py`:

```python
"""Tests for the report writers."""

import csv
import io
import json
from datetime import UTC, datetime

from lsst.github_summarizer.config import Config, GroupRule
from lsst.github_summarizer.models import ActivityStatus, Repository, RepositorySummary
from lsst.github_summarizer.report import render_csv, render_json, render_markdown

GENERATED = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _summary(
    name: str, group: str, activity: ActivityStatus, topics: list[str] | None = None
) -> RepositorySummary:
    return RepositorySummary(
        repo=Repository(
            name=name,
            url=f"https://github.com/lsst/{name}",
            description=f"{name} desc",
            primary_language="Python",
            topics=topics or [],
            pushed_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        activity=activity,
        group=group,
        grouping_reason="fallback",
    )


def _config() -> Config:
    return Config(
        org="lsst",
        groups=[GroupRule(name="Pipelines", description="Coordinated pipelines")],
    )


def _summaries() -> list[RepositorySummary]:
    return [
        _summary("afw", "Pipelines", ActivityStatus.ACTIVE, ["pipelines"]),
        _summary("old-thing", "Uncategorized", ActivityStatus.DORMANT),
    ]


def test_render_json_has_summary_and_repos() -> None:
    output = render_json(_summaries(), _config(), GENERATED)
    data = json.loads(output)
    assert data["org"] == "lsst"
    assert data["summary"]["total"] == 2
    assert data["summary"]["active"] == 1
    assert data["summary"]["dormant"] == 1
    assert {r["repo"]["name"] for r in data["repositories"]} == {"afw", "old-thing"}


def test_render_csv_flattens_repo_fields() -> None:
    output = render_csv(_summaries(), _config(), GENERATED)
    rows = list(csv.DictReader(io.StringIO(output)))
    assert rows[0]["name"] == "afw"
    assert rows[0]["topics"] == "pipelines"
    assert rows[0]["activity"] == "active"
    assert rows[0]["group"] == "Pipelines"


def test_render_markdown_has_title_summary_and_groups() -> None:
    output = render_markdown(_summaries(), _config(), GENERATED)
    assert "# GitHub Repository Report: lsst" in output
    assert "Total repositories: 2" in output
    assert "Active: 1" in output
    assert "## Pipelines" in output
    assert "Coordinated pipelines" in output
    assert "## Uncategorized" in output
    assert "afw" in output


def test_render_markdown_appendix_optional() -> None:
    without = render_markdown(_summaries(), _config(), GENERATED)
    assert "Appendix" not in without
    with_appendix = render_markdown(
        _summaries(), _config(), GENERATED, include_appendix=True
    )
    assert "Appendix" in with_appendix


def test_render_markdown_groups_sorted_fallback_last() -> None:
    output = render_markdown(_summaries(), _config(), GENERATED)
    assert output.index("## Pipelines") < output.index("## Uncategorized")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `python/lsst/github_summarizer/report.py`:

```python
"""Render repository summaries to markdown, CSV, and JSON."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from .config import Config
from .models import ActivityStatus, RepositorySummary

__all__ = ["render_csv", "render_json", "render_markdown"]

_CSV_COLUMNS = [
    "name",
    "url",
    "description",
    "primary_language",
    "topics",
    "pushed_at",
    "is_archived",
    "is_disabled",
    "default_branch",
    "activity",
    "group",
    "grouping_reason",
]

_TABLE_HEADER = (
    "| Repo | Description | Language | Topics | Last push | Activity | "
    "Archived | Disabled | Reason |"
)
_TABLE_DIVIDER = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"


def _summary_counts(summaries: list[RepositorySummary]) -> dict[ActivityStatus, int]:
    counts = {status: 0 for status in ActivityStatus}
    for summary in summaries:
        counts[summary.activity] += 1
    return counts


def render_json(
    summaries: list[RepositorySummary], config: Config, generated_at: datetime
) -> str:
    """Render summaries as a JSON document.

    Parameters
    ----------
    summaries : `list` [ `RepositorySummary` ]
        The enriched repositories.
    config : `Config`
        The configuration (used for the org name).
    generated_at : `datetime.datetime`
        Report generation timestamp.

    Returns
    -------
    text : `str`
        The JSON document.
    """
    counts = _summary_counts(summaries)
    payload = {
        "generated_at": generated_at.isoformat(),
        "org": config.org,
        "summary": {
            "total": len(summaries),
            **{status.value: counts[status] for status in ActivityStatus},
        },
        "repositories": [summary.model_dump(mode="json") for summary in summaries],
    }
    return json.dumps(payload, indent=2)


def render_csv(
    summaries: list[RepositorySummary], config: Config, generated_at: datetime
) -> str:
    """Render summaries as CSV, one row per repository.

    Parameters
    ----------
    summaries : `list` [ `RepositorySummary` ]
        The enriched repositories.
    config : `Config`
        Unused; present for a uniform writer signature.
    generated_at : `datetime.datetime`
        Unused; present for a uniform writer signature.

    Returns
    -------
    text : `str`
        The CSV document.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_CSV_COLUMNS)
    for summary in summaries:
        repo = summary.repo
        writer.writerow(
            [
                repo.name,
                repo.url,
                repo.description or "",
                repo.primary_language or "",
                ";".join(repo.topics),
                repo.pushed_at.isoformat() if repo.pushed_at else "",
                repo.is_archived,
                repo.is_disabled,
                repo.default_branch or "",
                summary.activity.value,
                summary.group,
                summary.grouping_reason,
            ]
        )
    return output.getvalue()


def render_markdown(
    summaries: list[RepositorySummary],
    config: Config,
    generated_at: datetime,
    *,
    include_appendix: bool = False,
) -> str:
    """Render summaries as a grouped markdown report.

    Parameters
    ----------
    summaries : `list` [ `RepositorySummary` ]
        The enriched repositories.
    config : `Config`
        Configuration providing group descriptions and the fallback group.
    generated_at : `datetime.datetime`
        Report generation timestamp.
    include_appendix : `bool`, optional
        Append a full raw-inventory table. Defaults to `False`.

    Returns
    -------
    text : `str`
        The markdown document.
    """
    descriptions = {rule.name: rule.description for rule in config.groups}
    by_group: dict[str, list[RepositorySummary]] = {}
    for summary in summaries:
        by_group.setdefault(summary.group, []).append(summary)

    lines: list[str] = [
        f"# GitHub Repository Report: {config.org}",
        "",
        f"_Generated {generated_at.isoformat()}_",
        "",
        "## Summary",
        "",
        f"- Total repositories: {len(summaries)}",
    ]
    counts = _summary_counts(summaries)
    for status in ActivityStatus:
        lines.append(f"- {status.value.capitalize()}: {counts[status]}")
    lines.append("")

    fallback = config.fallback_group
    ordered = sorted(name for name in by_group if name != fallback)
    for name in ordered:
        _render_group(lines, name, by_group[name], descriptions.get(name))
    if fallback in by_group:
        _render_group(lines, fallback, by_group[fallback], descriptions.get(fallback))

    if include_appendix:
        lines.append("## Appendix: Full inventory")
        lines.append("")
        _render_table(lines, summaries)

    return "\n".join(lines) + "\n"


def _render_group(
    lines: list[str],
    name: str,
    summaries: list[RepositorySummary],
    description: str | None,
) -> None:
    lines.append(f"## {name}")
    lines.append("")
    if description:
        lines.append(description)
        lines.append("")
    lines.append(f"{len(summaries)} repositories.")
    lines.append("")
    _render_table(lines, summaries)


def _render_table(lines: list[str], summaries: list[RepositorySummary]) -> None:
    lines.append(_TABLE_HEADER)
    lines.append(_TABLE_DIVIDER)
    for summary in sorted(summaries, key=lambda s: s.repo.name):
        repo = summary.repo
        last_push = repo.pushed_at.date().isoformat() if repo.pushed_at else ""
        lines.append(
            f"| [{repo.name}]({repo.url}) "
            f"| {repo.description or ''} "
            f"| {repo.primary_language or ''} "
            f"| {', '.join(repo.topics)} "
            f"| {last_push} "
            f"| {summary.activity.value} "
            f"| {'yes' if repo.is_archived else 'no'} "
            f"| {'yes' if repo.is_disabled else 'no'} "
            f"| {summary.grouping_reason} |"
        )
    lines.append("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_report.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && ruff format --check . && mypy python
git add python/lsst/github_summarizer/report.py tests/test_report.py
git commit -m "Add markdown, CSV, and JSON report writers"
```

---

## Task 9: CLI

**Files:**
- Create: `python/lsst/github_summarizer/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
"""Tests for the Click CLI wiring."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from lsst.github_summarizer import cli
from lsst.github_summarizer.models import Repository


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("org: lsst\n")
    return path


def _patch_backend(monkeypatch: pytest.MonkeyPatch, repos: list[Repository]) -> None:
    monkeypatch.setattr(cli, "resolve_token", lambda token: "fake-token")
    monkeypatch.setattr(
        cli.GitHubGraphQLSource,
        "fetch_repositories",
        lambda self, org: repos,
    )


def test_report_json_to_stdout(
    monkeypatch: pytest.MonkeyPatch, config_file: Path
) -> None:
    _patch_backend(
        monkeypatch, [Repository(name="afw", url="https://github.com/lsst/afw")]
    )
    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["report", "--config", str(config_file), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["org"] == "lsst"
    assert data["repositories"][0]["repo"]["name"] == "afw"


def test_report_writes_to_output_file(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    _patch_backend(
        monkeypatch, [Repository(name="afw", url="https://github.com/lsst/afw")]
    )
    out = tmp_path / "report.md"
    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["report", "--config", str(config_file), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "# GitHub Repository Report: lsst" in out.read_text()


def test_org_flag_overrides_config(
    monkeypatch: pytest.MonkeyPatch, config_file: Path
) -> None:
    seen: dict[str, str] = {}

    def fake_fetch(self: object, org: str) -> list[Repository]:
        seen["org"] = org
        return []

    monkeypatch.setattr(cli, "resolve_token", lambda token: "fake-token")
    monkeypatch.setattr(cli.GitHubGraphQLSource, "fetch_repositories", fake_fetch)
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["report", "--config", str(config_file), "--org", "other", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert seen["org"] == "other"


def test_config_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["report", "--config", str(tmp_path / "missing.yaml")]
    )
    assert result.exit_code != 0
    assert "error:" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError`.

- [ ] **Step 3: Write the implementation**

Create `python/lsst/github_summarizer/cli.py`:

```python
"""Command-line interface for the GitHub organization summarizer."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import click

from .config import ConfigError, load_config
from .orchestrator import build_summaries
from .report import render_csv, render_json, render_markdown
from .source import GitHubError, GitHubGraphQLSource, resolve_token

__all__ = ["main"]


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
@click.option("--verbose", is_flag=True, help="Show full tracebacks on error.")
def report(
    config_path: Path,
    org: str | None,
    token: str | None,
    output_format: str,
    output: Path | None,
    include_archived: bool,
    include_disabled: bool,
    appendix: bool,
    verbose: bool,
) -> None:
    """Generate a repository report for the configured organization."""
    try:
        config = load_config(config_path)
        if org:
            config = config.model_copy(update={"org": org})

        resolved = resolve_token(token)
        source = GitHubGraphQLSource(resolved)
        now = datetime.now(UTC)
        summaries = build_summaries(
            source,
            config,
            now=now,
            include_archived=include_archived,
            include_disabled=include_disabled,
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
    except (ConfigError, GitHubError) as exc:
        if verbose:
            raise
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Lint, type-check, commit**

```bash
ruff check . && ruff format --check . && mypy python
git add python/lsst/github_summarizer/cli.py tests/test_cli.py
git commit -m "Add Click CLI with report subcommand"
```

---

## Task 10: CI workflow & example config

**Files:**
- Create: `.github/workflows/ci.yaml`
- Create: `example-config.yaml`

- [ ] **Step 1: Create the example config**

Create `example-config.yaml`:

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
    glob: ["DMTN-*"]
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

- [ ] **Step 2: Create the CI workflow**

Create `.github/workflows/ci.yaml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.13", "3.14"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install
        run: pip install -e '.[test]' ruff mypy
      - name: Ruff check
        run: ruff check .
      - name: Ruff format
        run: ruff format --check .
      - name: Mypy
        run: mypy python
      - name: Pytest
        run: pytest -v
```

- [ ] **Step 3: Validate the example config loads**

Run: `python -c "from pathlib import Path; from lsst.github_summarizer.config import load_config; print(load_config(Path('example-config.yaml')).org)"`
Expected: prints `lsst`.

- [ ] **Step 4: Final full verification**

Run: `ruff check . && ruff format --check . && mypy python && pytest -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yaml example-config.yaml
git commit -m "Add CI workflow and example configuration"
```

---

## Self-Review Notes (for the implementer)

- **Task order caveat:** Task 6 (orchestrator) imports `GitHubSource` from `source.py` (Task 7). Implement Task 7's protocol + token resolver before running Task 6's tests, or do Task 7 entirely first. This is called out in both tasks.
- **Type names are consistent across tasks:** `Repository`, `RepositorySummary`, `ActivityStatus`, `Config`, `ActivityConfig`, `GroupRule`, `Override`, `AutoGroupConfig`, `Grouper.assign`, `classify_activity`, `build_summaries`, `resolve_token`, `GitHubGraphQLSource`, `render_markdown`/`render_csv`/`render_json` — all used identically where referenced.
- **Spec coverage:** all required fields (§5), activity precedence (§7), grouping precedence incl. glob/regex/auto-topic (§8), three output formats (§9), token discovery chain (§10), CLI flags with archived/disabled excluded by default (§11), error handling (§12), and the full test matrix (§13) each map to a task above.
```
