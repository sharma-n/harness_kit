# Issue tracker: GitHub Issues

Issues for this repo are tracked in GitHub Issues at https://github.com/sharma-n/harness_kit/issues.

The engineering skills (`to-tickets`, `triage`, `to-spec`, `qa`) use the GitHub CLI (`gh`) to:
- List and filter issues
- Create new issues
- Update labels, assignees, and status

## Pull requests as a request surface

By default, external pull requests are **not** added to the triage queue. If you want PRs to appear in triage, edit this file and set `external_prs_in_triage: true` under the GitHub section.

## Workflow

1. **New work** arrives as an issue or pull request
2. **Triage** labels it with one of the five canonical roles (see `docs/agents/triage-labels.md`)
3. **Agent skills** (`to-spec`, `to-tickets`, `qa`) read from and write to the tracker
