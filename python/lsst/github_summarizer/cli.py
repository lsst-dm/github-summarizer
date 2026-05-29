"""Command-line interface for the GitHub organization summarizer."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import assert_never

import click

from .cache import CacheError, RawFileSource, load_raw, save_raw
from .config import ConfigError, load_config
from .metadata import (
    MetadataChange,
    MetadataError,
    MetadataField,
    load_metadata_csv,
    plan_metadata_updates,
)
from .orchestrator import build_summaries
from .report import render_csv, render_json, render_markdown
from .source import GitHubError, GitHubGraphQLSource, GitHubMetadataUpdater, GitHubSource, resolve_token

__all__ = ["main"]

_LOG = logging.getLogger(__name__)


@contextmanager
def _cli_context(verbose: bool) -> Iterator[None]:
    """Configure logging and turn known errors into clean CLI failures.

    Parameters
    ----------
    verbose : `bool`
        Enable debug logging and re-raise (full traceback) on known errors.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        yield
    except (ConfigError, GitHubError, CacheError, MetadataError) as exc:
        if verbose:
            raise
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """Summarize the repositories in a GitHub organization."""


@main.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    help="Path to the YAML configuration file.",
)
@click.option("--org", default=None, help="Override the organization in the config.")
@click.option("--token", default=None, help="GitHub token (else discovered).")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["markdown", "csv", "json"]),
    default="markdown",
    help="Output format.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write to this path instead of stdout.",
)
@click.option("--include-archived", is_flag=True, help="Include archived repos.")
@click.option("--include-disabled", is_flag=True, help="Include disabled repos.")
@click.option("--appendix", is_flag=True, help="Append full inventory (markdown).")
@click.option(
    "--timeout",
    type=float,
    default=30.0,
    show_default=True,
    help="HTTP timeout in seconds per request.",
)
@click.option(
    "--from-raw",
    "from_raw",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="Render from a saved raw cache file instead of querying GitHub.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable debug logging and show full tracebacks on error.",
)
def report(
    config_path: Path,
    org: str | None,
    token: str | None,
    output_format: str,
    output: Path | None,
    include_archived: bool,
    include_disabled: bool,
    appendix: bool,
    timeout: float,
    from_raw: Path | None,
    verbose: bool,
) -> None:
    """Generate a repository report for the configured organization."""
    with _cli_context(verbose):
        config = load_config(config_path)
        if org:
            config = config.model_copy(update={"org": org})

        now = datetime.now(UTC)
        source: GitHubSource
        fetched_at: datetime | None = None
        if from_raw is not None:
            raw = load_raw(from_raw)
            source = RawFileSource(raw)
            fetched_at = raw.fetched_at
        else:
            source = GitHubGraphQLSource(resolve_token(token), timeout=timeout)
        summaries = build_summaries(
            source,
            config,
            now=now,
            include_archived=include_archived,
            include_disabled=include_disabled,
        )
        _LOG.info("Generated report for %d repositories in %r", len(summaries), config.org)

        if output_format == "json":
            text = render_json(summaries, config, now)
        elif output_format == "csv":
            text = render_csv(summaries, config, now)
        else:
            text = render_markdown(
                summaries,
                config,
                now,
                fetched_at=fetched_at,
                include_archived=include_archived,
                include_disabled=include_disabled,
                include_appendix=appendix,
            )

        if output is not None:
            output.write_text(text)
        else:
            click.echo(text, nl=False)


@main.command("apply-metadata")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    help="Path to the YAML configuration file.",
)
@click.option("--org", default=None, help="Override the organization in the config.")
@click.option("--token", default=None, help="GitHub token (else discovered).")
@click.option(
    "--baseline",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Original CSV report exported before spreadsheet edits.",
)
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Edited CSV report containing desired descriptions and topics.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show detected updates without changing GitHub.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Apply safe updates without prompting.",
)
@click.option(
    "--allow-stale",
    is_flag=True,
    help="Apply edits even when live GitHub metadata differs from the baseline CSV.",
)
@click.option(
    "--timeout",
    type=float,
    default=30.0,
    show_default=True,
    help="HTTP timeout in seconds per request.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable debug logging and show full tracebacks on error.",
)
def apply_metadata(
    config_path: Path,
    org: str | None,
    token: str | None,
    baseline: Path,
    input_path: Path,
    dry_run: bool,
    yes: bool,
    allow_stale: bool,
    timeout: float,
    verbose: bool,
) -> None:
    """Apply edited CSV descriptions and topics to GitHub repositories."""
    with _cli_context(verbose):
        if dry_run and yes:
            raise MetadataError("--dry-run and --yes cannot be used together")

        config = load_config(config_path)
        if org:
            config = config.model_copy(update={"org": org})

        baseline_rows = load_metadata_csv(baseline)
        edited_rows = load_metadata_csv(input_path)
        client = GitHubGraphQLSource(resolve_token(token), timeout=timeout)
        current = client.fetch_repositories(config.org)
        plan = plan_metadata_updates(baseline_rows, edited_rows, current)

        _report_plan_skips(plan.missing_repositories, plan.already_current)
        if plan.conflicts and not allow_stale:
            _report_conflicts(plan.conflicts)

        changes = list(plan.changes)
        stale_changes = set(plan.conflicts)
        if allow_stale:
            changes.extend(plan.conflicts)

        if not changes:
            if not plan.conflicts and not plan.missing_repositories and not plan.already_current:
                click.echo("No metadata changes detected.")
            elif dry_run:
                click.echo("Dry run: no GitHub metadata would be updated.")
            return

        applied = 0
        skipped = 0
        for change in changes:
            _show_change(change, stale=change in stale_changes)
            if dry_run:
                continue
            if not yes and not click.confirm("Apply this update?", default=False):
                skipped += 1
                continue
            _apply_metadata_change(client, config.org, change)
            applied += 1

        if dry_run:
            click.echo(f"Dry run: {len(changes)} GitHub metadata update(s) would be applied.")
        else:
            click.echo(f"Applied {applied} GitHub metadata update(s); skipped {skipped}.")


