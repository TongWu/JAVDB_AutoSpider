# Publish to Public Repository Guide

This document explains how code is automatically synchronized from the private repository to the public repository.

## Overview

```
Private repo (main branch)
    ↓ (auto-trigger on push, or manual trigger)
GitHub Actions workflow (git-filter-repo)
    ↓ (rewrite history, remove sensitive files)
Private repo (public-sync branch)
    ↓ (force push)
Public repo (dev branch)
```

## How It Works

The publishing process uses `git-filter-repo` to:

1. **Completely remove excluded files from the ENTIRE git history**
   - The public repo will have NO trace of files like `reports/`, logs, or this workflow itself
   - This is more secure than simply deleting files in the latest commit

2. **Prune commits that become empty after file removal**
   - e.g., "Auto-commit" commits that only modified `reports/` will be removed
   - This keeps the public repo history clean and meaningful

3. **Preserve original commit timestamps**
   - Your development history remains accurate in the public repo

## Configuration

All publishing configuration is centralized in `.publish-config.yml`:

```yaml
# Files/directories to exclude from public repo
exclude_paths:
  - "reports/"
  - "logs/"
  - "Daily Report/"
  - "Ad Hoc/"
  - "docs/PUBLISH_TO_PUBLIC.md"
  - ".github/workflows/block-public-sync-to-main.yml"
  - ".github/workflows/publish-to-public.yml"
  - ".publish-config.yml"

# Workflow modifications
workflow_modifications:
  disable_schedule:
    - ".github/workflows/DailyIngestion.yml"
    - ".github/workflows/QBFileFilter.yml"
    - ".github/workflows/StaleSessionCleanup.yml"
  enable_push_trigger:
    - ".github/workflows/docker-publish-ghcr.yml"
    - ".github/workflows/TestIngestion.yml"

# Target branches
branches:
  publish_branch: "public-sync"
  public_target_branch: "dev"
```

## Triggers

The workflow is triggered in two ways:

### 1. Automatic (on push to main)

```yaml
push:
  branches:
    - main
  paths-ignore:
    - 'reports/**'
    - 'logs/**'
    - '*.csv'
```

When you push code changes to `main`, the workflow automatically runs and syncs to the public repo.

### 2. Manual (workflow_dispatch)

Go to GitHub Actions → "Publish to Public Repository" → "Run workflow"

Options:
- **dry_run**: Check the checkbox to simulate without actually pushing

## Required Secrets

Configure these in GitHub repository settings → Secrets and variables → Actions:

| Secret | Description |
|--------|-------------|
| `DEPLOY_KEY` | SSH key for accessing the private repo |
| `GIT_USERNAME` | Git username for public repo authentication |
| `GIT_PASSWORD` | Git password/Personal Access Token for public repo |
| `GIT_REPO_URL_REMOTE` | Public repository URL (HTTPS format) |

## What Happens During Publishing

### Step 1: Checkout & Create Branch
- Full git history is checked out
- A new `public-sync` branch is created

### Step 2: Rewrite History
- `git-filter-repo` removes all excluded files from every commit
- Commits that become empty are pruned
- Original timestamps are preserved

### Step 3: Workflow Modifications
- Scheduled triggers are disabled (prevents forks from auto-running)
- Docker push triggers are enabled for the public repo
- `runs-on:` lines tagged with `# PUBLIC_RUNNER: <name>` are rewritten to use
  `<name>` so jobs that run on private self-hosted runners fall back to a
  GitHub-hosted equivalent in the public repo

### Step 4: Push to Public Repo
- Force push to `public-sync` branch in private repo
- Force push to `dev` branch in public repo

## Important Notes

### Force Push Warning

Because `git-filter-repo` rewrites commit history, the workflow always performs a **force push** to the public repo. This is expected behavior.

### Security Reminders

1. **Never commit secrets** - Use environment variables or GitHub secrets
2. **Review `.publish-config.yml`** - Ensure all sensitive paths are listed
3. **Use dry-run first** - Test with dry_run=true before actual publishing

## FAQ

### Q: How do I add a new file/directory to exclude?

Edit `.publish-config.yml` and add the path to `exclude_paths`:

```yaml
exclude_paths:
  - "reports/"
  - "your-new-path/"  # Add here
```

### Q: How do I make a self-hosted job work on the public repo?

The public repo has no access to the private self-hosted runner pool, so any
job with `runs-on: self-hosted` (or another private label) would hang there.
Annotate the line inline with the GitHub-hosted runner you want the public
repo to use:

```yaml
build-arm:
  runs-on: self-hosted  # PUBLIC_RUNNER: ubuntu-24.04-arm
```

During publish, `publish-to-public.yml` rewrites every line matching this
pattern to `runs-on: <public-runner>` (here `ubuntu-24.04-arm`). The marker
is scanned across all `.github/workflows/*.yml` files, so no extra entry in
`.publish-config.yml` is required.

Limitations: only single-token `runs-on` values are supported (e.g.
`self-hosted`, `ubuntu-latest`). Array forms like `[self-hosted, linux]` or
expressions like `${{ matrix.runner }}` are intentionally out of scope and
would need a richer marker scheme.

### Q: How do I change the target branch?

Edit `.publish-config.yml`:

```yaml
branches:
  public_target_branch: "main"  # Change from "dev" to "main"
```

### Q: The workflow failed, how do I debug?

1. Check the workflow run logs in GitHub Actions
2. Run with `dry_run: true` to see what would happen without pushing
3. Verify all secrets are correctly configured

### Q: How do I manually rollback?

```bash
# Rollback to previous commit in public repo
git push -f public HEAD~1:dev

# Or push a specific commit
git push -f public <commit-hash>:dev
```

## Comparison: Old Script vs New Workflow

| Feature | Old Script | New Workflow |
|---------|------------|--------------|
| Trigger | Manual only | Auto + Manual |
| History cleanup | Delete files in latest commit | Remove from entire history |
| Empty commits | Still visible | Pruned automatically |
| Configuration | Hardcoded in script | Centralized YAML config |
| Runs on | Local machine | GitHub Actions |
