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
        auto_group_by_topic=AutoGroupConfig(enabled=True, min_repos=3, ignore_topics=["lsst"]),
        fallback_group="Uncategorized",
    )
    repos = [_repo(f"r{i}", topics=["lsst"]) for i in range(5)]
    result = Grouper(config).assign(repos)
    assert all(result[r.name] == ("Uncategorized", "fallback") for r in repos)


def test_auto_topic_largest_cluster_wins_for_multi_topic_repo() -> None:
    # 'shared' covers 4 repos, 'small' covers 4 (including the overlap repo d).
    # The overlap repo must land in the larger/earlier 'shared' cluster, and
    # 'small' still forms from its three remaining members.
    config = Config(org="lsst", auto_group_by_topic=AutoGroupConfig(enabled=True, min_repos=3))
    repos = [
        _repo("a", topics=["shared"]),
        _repo("b", topics=["shared"]),
        _repo("c", topics=["shared"]),
        _repo("d", topics=["shared", "small"]),
        _repo("e", topics=["small"]),
        _repo("f", topics=["small"]),
        _repo("g", topics=["small"]),
    ]
    result = Grouper(config).assign(repos)
    assert result["d"] == ("shared", "auto-topic:shared")
    assert result["e"] == ("small", "auto-topic:small")


def test_auto_topic_ties_broken_alphabetically() -> None:
    # 'alpha' and 'beta' each cover exactly 3 distinct repos. 'alpha' wins.
    config = Config(org="lsst", auto_group_by_topic=AutoGroupConfig(enabled=True, min_repos=3))
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
