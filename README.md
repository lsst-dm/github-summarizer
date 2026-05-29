# github-summarizer
Summarize the repos in a GitHub org and generate a report

## Grouping topic exemptions

Use top-level `ignored_topics` for broadly applied GitHub topics that should
not influence grouping. Ignored topics still appear in reports and metadata,
but they do not match configured topic rules or create automatic topic groups.

```yaml
ignored_topics: [hacktoberfest]
```

`auto_group_by_topic.ignore_topics` remains available for topics that should
only be skipped by automatic topic clusters.

## CSV metadata updates

Export a CSV report, edit only the `description` and `topics` columns in a
spreadsheet, save it as CSV, then apply the intentional edits:

```sh
github-summarizer report --config configs/lsst-config.yaml --format csv --output repos-baseline.csv
github-summarizer apply-metadata --config configs/lsst-config.yaml --baseline repos-baseline.csv --input repos-edited.csv
```

By default `apply-metadata` prompts before each GitHub update. Use `--dry-run`
to preview updates or `--yes` to apply all safe updates without prompting. A
safe update means the live GitHub value still matches the baseline CSV value;
stale edits are skipped unless `--allow-stale` is supplied.
