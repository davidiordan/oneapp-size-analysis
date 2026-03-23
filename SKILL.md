---
name: git-archaeology
description: >
  Use this skill whenever a user wants to trace the history of a line or block of code in a file
  that lives inside a Git repository. Triggers include: "who wrote this", "why was this changed",
  "what PR introduced this", "trace this code back", "git blame", "find the Jira ticket for this",
  "show me the history of this line", "dig into this commit", "go back further in history",
  "who changed this and why", or any request that involves understanding the origin or evolution
  of code in a file. Also trigger when a user pastes code and asks where it came from or why it
  exists. This skill handles the full archaeology loop: blame → commit → GitHub PR (via gh CLI)
  → Jira ticket, and supports walking back through time commit-by-commit until the original
  introduction.
allowed-tools:
  - Bash(git blame:*)
  - Bash(git log:*)
  - Bash(git show:*)
  - Bash(git rev-parse:*)
  - Bash(git remote:*)
  - Bash(gh auth:*)
  - Bash(gh pr:*)
  - Bash(gh repo:*)
  - Bash(gh api:*)
  - Bash(curl:*)
---

# Git Archaeology Skill

Trace any line or block of code back through its full history: from `git blame`, to the commit,
to the GitHub Pull Request that introduced it (via the `gh` CLI), to the Jira ticket behind it —
and keep walking back until you hit the original commit.

---

## Overview of the Loop

```
git blame -L <lines> --porcelain <commit> -- <file>
        ↓
  Commit SHA + author + date + summary
        ↓
  git show --stat <sha>
        ↓
  gh api repos/{owner}/{repo}/commits/<sha>/pulls
        ↓
  gh pr view <number>  →  full PR detail + body
        ↓
  Extract Jira key  →  curl Jira REST API
        ↓
  [Prompt: go further back?]
        ↓
  git log --pretty=%P -n 1 <sha>  →  parent SHA
        ↓
  git blame --porcelain <parent_sha> -- <file>  (repeat)
```

---

## Step 1: Preflight Checks

### 1a — Confirm git repo

```bash
git rev-parse --is-inside-work-tree
```

If this fails, stop and tell the user the current directory is not inside a git repository.

### 1b — Confirm `gh` is installed and authenticated

```bash
gh --version
gh auth status
```

- If `gh` is not installed → tell the user to install it from https://cli.github.com
- If `gh` is installed but not authenticated → tell the user to run `gh auth login`

### 1c — Gather inputs

