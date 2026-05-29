"""GitHub repository source: protocol, token resolution, and GraphQL client."""

from __future__ import annotations

import logging
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

_LOG = logging.getLogger(__name__)

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
# httpx defaults to a 5s timeout, too short for a GraphQL query over a large
# organization; use a generous default that callers can override.
_DEFAULT_TIMEOUT = 30.0


class GitHubError(Exception):
    """Raised when the GitHub source cannot produce data."""


class GitHubSource(Protocol):
    """A source of raw repository records for an organization."""

    def fetch_repositories(self, org: str) -> list[Repository]:
        """Return all repositories for the named organization."""
        ...


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
        _LOG.debug("Using token from explicit --token option")
        return explicit

    if os.environ.get("GITHUB_TOKEN"):
        _LOG.debug("Using token from GITHUB_TOKEN environment variable")
        return os.environ["GITHUB_TOKEN"]
    if os.environ.get("GH_TOKEN"):
        _LOG.debug("Using token from GH_TOKEN environment variable")
        return os.environ["GH_TOKEN"]

    chain = _DEFAULT_RESOLVERS if resolvers is None else resolvers
    for resolver in chain:
        token = resolver()
        if token:
            _LOG.debug("Using token from %s", getattr(resolver, "__name__", "resolver"))
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
        timeout: float = _DEFAULT_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._client = client or httpx.Client(timeout=timeout)
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
        _LOG.debug("Fetching repositories for organization %r from %s", org, self._base_url)
        repos: list[Repository] = []
        cursor: str | None = None
        page = 0
        while True:
            page += 1
            data = self._post({"org": org, "cursor": cursor}, page=page)
            organization = data["organization"]
            if organization is None:
                raise GitHubError(f"organization {org!r} not found or not accessible")
            connection = organization["repositories"]
            for node in connection["nodes"]:
                repos.append(self._parse_node(node))
            _LOG.debug("Page %d: %d repositories so far", page, len(repos))
            page_info = connection["pageInfo"]
            if page_info["hasNextPage"]:
                cursor = page_info["endCursor"]
            else:
                _LOG.debug("Fetched %d repositories across %d page(s)", len(repos), page)
                return repos

    def _post(self, variables: dict[str, str | None], *, page: int = 1) -> dict[str, Any]:
        """POST the query, retrying on rate-limit responses."""
        headers = {"Authorization": f"Bearer {self._token}"}
        payload = {"query": _GRAPHQL_QUERY, "variables": variables}
        for attempt in range(_MAX_RETRIES):
            _LOG.debug("POST page %d to %s (attempt %d)", page, self._base_url, attempt + 1)
            started = time.monotonic()
            try:
                response = self._client.post(self._base_url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise GitHubError(
                    f"request to GitHub failed: {exc} "
                    f"(after {time.monotonic() - started:.1f}s; "
                    f"is the network reachable and the timeout sufficient?)"
                ) from exc

            elapsed = time.monotonic() - started
            _LOG.debug("HTTP %d in %.1fs", response.status_code, elapsed)

            if response.status_code == 403 and attempt < _MAX_RETRIES - 1:
                wait = _retry_after_seconds(response)
                _LOG.warning("Rate limited (HTTP 403); retrying page %d in %.0fs", page, wait)
                self._sleep(wait)
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
