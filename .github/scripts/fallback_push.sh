#!/usr/bin/env bash
# fallback_push.sh — When all push retries are exhausted, fork a new branch
# from the current HEAD and open a PR for manual conflict resolution.
#
# Usage:
#   fallback_push.sh <task-name> <target-branch> [preserved-commit]
#
# Arguments:
#   task-name        — Short identifier used in the fallback branch name / PR title.
#   target-branch    — Branch the eventual PR should merge into.
#   preserved-commit — (Optional) The commit SHA the caller wants the fallback
#                      branch to be based on.  When supplied, the script resets
#                      HEAD to this SHA *after* aborting any in-progress rebase
#                      or merge, guaranteeing the fallback branch is created
#                      from the intended commit even if the abort rewound HEAD.
#
# Environment variables (set by caller):
#   GH_TOKEN      — GitHub token with pull-requests:write scope (required for PR creation)
#   TZ            — Timezone for datetime stamp (optional, defaults to UTC)
#   GITHUB_*      — Standard GitHub Actions context vars (optional, enriches PR body)

set -euo pipefail

TASK_NAME="${1:?Usage: fallback_push.sh <task-name> <target-branch> [preserved-commit]}"
TARGET_BRANCH="${2:?Usage: fallback_push.sh <task-name> <target-branch> [preserved-commit]}"
LOCAL_COMMIT="${3:-}"

DATETIME=$(TZ="${TZ:-UTC}" date +'%Y%m%d-%H%M%S')
SAFE_TASK_NAME=$(echo "$TASK_NAME" | tr ' ' '-' | tr '[:upper:]' '[:lower:]')
FALLBACK_BRANCH="auto/${SAFE_TASK_NAME}/${DATETIME}"

echo ""
echo "============================================"
echo "FALLBACK: Creating branch for manual merge"
echo "============================================"
echo "Task:             $TASK_NAME"
echo "Target branch:    $TARGET_BRANCH"
echo "Fallback branch:  $FALLBACK_BRANCH"
if [ -n "$LOCAL_COMMIT" ]; then
  echo "Preserved commit: $LOCAL_COMMIT"
fi

git rebase --abort 2>/dev/null || true
git merge --abort 2>/dev/null || true

# After aborting a rebase/merge HEAD may have been moved back to the
# pre-operation state, losing the auto-generated commit the caller wanted
# to preserve.  If the caller supplied that commit's SHA, force HEAD back
# to it before branching off so the fallback branch contains the intended
# payload.
if [ -n "$LOCAL_COMMIT" ]; then
  if git cat-file -e "${LOCAL_COMMIT}^{commit}" 2>/dev/null; then
    git reset --hard "$LOCAL_COMMIT"
  else
    echo "::error::Preserved commit '$LOCAL_COMMIT' not found in local repo — cannot safely create fallback branch."
    exit 1
  fi
fi

echo "Current HEAD: $(git rev-parse HEAD)"
echo "Files in HEAD commit:"
git diff-tree --no-commit-id --name-only -r HEAD 2>/dev/null || true
echo ""

git checkout -b "$FALLBACK_BRANCH"

PUSH_RETRIES=3
PUSH_DELAY=5
for attempt in $(seq 1 $PUSH_RETRIES); do
  if git push -u origin "$FALLBACK_BRANCH"; then
    echo "Fallback branch pushed successfully"
    break
  fi
  if [ "$attempt" -eq "$PUSH_RETRIES" ]; then
    echo "::error::Failed to push fallback branch after $PUSH_RETRIES attempts"
    exit 1
  fi
  echo "Push attempt $attempt failed, retrying in ${PUSH_DELAY}s..."
  sleep "$PUSH_DELAY"
  PUSH_DELAY=$((PUSH_DELAY * 2))
done

RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-unknown}/actions/runs/${GITHUB_RUN_ID:-0}"

PR_TITLE="[Auto] ${TASK_NAME} — conflict resolution required (${DATETIME})"

read -r -d '' PR_BODY <<EOF || true
## Automated Conflict Resolution PR

This PR was **automatically created** because the **${TASK_NAME}** workflow could not push results directly to \`${TARGET_BRANCH}\` after exhausting all retry attempts.

### What happened

The \`${TARGET_BRANCH}\` branch diverged from the workflow's working copy, and automatic rebase/merge strategies could not resolve the conflicts.

### Action required

1. **Review** the file changes in this PR
2. **Resolve** any merge conflicts with \`${TARGET_BRANCH}\`
3. **Merge** this PR to preserve the pipeline/migration results

### Details

| Field | Value |
|-------|-------|
| Workflow | \`${TASK_NAME}\` |
| Fallback branch | \`${FALLBACK_BRANCH}\` |
| Target branch | \`${TARGET_BRANCH}\` |
| Timestamp | \`${DATETIME}\` |
| Run | [#${GITHUB_RUN_ID:-N/A}](${RUN_URL}) |

> **Important**: This branch contains auto-generated data (reports, databases, CSVs) produced by the pipeline. Please do not close this PR without reviewing — the data would be lost.
EOF

echo ""
echo "Creating pull request..."
if gh pr create \
  --base "$TARGET_BRANCH" \
  --head "$FALLBACK_BRANCH" \
  --title "$PR_TITLE" \
  --body "$PR_BODY"; then
  echo "Pull request created successfully"
else
  echo "::warning::Failed to create PR via gh CLI. The fallback branch '${FALLBACK_BRANCH}' has been pushed — please create a PR manually."
fi

echo ""
echo "============================================"
echo "Fallback complete"
echo "  Branch: $FALLBACK_BRANCH"
echo "  Target: $TARGET_BRANCH"
echo "============================================"
