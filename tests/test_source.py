"""Tests for token resolution and the GraphQL source."""

import json
import logging
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
                                "defaultBranchRef": {
                                    "name": "main",
                                    "target": {
                                        "history": {
                                            "totalCount": 2,
                                            "nodes": [
                                                {"committedDate": "2026-01-01T00:00:00Z"},
                                                {"committedDate": "2025-12-01T00:00:00Z"},
                                            ],
                                        }
                                    },
                                },
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
    assert repos[0].default_branch_commit_count == 2
    assert len(repos[0].recent_commit_dates) == 2
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


def test_timeout_applied_to_default_client() -> None:
    source = GitHubGraphQLSource("token", timeout=12.5)
    assert source._client.timeout.read == 12.5


def test_default_timeout_is_generous() -> None:
    # Must be well above httpx's 5s default so large orgs do not time out.
    source = GitHubGraphQLSource("token")
    assert source._client.timeout.read is not None
    assert source._client.timeout.read >= 30.0


def test_custom_repository_page_size_and_commit_history_count_are_sent() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode())
        seen["repository_count"] = payload["variables"]["repositoryCount"]
        seen["history_count"] = payload["variables"]["historyCount"]
        return httpx.Response(
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
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = GitHubGraphQLSource(
        "token",
        client=client,
        repository_page_size=11,
        commit_history_count=12,
    )
    source.fetch_repositories("lsst")
    assert seen["repository_count"] == 11
    assert seen["history_count"] == 12


def test_invalid_repository_page_size_raises() -> None:
    with pytest.raises(ValueError):
        GitHubGraphQLSource("token", repository_page_size=0)


def test_invalid_commit_history_count_raises() -> None:
    with pytest.raises(ValueError):
        GitHubGraphQLSource("token", commit_history_count=0)


def test_transient_graphql_server_error_is_retried() -> None:
    responses = [
        httpx.Response(502, text="<html>bad gateway</html>"),
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


def test_fetch_logs_progress(caplog: pytest.LogCaptureFixture) -> None:
    client = httpx.Client(transport=httpx.MockTransport(_paged_handler()))
    source = GitHubGraphQLSource("token", client=client)
    with caplog.at_level(logging.DEBUG, logger="lsst.github_summarizer.source"):
        source.fetch_repositories("lsst")
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "lsst" in messages
    assert "page" in messages.lower()
    assert "2" in messages  # total repository count


def test_rate_limit_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
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
    with caplog.at_level(logging.WARNING, logger="lsst.github_summarizer.source"):
        source.fetch_repositories("lsst")
    assert any("rate" in record.getMessage().lower() for record in caplog.records)


def test_update_repository_description_uses_rest_api() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = GitHubGraphQLSource("token", client=client)
    source.update_repository_description("lsst", "afw", "new description")
    assert seen["method"] == "PATCH"
    assert seen["url"] == "https://api.github.com/repos/lsst/afw"
    assert seen["body"] == '{"description":"new description"}'


def test_replace_repository_topics_uses_rest_api() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"names": ["dm", "pipelines"]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = GitHubGraphQLSource("token", client=client)
    source.replace_repository_topics("lsst", "afw", ["dm", "pipelines"])
    assert seen["method"] == "PUT"
    assert seen["url"] == "https://api.github.com/repos/lsst/afw/topics"
    assert seen["body"] == '{"names":["dm","pipelines"]}'


def test_rest_update_errors_raise_github_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = GitHubGraphQLSource("token", client=client)
    with pytest.raises(GitHubError, match="404"):
        source.update_repository_description("lsst", "missing", "new")


def test_resolve_token_logs_source(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with caplog.at_level(logging.DEBUG, logger="lsst.github_summarizer.source"):
        resolve_token(None)
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "GITHUB_TOKEN" in messages