Collect (infer from context where possible; ask only for what's missing):

| Input | How to get it |
|---|---|
| **File path** | From user or current editor context |
| **Line range** | From user (e.g. `42` or `40,50`) |
| **Starting commit** | Default: `HEAD`; user can specify any SHA or ref |
| **Jira base URL** | e.g. `https://acme.atlassian.net` — ask if not provided; skip Jira step if absent |

The `owner/repo` is resolved automatically:

```bash
gh repo view --json nameWithOwner --jq .nameWithOwner
```

---

## Step 2: Run `git blame`

```bash
git blame -L <startLine>,<endLine> --porcelain <commit> -- <file>
```

Parse the porcelain output to extract per-hunk:
- `commit_sha` — 40-char hex at the start of each hunk header
- `author` — from `author` field
- `author-time` — Unix timestamp; convert to human-readable date
- `summary` — commit subject line
- `filename` — use this (not the current path) for rename-aware history traversal

**If multiple hunks blame to different commits:** group and present them, then ask the user which commit(s) to investigate.

**Detecting the boundary:** check if this SHA has already been investigated (you're looping) or if it has no parents (root commit):

```bash
git log --pretty=%P -n 1 <commit_sha>
# Empty output = root commit; end the loop and tell the user
```

---

## Step 3: Show Commit Detail

```bash
git show --stat <commit_sha>
```

Display:
- Short SHA + full SHA
- Author name + email + date
- Commit subject + body
- Files changed summary

---

## Step 4: Find the PR via `gh`

**Primary — search by commit SHA:**

```bash
gh pr list \
  --search "<commit_sha>" \
  --state merged \
  --json number,title,url,author,mergedAt,baseRefName \
  --limit 5
```

**Fallback — GitHub API via `gh`:**

```bash
gh api repos/{owner}/{repo}/commits/<commit_sha>/pulls \
  --header "Accept: application/vnd.github+json"
```

(`gh` resolves `{owner}/{repo}` from the current repo automatically when run inside it.)

**If multiple PRs returned:** list them and ask the user which to investigate.

**If no PR found:** commit landed directly on the default branch (no PR). Show commit detail only, offer to continue walking history.

---

## Step 5: Display the PR

```bash
gh pr view <number> \
  --json number,title,url,author,mergedAt,baseRefName,body,labels,reviews
```

Show the user:
- PR number + title + URL
- Author + merge date + target branch
- Full PR description body
- Labels and reviewers if present

**Offer to open in browser:**
```bash
gh pr view <number> --web
```
Ask if they'd like to view it in GitHub — useful for long descriptions or linked comments.

---

## Step 6: Extract Jira Ticket Key

Scan the PR title and full body using this regex pattern:

```
[A-Z][A-Z0-9]+-[0-9]+
```

Examples: `PROJ-123`, `BE-4567`, `DATA-89`

- **One match:** proceed automatically
- **Multiple matches:** list all, ask which to fetch
- **No match:** inform the user, let the PR description stand on its own. Offer to search Jira by keyword from the PR title if a base URL is available.

---

## Step 7: Fetch the Jira Ticket

```bash
curl -s \
  -u "${JIRA_EMAIL}:${JIRA_API_TOKEN}" \
  -H "Accept: application/json" \
  "${JIRA_BASE_URL}/rest/api/3/issue/<TICKET-KEY>"
```

**Credential resolution order:**
1. Env vars: `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_BASE_URL`
2. `~/.config/jira/credentials` or similar dotfile
3. Prompt the user if neither is found

Display:
- Ticket key + summary
- Status + priority + issue type
- Reporter → Assignee
- Description (render as plain text)
- Linked issues, sprint, fix version (if present)

---

## Step 8: Prompt to Continue Walking Back

After each full iteration, **always ask the user:**

> "Want to go further back? I can blame the parent commit to see what this code looked like before this change."

If yes:

```bash
# Get parent SHA (use first parent for merge commits)
git log --pretty=%P -n 1 <current_commit_sha>

# Re-blame at the parent, using the historical filename from porcelain output
git blame -L <startLine>,<endLine> --porcelain <parent_sha> -- <historical_filename>
```

**Line drift:** line numbers shift between commits. After getting the parent blame, show the user what those lines contained and confirm whether they want to continue investigating those specific lines.

**Rename detection:** use the `filename` field from the porcelain output — it may differ from the current file path if the file was renamed.

**Merge commits (2 parents):** ask which branch to follow:
> "This is a merge commit. Investigate the feature branch (parent 1: `<sha>`) or the base branch (parent 2: `<sha>`)?"

Repeat Steps 3–8 until:
- The user says stop
- A commit has no parents → announce **"This is the original commit — no further history exists."**

---

## Edge Cases

| Situation | Handling |
|---|---|
| Merge commit | Ask user which parent to follow |
| Binary file | Blame isn't meaningful — inform user |
| File didn't exist at that commit | `git blame` errors — tell user the file wasn't present at that point |
| Shallow clone | Blame may fail — tell user to run `git fetch --unshallow` |
| No GitHub remote | Skip Steps 4–6, show commit detail only |
| No Jira base URL | Skip Step 7, show PR description and let user find ticket manually |
| `gh` not installed | Direct to https://cli.github.com |
| `gh` not authenticated | Tell user to run `gh auth login` |

---

## Output Format

Structure each iteration clearly so history is easy to follow:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍 Iteration 1 — Blame at HEAD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Commit:   abc1234 (2024-03-15) by Jane Smith
Message:  "fix: handle null case in payment processor"

📦 Pull Request
  #482 — Fix null pointer in payment processor
  Merged: 2024-03-15 by janesmith → main
  https://github.com/acme/backend/pull/482

🎫 Jira Ticket
  PAY-891 — Payment processor crashes on null user
  Status: Done | Priority: High | Type: Bug
  Reporter: Bob Jones → Assigned: Jane Smith
  Sprint: Q1 2024 Sprint 3

↩️  Want to go further back? (Parent: def5678)
```

---

## Tips

- **Always show the blame result first** before jumping to the PR — author + date alone often answers the question.
- **`gh` handles all GitHub auth** — no token management needed; just ensure `gh auth status` passes.
- **Line drift is real** — be explicit with the user when line numbers have shifted at a parent commit.
- **Keep the loop conversational** — summarize what was found each iteration, then ask clearly whether to continue. Never auto-loop.
- **Gracefully degrade** — if no PR, no Jira, or no `gh`, provide what's available rather than failing hard.
