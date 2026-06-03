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
    assert config.activity.abandoned_days == 5 * 365
    assert config.fallback_group == "Uncategorized"
    assert config.ignored_topics == []
    assert config.auto_group_by_topic.enabled is True
    assert config.auto_group_by_topic.min_repos == 3


def test_load_full_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
org: lsst
ignored_topics: [hacktoberfest]
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

    assert config.ignored_topics == ["hacktoberfest"]
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
