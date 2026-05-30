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
        activity_at=datetime(2026, 1, 1, tzinfo=UTC),
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
    assert rows[0]["activity_at"] == "2026-01-01T00:00:00+00:00"
    assert rows[0]["group"] == "Pipelines"


def test_render_markdown_has_title_summary_and_groups() -> None:
    output = render_markdown(_summaries(), _config(), GENERATED)
    assert "# GitHub Repository Report: lsst" in output
    assert "Total repositories: 2" in output
    assert "Active: 1" in output
    assert "Activity date" in output
    assert "## Pipelines" in output
    assert "Coordinated pipelines" in output
    assert "## Uncategorized" in output
    assert "afw" in output


def test_render_markdown_appendix_optional() -> None:
    without = render_markdown(_summaries(), _config(), GENERATED)
    assert "Appendix" not in without
    with_appendix = render_markdown(_summaries(), _config(), GENERATED, include_appendix=True)
    assert "Appendix" in with_appendix


def test_render_markdown_groups_sorted_fallback_last() -> None:
    output = render_markdown(_summaries(), _config(), GENERATED)
    assert output.index("## Pipelines") < output.index("## Uncategorized")


def test_render_markdown_omits_archived_disabled_counts_by_default() -> None:
    output = render_markdown(_summaries(), _config(), GENERATED)
    assert "Active:" in output
    assert "Archived:" not in output
    assert "Disabled:" not in output


def test_render_markdown_shows_archived_disabled_counts_when_included() -> None:
    output = render_markdown(
        _summaries(),
        _config(),
        GENERATED,
        include_archived=True,
        include_disabled=True,
    )
    assert "Archived:" in output
    assert "Disabled:" in output


def test_render_markdown_sorts_repos_case_insensitively() -> None:
    summaries = [
        _summary("DMTN-095", "Uncategorized", ActivityStatus.ACTIVE),
        _summary("dmtn-000", "Uncategorized", ActivityStatus.ACTIVE),
    ]
    output = render_markdown(summaries, _config(), GENERATED)
    assert output.index("dmtn-000") < output.index("DMTN-095")


def test_render_markdown_omits_fetched_line_by_default() -> None:
    output = render_markdown(_summaries(), _config(), GENERATED)
    assert "Data fetched" not in output


def test_render_markdown_shows_fetched_line_when_provided() -> None:
    fetched = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    output = render_markdown(_summaries(), _config(), GENERATED, fetched_at=fetched)
    assert "Data fetched" in output
    assert fetched.isoformat() in output
