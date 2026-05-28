# GitHub Organization Repository Report Generator

## Goal

Create a Python command-line tool that generates a report for all repositories in a GitHub organization.

The report must summarize basic repository metadata, activity status, and semantic grouping. The tool should be flexible enough to add more fields later without large architectural changes.

## Required repository fields

For every repository, collect:

- Repository name
- Repository URL
- Archived status
- Disabled status
- Description
- Primary language
- GitHub topic labels
- Last push timestamp
- Activity status
- Semantic group
- Grouping reason

## Data source

Use the GitHub GraphQL API as the primary data source.

The query should paginate over all repositories in the configured organization and retrieve at least:

- name
- url
- description
- isArchived
- isDisabled
- primaryLanguage { name }
- pushedAt
- repositoryTopics
- defaultBranchRef { name }

The tool should authenticate using a GitHub token from either:

- GITHUB_TOKEN
- an explicit CLI option

## Activity status

Compute activity status from the last push date.

Default thresholds:

- Active: pushed within 90 days
- Warm: pushed within 180 days
- Quiet: pushed within 365 days
- Dormant: no push in more than 365 days
- Archived: repository is archived
- Disabled: repository is disabled

Archived and disabled statuses take precedence over date-based activity status.

Thresholds must be configurable.

## Semantic grouping

Repositories should be assigned to semantic groups using configurable rules.

Rule precedence:

1. Explicit repository-name override
2. Topic-based grouping
3. Regex/name-based grouping
4. Fallback group

Each classified repository should include a grouping_reason, such as:

- override:special-repo-name
- topic:pipelines
- regex:^DMTN-[0-9]+$
- fallback

Example use cases:

- Repositories with the pipelines topic should be grouped together as a virtual monorepo.
- Repositories matching ^DMTN-[0-9]+$ should be grouped as Data Management Tech Notes.

## Configuration

Use a YAML configuration file.

Example:
```yaml
org: lsst

activity:
  active_days: 90
  warm_days: 180
  quiet_days: 365

groups:
  - name: Pipelines
    topics:
      - pipelines
    description: Virtual monorepo / coordinated pipelines repositories

  - name: Data Management Tech Notes
    regex:
      - "^DMTN-[0-9]+$"
      - "^dmtn-[0-9]+$"

  - name: Documentation
    topics:
      - documentation
      - docs

overrides:
  special-repo-name:
    group: Pipelines
    notes: Manual override
```

## Output formats

The tool should support at least:

- Markdown report
- CSV inventory
- JSON inventory

Markdown report structure:

1. Title and generation timestamp
2. Summary counts:
   - total repositories
   - active repositories
   - warm repositories
   - quiet repositories
   - dormant repositories
   - archived repositories
   - disabled repositories
3. Grouped repository sections
4. Uncategorized repositories section
5. Optional appendix containing the raw inventory table

Each group section should include:

- Group name
- Optional group description
- Number of repositories
- Table of repositories

Repository table columns:

- Repo
- Description
- Primary language
- Topics
- Last push
- Activity status
- Archived
- Disabled
- Grouping reason

## CLI interface

Suggested command:

```bash
github-org-report \
  --config report-config.yaml \
  --format markdown \
  --output repo-report.md
```

Consider using Click with subcommands to simplify and expand the user interface.
For example `github-org report` would potentially give us more flexibility later if we wanted to generate different output.

Optional flags:

```bash
--org ORG
--token TOKEN
--format markdown|csv|json
--output PATH
--include-archived
--include-disabled
--verbose
```
## Architecture

Suggested modules:

- github_client.py
  - GraphQL client
  - pagination
  - rate-limit handling
- models.py
  - repository data model
  - group rule model
- activity.py
  - activity classification logic
- grouping.py
  - semantic grouping logic
- report.py
  - markdown, CSV, and JSON output writers
- cli.py
  - command-line interface

## Testing

Add tests for:

- activity status classification
- topic-based grouping
- regex-based grouping
- override precedence
- fallback grouping
- markdown report rendering

Use mocked GitHub API responses for tests.

## Future extensions

The implementation should make it easy to add:

- commit counts over the last 30/90/365 days
- open issue and pull request counts
- default branch
- license
- branch protection status
- CODEOWNERS presence
- CI workflow presence
- release recency
- ownership/team metadata
- repo health score
