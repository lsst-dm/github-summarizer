# CLAUDE Guidelines

## What is this?

This is a tool for scanning a GitHub org and generating reports on the state of the repositories in that org.

## Build Guidelines

Uses standard python tooling.

* The code must always pass `ruff check`, `ruff format` and `mypy`.
* There should be unit tests for all public APIs. Pytest can be used for testing infrastructure.
* Use Click for command line tooling with subcommands.
* Use GitHub workflows for CI.

## Coding Policies

* Place imports at the top of the file unless there is a circular import problem.
  We do not want imports in each method/function call.
