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
                                "repositoryTopics": {"nodes": [{"topic": {"name": "pipelines"}}]},
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
