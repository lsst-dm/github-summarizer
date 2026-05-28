"""Configuration models and YAML loader."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator

__all__ = [
    "ActivityConfig",
    "AutoGroupConfig",
    "Config",
    "ConfigError",
    "GroupRule",
    "Override",
    "load_config",
]


class ConfigError(Exception):
    """Raised when configuration cannot be loaded or validated."""


class ActivityConfig(BaseModel):
    """Day thresholds for activity classification."""

    active_days: int = 90
    warm_days: int = 180
    quiet_days: int = 365


class GroupRule(BaseModel):
    """A semantic grouping rule matched by topic, glob, or regex."""

    name: str
    topics: list[str] = []
    glob: list[str] = []
    regex: list[str] = []
    description: str | None = None

    @field_validator("regex")
    @classmethod
    def _validate_regex(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return value


class Override(BaseModel):
    """A manual group assignment for a specific repository name."""

    group: str
    notes: str | None = None


class AutoGroupConfig(BaseModel):
    """Settings for clustering leftover repos by shared topic."""

    enabled: bool = True
    min_repos: int = 3
    ignore_topics: list[str] = []


class Config(BaseModel):
    """Top-level configuration for a report run."""

    org: str
    activity: ActivityConfig = ActivityConfig()
    groups: list[GroupRule] = []
    overrides: dict[str, Override] = {}
    auto_group_by_topic: AutoGroupConfig = AutoGroupConfig()
    fallback_group: str = "Uncategorized"


def load_config(path: Path) -> Config:
    """Load and validate a YAML configuration file.

    Parameters
    ----------
    path : `pathlib.Path`
        Path to the YAML configuration file.

    Returns
    -------
    config : `Config`
        The validated configuration.

    Raises
    ------
    ConfigError
        Raised if the file cannot be read, is not valid YAML, is not a
        mapping, or fails schema validation.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"config {path} must be a mapping")

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid config {path}: {exc}") from exc
