"""GitHub repository source: protocol, token resolution, and GraphQL client."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from typing import Any, Protocol

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
        "no GitHub token found (tried --token, GITHUB_TOKEN/GH_TOKEN, gh auth token, git credential)"
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

    def _post(self, variables: dict[str, str | None]) -> dict[str, Any]:
        """POST the query, retrying on rate-limit responses."""
        headers = {"Authorization": f"Bearer {self._token}"}
        payload = {"query": _GRAPHQL_QUERY, "variables": variables}
        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.post(self._base_url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise GitHubError(f"request to GitHub failed: {exc}") from exc

            if response.status_code == 403 and attempt < _MAX_RETRIES - 1:
                self._sleep(_retry_after_seconds(response))
                continue

            if response.status_code != 200:
                raise GitHubError(f"GitHub returned HTTP {response.status_code}: {response.text}")

            body = response.json()
            if body.get("errors"):
                raise GitHubError(f"GraphQL errors: {body['errors']}")
            data: dict[str, Any] = body["data"]
            return data

        raise GitHubError("exceeded retry limit due to rate limiting")

    @staticmethod
    def _parse_node(node: dict[str, Any]) -> Repository:
        """Convert a GraphQL repository node into a `Repository`."""
        language = node.get("primaryLanguage")
        branch = node.get("defaultBranchRef")
        topics = [entry["topic"]["name"] for entry in node["repositoryTopics"]["nodes"]]
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
