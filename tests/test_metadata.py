"""Tests for CSV metadata update planning."""

import pytest

from lsst.github_summarizer.metadata import (
    MetadataError,
    MetadataField,
    MetadataRow,
    parse_metadata_csv,
    plan_metadata_updates,
)
from lsst.github_summarizer.models import Repository


def test_parse_metadata_csv_normalizes_editable_fields() -> None:
    rows = parse_metadata_csv(
        'name,description,topics\nafw, Framework , Pipelines;LSST;pipelines \ndaf_butler,,"data,extra"\n'
    )
    assert rows["afw"].description == "Framework"
    assert rows["afw"].topics == ("pipelines", "lsst")
    assert rows["daf_butler"].description == ""
    assert rows["daf_butler"].topics == ("data", "extra")


def test_parse_metadata_csv_requires_columns() -> None:
    with pytest.raises(MetadataError, match="description"):
        parse_metadata_csv("name,topics\na,b\n")


def test_parse_metadata_csv_rejects_duplicate_names() -> None:
    with pytest.raises(MetadataError, match="duplicate"):
        parse_metadata_csv("name,description,topics\na,,x\na,,y\n")


def test_parse_metadata_csv_rejects_extra_columns() -> None:
    with pytest.raises(MetadataError, match="too many columns"):
        parse_metadata_csv("name,description,topics\na,,x,y\n")


def test_plan_metadata_updates_detects_safe_changes() -> None:
    baseline = {
        "afw": MetadataRow("afw", "old", ("pipelines",)),
        "daf_butler": MetadataRow("daf_butler", "", ()),
    }
    edited = {
        "afw": MetadataRow("afw", "new", ("pipelines", "data")),
        "daf_butler": MetadataRow("daf_butler", "", ()),
    }
    current = [
        Repository(
            name="afw",
            url="https://github.com/lsst/afw",
            description="old",
            topics=["pipelines"],
        )
    ]
    plan = plan_metadata_updates(baseline, edited, current)
    assert [(change.repo, change.field) for change in plan.changes] == [
        ("afw", MetadataField.DESCRIPTION),
        ("afw", MetadataField.TOPICS),
    ]
    assert plan.conflicts == ()
    assert plan.missing_repositories == ()
    assert plan.has_work


def test_plan_metadata_updates_detects_conflicts_and_already_current() -> None:
    baseline = {
        "afw": MetadataRow("afw", "old", ("pipelines",)),
        "daf_butler": MetadataRow("daf_butler", "baseline", ()),
    }
    edited = {
        "afw": MetadataRow("afw", "new", ("pipelines",)),
        "daf_butler": MetadataRow("daf_butler", "desired", ()),
    }
    current = [
        Repository(name="afw", url="https://github.com/lsst/afw", description="live"),
        Repository(
            name="daf_butler",
            url="https://github.com/lsst/daf_butler",
            description="desired",
        ),
    ]
    plan = plan_metadata_updates(baseline, edited, current)
    assert [change.repo for change in plan.conflicts] == ["afw"]
    assert [change.repo for change in plan.already_current] == ["daf_butler"]
    assert plan.changes == ()


def test_plan_metadata_updates_reports_missing_current_repo() -> None:
    baseline = {"afw": MetadataRow("afw", "old", ())}
    edited = {"afw": MetadataRow("afw", "new", ())}
    plan = plan_metadata_updates(baseline, edited, [])
    assert plan.missing_repositories == ("afw",)
    assert plan.changes == ()
    assert not plan.has_work


def test_plan_metadata_updates_rejects_unknown_edited_repo() -> None:
    baseline = {"afw": MetadataRow("afw", "", ())}
    edited = {
        "afw": MetadataRow("afw", "", ()),
        "new": MetadataRow("new", "", ()),
    }
    with pytest.raises(MetadataError, match="not present"):
        plan_metadata_updates(baseline, edited, [])