@main.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    help="Path to the YAML configuration file.",
)
@click.option("--org", default=None, help="Override the organization in the config.")
@click.option("--token", default=None, help="GitHub token (else discovered).")
@click.option(
    "--output",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Destination file for the raw repository data.",
)
@click.option(
    "--timeout",
    type=float,
    default=30.0,
    show_default=True,
    help="HTTP timeout in seconds per request.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable debug logging and show full tracebacks on error.",
)
def fetch(
    config_path: Path,
    org: str | None,
    token: str | None,
    output: Path,
    timeout: float,
    verbose: bool,
) -> None:
    """Fetch raw repository data and save it for later reporting."""
    with _cli_context(verbose):
        config = load_config(config_path)
        if org:
            config = config.model_copy(update={"org": org})

        source = GitHubGraphQLSource(resolve_token(token), timeout=timeout)
        repos = source.fetch_repositories(config.org)
        save_raw(output, repos, org=config.org, fetched_at=datetime.now(UTC))
        _LOG.info("Saved %d repositories to %s", len(repos), output)


def _report_plan_skips(
    missing_repositories: tuple[str, ...],
    already_current: tuple[MetadataChange, ...],
) -> None:
    """Report skipped metadata plan entries."""
    if missing_repositories:
        click.echo(
            "Skipped repository metadata edits missing from GitHub: " + ", ".join(missing_repositories)
        )
    if already_current:
        click.echo(f"Skipped {len(already_current)} update(s) already present on GitHub.")


def _report_conflicts(conflicts: tuple[MetadataChange, ...]) -> None:
    """Report stale metadata conflicts."""
    click.echo(f"Skipped {len(conflicts)} stale update(s); use --allow-stale to apply them anyway.")
    for change in conflicts:
        click.echo(
            f"  {change.repo} {change.field.value}: baseline "
            f"{_format_metadata_value(change.baseline)}; current "
            f"{_format_metadata_value(change.current)}; desired "
            f"{_format_metadata_value(change.desired)}"
        )


def _show_change(change: MetadataChange, *, stale: bool) -> None:
    """Display one metadata change."""
    prefix = "STALE " if stale else ""
    topic_annotation = _topic_delta_annotation(change)
    click.echo(
        f"{prefix}{change.repo} {change.field.value}: "
        f"{_format_metadata_value(change.current)} -> {_format_metadata_value(change.desired)}"
        f"{topic_annotation}"
    )


def _apply_metadata_change(
    updater: GitHubMetadataUpdater,
    org: str,
    change: MetadataChange,
) -> None:
    """Apply one metadata change through the GitHub API abstraction."""
    if change.field is MetadataField.DESCRIPTION:
        if not isinstance(change.desired, str):
            raise TypeError("description change must have a string desired value")
        updater.update_repository_description(org, change.repo, change.desired)
    elif change.field is MetadataField.TOPICS:
        if not isinstance(change.desired, tuple):
            raise TypeError("topic change must have a tuple desired value")
        updater.replace_repository_topics(org, change.repo, list(change.desired))
    else:
        assert_never(change.field)


def _format_metadata_value(value: object) -> str:
    """Format metadata values for CLI display."""
    if isinstance(value, tuple):
        return ";".join(str(item) for item in value) if value else "(none)"
    if value == "":
        return "(empty)"
    return str(value)


def _topic_delta_annotation(change: MetadataChange) -> str:
    """Summarize topic additions and removals for non-empty topic changes."""
    if change.field is not MetadataField.TOPICS:
        return ""
    if not isinstance(change.current, tuple) or not isinstance(change.desired, tuple):
        raise TypeError("topic change must have tuple metadata values")
    if not change.current or not change.desired:
        return ""

    current = set(change.current)
    desired = set(change.desired)
    added = [topic for topic in change.desired if topic not in current]
    removed = [topic for topic in change.current if topic not in desired]

    notes: list[str] = []
    if added:
        notes.append(f'added "{", ".join(added)}"')
    if removed:
        notes.append(f'removed "{", ".join(removed)}"')
    return f" [{'; '.join(notes)}]" if notes else ""
