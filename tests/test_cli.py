"""Tests for the Click CLI wiring."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from lsst.github_summarizer import cli
from lsst.github_summarizer.cache import load_raw, save_raw
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


def test_h_alias_on_group() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.main, ["-h"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_h_alias_on_subcommand() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.main, ["report", "-h"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_report_json_to_stdout(monkeypatch: pytest.MonkeyPatch, config_file: Path) -> None:
    _patch_backend(monkeypatch, [Repository(name="afw", url="https://github.com/lsst/afw")])
    runner = CliRunner()
    result = runner.invoke(cli.main, ["report", "--config", str(config_file), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["org"] == "lsst"
    assert data["repositories"][0]["repo"]["name"] == "afw"


def test_report_writes_to_output_file(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    _patch_backend(monkeypatch, [Repository(name="afw", url="https://github.com/lsst/afw")])
    out = tmp_path / "report.md"
    runner = CliRunner()
    result = runner.invoke(cli.main, ["report", "--config", str(config_file), "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert "# GitHub Repository Report: lsst" in out.read_text()


def test_org_flag_overrides_config(monkeypatch: pytest.MonkeyPatch, config_file: Path) -> None:
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


def test_config_error_exits_nonzero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.main, ["report", "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code != 0
    assert "error:" in result.output


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
    result = runner.invoke(cli.main, ["fetch", "--config", str(config_file), "--output", str(out)])
    assert result.exit_code == 0, result.output
    data = load_raw(out)
    assert data.org == "lsst"
    # Archived repos are saved too -- no filtering at fetch time.
    assert {r.name for r in data.repositories} == {"afw", "x"}


def test_fetch_requires_output(monkeypatch: pytest.MonkeyPatch, config_file: Path) -> None:
    _patch_backend(monkeypatch, [])
    runner = CliRunner()
    result = runner.invoke(cli.main, ["fetch", "--config", str(config_file)])
    assert result.exit_code != 0


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


def test_report_from_raw_markdown_shows_fetched(config_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "raw.json"
    fetched = datetime(2026, 5, 1, tzinfo=UTC)
    save_raw(
        out,
        [Repository(name="afw", url="https://github.com/lsst/afw")],
        org="lsst",
        fetched_at=fetched,
    )
    runner = CliRunner()
    result = runner.invoke(cli.main, ["report", "--config", str(config_file), "--from-raw", str(out)])
    assert result.exit_code == 0, result.output
    assert "Data fetched" in result.output
    assert fetched.isoformat() in result.output


def test_report_from_raw_corrupt_exits_nonzero(config_file: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    runner = CliRunner()
    result = runner.invoke(cli.main, ["report", "--config", str(config_file), "--from-raw", str(bad)])
    assert result.exit_code != 0
    assert "error:" in result.output


def test_apply_metadata_dry_run_does_not_update(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline.csv"
    edited = tmp_path / "edited.csv"
    baseline.write_text("name,description,topics\nafw,old,pipelines\n")
    edited.write_text("name,description,topics\nafw,new,pipelines;dm\n")
    _patch_backend(
        monkeypatch,
        [
            Repository(
                name="afw",
                url="https://github.com/lsst/afw",
                description="old",
                topics=["pipelines"],
            )
        ],
    )

    def boom_description(self: object, org: str, repo: str, description: str) -> None:
        raise AssertionError("dry run must not update descriptions")

    def boom_topics(self: object, org: str, repo: str, topics: list[str]) -> None:
        raise AssertionError("dry run must not update topics")

    monkeypatch.setattr(cli.GitHubGraphQLSource, "update_repository_description", boom_description)
    monkeypatch.setattr(cli.GitHubGraphQLSource, "replace_repository_topics", boom_topics)
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "apply-metadata",
            "--config",
            str(config_file),
            "--baseline",
            str(baseline),
            "--input",
            str(edited),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "afw description" in result.output
    assert 'afw topics: pipelines -> pipelines;dm [added "dm"]' in result.output
    assert "Dry run" in result.output


def test_apply_metadata_dry_run_shows_removed_topics(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline.csv"
    edited = tmp_path / "edited.csv"
    baseline.write_text("name,description,topics\nafw,,a;c;b\n")
    edited.write_text("name,description,topics\nafw,,c\n")
    _patch_backend(
        monkeypatch,
        [
            Repository(
                name="afw",
                url="https://github.com/lsst/afw",
                topics=["a", "c", "b"],
            )
        ],
    )
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "apply-metadata",
            "--config",
            str(config_file),
            "--baseline",
            str(baseline),
            "--input",
            str(edited),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert 'afw topics: a;c;b -> c [removed "a, b"]' in result.output


def test_apply_metadata_yes_updates_changed_fields(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline.csv"
    edited = tmp_path / "edited.csv"
    baseline.write_text("name,description,topics\nafw,old,pipelines\n")
    edited.write_text("name,description,topics\nafw,new,pipelines;dm\n")
    _patch_backend(
        monkeypatch,
        [
            Repository(
                name="afw",
                url="https://github.com/lsst/afw",
                description="old",
                topics=["pipelines"],
            )
        ],
    )
    descriptions: list[tuple[str, str, str]] = []
    topics: list[tuple[str, str, list[str]]] = []

    def update_description(self: object, org: str, repo: str, description: str) -> None:
        descriptions.append((org, repo, description))

    def replace_topics(self: object, org: str, repo: str, desired_topics: list[str]) -> None:
        topics.append((org, repo, desired_topics))

    monkeypatch.setattr(cli.GitHubGraphQLSource, "update_repository_description", update_description)
    monkeypatch.setattr(cli.GitHubGraphQLSource, "replace_repository_topics", replace_topics)
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "apply-metadata",
            "--config",
            str(config_file),
            "--baseline",
            str(baseline),
            "--input",
            str(edited),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert descriptions == [("lsst", "afw", "new")]
    assert topics == [("lsst", "afw", ["pipelines", "dm"])]
    assert "Applied 2" in result.output


def test_apply_metadata_skips_stale_updates_by_default(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline.csv"
    edited = tmp_path / "edited.csv"
    baseline.write_text("name,description,topics\nafw,old,pipelines\n")
    edited.write_text("name,description,topics\nafw,new,pipelines\n")
    _patch_backend(
        monkeypatch,
        [Repository(name="afw", url="https://github.com/lsst/afw", description="live")],
    )
    descriptions: list[str] = []

    def update_description(self: object, org: str, repo: str, description: str) -> None:
        descriptions.append(description)

    monkeypatch.setattr(cli.GitHubGraphQLSource, "update_repository_description", update_description)
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "apply-metadata",
            "--config",
            str(config_file),
            "--baseline",
            str(baseline),
            "--input",
            str(edited),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert descriptions == []
    assert "stale" in result.output


def test_apply_metadata_allow_stale_updates_conflict(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline.csv"
    edited = tmp_path / "edited.csv"
    baseline.write_text("name,description,topics\nafw,old,pipelines\n")
    edited.write_text("name,description,topics\nafw,new,pipelines\n")
    _patch_backend(
        monkeypatch,
        [Repository(name="afw", url="https://github.com/lsst/afw", description="live")],
    )
    descriptions: list[str] = []

    def update_description(self: object, org: str, repo: str, description: str) -> None:
        descriptions.append(description)

    monkeypatch.setattr(cli.GitHubGraphQLSource, "update_repository_description", update_description)
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "apply-metadata",
            "--config",
            str(config_file),
            "--baseline",
            str(baseline),
            "--input",
            str(edited),
            "--yes",
            "--allow-stale",
        ],
    )
    assert result.exit_code == 0, result.output
    assert descriptions == ["new"]
    assert "STALE afw description" in result.output
