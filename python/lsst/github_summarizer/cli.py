"""Command-line interface for the GitHub organization summarizer."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import click

from .cache import CacheError, RawFileSource, load_raw, save_raw
from .config import ConfigError, load_config
from .orchestrator import build_summaries
from .report import render_csv, render_json, render_markdown
from .source import GitHubError, GitHubGraphQLSource, GitHubSource, resolve_token

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
    except (ConfigError, GitHubError, CacheError) as exc:
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
