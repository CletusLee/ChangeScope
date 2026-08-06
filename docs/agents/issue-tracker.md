# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

A GitHub remote has not been configured yet. Until one exists, pass the target repository explicitly using the appropriate `gh --repo <owner>/<repo>` option.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`.
- **Read an issue**: `gh issue view <number> --comments`, including its labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments` with appropriate label and state filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply/remove labels**: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- **Close an issue**: `gh issue close <number> --comment "..."`

After a GitHub remote is configured, `gh` can infer the repository when run inside this clone.

## Pull requests as a triage surface

**PRs as a request surface: no.**

Set this to `yes` if the repository later treats external pull requests as feature requests. The `/triage` skill reads this flag.

GitHub shares one number space across issues and pull requests. A bare `#42` may be either; resolve it with `gh pr view 42`, then fall back to `gh issue view 42`.

## When a skill says “publish to the issue tracker”

Create a GitHub issue.

## When a skill says “fetch the relevant ticket”

Run `gh issue view <number> --comments`.

## Wayfinding operations

The `/wayfinder` skill represents a map as one issue and its work as child issues.

- **Map**: An issue labelled `wayfinder:map` containing Notes, Decisions-so-far, and Fog.
- **Child ticket**: A GitHub sub-issue labelled `wayfinder:<type>`, where type is `research`, `prototype`, `grilling`, or `task`.
- **Fallback linking**: If sub-issues are unavailable, add the child to a task list in the map and put `Part of #<map>` at the top of the child.
- **Blocking**: Prefer GitHub’s native issue dependencies. If unavailable, use a `Blocked by: #<n>` line at the top of the child.
- **Frontier query**: Select the first open, unassigned child in map order with no open blockers.
- **Claim**: `gh issue edit <number> --add-assignee @me`.
- **Resolve**: Comment with the result, close the child, and append a context pointer to the map’s Decisions-so-far section.
