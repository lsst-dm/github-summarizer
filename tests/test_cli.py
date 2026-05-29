"""Tests for the Click CLI wiring."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from lsst.github_summarizer import cli
from lsst.github_summarizer.cache import load_raw
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
