"""Semantic grouping of repositories by configured rules."""

from __future__ import annotations

import fnmatch
import re

from .config import Config
from .models import Repository

__all__ = ["Grouper"]

# A (group_name, grouping_reason) pair.
Assignment = tuple[str, str]


class Grouper:
    """Assign repositories to semantic groups using configured rules."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def assign(self, repos: list[Repository]) -> dict[str, Assignment]:
        """Assign every repository to a group.

        Parameters
        ----------
        repos : `list` [ `Repository` ]
            All repositories to classify. The full list is required because
            the auto-topic pass clusters across the ungrouped remainder.

        Returns
        -------
        assignments : `dict` [ `str`, `tuple` [ `str`, `str` ] ]
            Mapping of repository name to ``(group, grouping_reason)``.
        """
        result: dict[str, Assignment] = {}
        ungrouped: list[Repository] = []
        for repo in repos:
            explicit = self._match_explicit(repo)
            if explicit is not None:
                result[repo.name] = explicit
            else:
                ungrouped.append(repo)

        if self._config.auto_group_by_topic.enabled:
            self._assign_auto_topic(ungrouped, result)

        fallback = self._config.fallback_group
        for repo in ungrouped:
            if repo.name not in result:
                result[repo.name] = (fallback, "fallback")
        return result

    def _match_explicit(self, repo: Repository) -> Assignment | None:
        """Match override, glob, regex, then topic (in precedence order)."""
        config = self._config
        if repo.name in config.overrides:
            return (config.overrides[repo.name].group, "override")

        for rule in config.groups:
            for pattern in rule.glob:
                if fnmatch.fnmatchcase(repo.name.lower(), pattern.lower()):
                    return (rule.name, f"glob:{pattern}")

        for rule in config.groups:
            for pattern in rule.regex:
                if re.search(pattern, repo.name):
                    return (rule.name, f"regex:{pattern}")

        for rule in config.groups:
            for topic in rule.topics:
                if topic in repo.topics:
                    return (rule.name, f"topic:{topic}")

        return None

    def _assign_auto_topic(self, ungrouped: list[Repository], result: dict[str, Assignment]) -> None:
        """Cluster ungrouped repos by their most-shared topic, greedily."""
        cfg = self._config.auto_group_by_topic
        ignore = set(cfg.ignore_topics)
        pool: dict[str, Repository] = {r.name: r for r in ungrouped}

        while True:
            index: dict[str, set[str]] = {}
            for name, repo in pool.items():
                for topic in repo.topics:
                    if topic in ignore:
                        continue
                    index.setdefault(topic, set()).add(name)
            if not index:
                break

            # Largest cluster wins; ties broken alphabetically by topic.
            topic = min(index, key=lambda t: (-len(index[t]), t))
            names = index[topic]
            if len(names) < cfg.min_repos:
                break

            for name in names:
                result[name] = (topic, f"auto-topic:{topic}")
                del pool[name]
