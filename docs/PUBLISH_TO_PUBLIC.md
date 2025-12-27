# Publish to Public Repository Guide

This document explains how to safely push code from a private repository to a public repository.

## Overview

Publishing flow:

```
Private repo (main/dev) 
    ↓
Private repo (publish branch) - Clean sensitive content
    ↓
Public repo (dev branch)
```

## Configuration

The script automatically reads the following from `config.py`:

| Config Item           | Description                           |
| --------------------- | ------------------------------------- |
| `GIT_USERNAME`        | Git username (for HTTPS auth)         |
| `GIT_PASSWORD`        | Git password or Personal Access Token |
| `GIT_REPO_URL`        | Private repository URL                |
| `GIT_REPO_URL_REMOTE` | Public repository URL                 |

Example configuration (in `config.py`):

```python
GIT_USERNAME = 'your-username'
GIT_PASSWORD = 'ghp_xxxxxxxxxxxx'  # GitHub Personal Access Token
GIT_REPO_URL = 'https://github.com/username/private-repo.git'
GIT_REPO_URL_REMOTE = 'https://github.com/username/public-repo.git'
```

## Automatic Processing During Publishing

The script automatically performs the following:

### 1. Excluded Files/Directories

The following files and directories are removed during publishing:

| Path                           | Reason                                               |
| ------------------------------ | ---------------------------------------------------- |
| `reports/`                     | Contains runtime data, not suitable for public       |
| `scripts/publish_to_public.sh` | The publish script itself, not needed in public repo |
| `docs/PUBLISH_TO_PUBLIC.md`    | This document, not needed in public repo             |
| `config.py`                    | Contains sensitive configuration, cannot be public   |

### 2. Modified Workflow Files

The following GitHub Actions workflow files are modified to disable scheduled tasks:

| File                                   | Modification                   |
| -------------------------------------- | ------------------------------ |
| `.github/workflows/DailyIngestion.yml` | Comment out `schedule` section |
| `.github/workflows/QBFileFilter.yml`   | Comment out `schedule` section |

This prevents forks of the public repository from automatically running scheduled tasks.

## Usage

### Prerequisites

1. Ensure `GIT_REPO_URL_REMOTE` (public repo URL) is configured in `config.py`
2. Ensure all changes are committed (no uncommitted changes)
3. Ensure you have push permission to the public repository:
   - **SSH mode** (default): SSH key must be configured
   - **HTTPS mode**: `GIT_USERNAME` and `GIT_PASSWORD` must be configured in `config.py`

### Recommended Usage (Using config.py)

The script automatically reads the public repo URL from `config.py`:

```bash
# Use SSH mode (default, recommended)
./scripts/publish_to_public.sh

# Use HTTPS mode (automatically uses auth from config.py)
./scripts/publish_to_public.sh --https
```

### Manually Specify Repository URL

You can also manually specify the public repo URL:

```bash
# Use SSH URL
./scripts/publish_to_public.sh git@github.com:username/public-repo.git

# Use HTTPS URL
./scripts/publish_to_public.sh https://github.com/username/public-repo.git
```

### Command Options

```bash
# Show help
./scripts/publish_to_public.sh --help

# Dry run (simulate without pushing)
./scripts/publish_to_public.sh --dry-run

# Only push to publish branch, skip public repo
./scripts/publish_to_public.sh --skip-public

# Use SSH mode (default)
./scripts/publish_to_public.sh --ssh

# Use HTTPS mode
./scripts/publish_to_public.sh --https
```

## Detailed Flow

### Step 1: Create publish branch

The script creates a new `publish` branch from the current branch:

```bash
git checkout -b publish
```

### Step 2: Remove sensitive content

The script removes the `reports/` directory and publish-related scripts/docs:

```bash
git rm -rf reports/
git rm -rf scripts/publish_to_public.sh
git rm -rf docs/PUBLISH_TO_PUBLIC.md
git rm -rf config.py
```

### Step 3: Disable Schedule

The script modifies workflow files to comment out the `schedule:` section:

**Before:**
```yaml
on:
  workflow_dispatch:
  schedule:
    - cron: '0 10 * * *'
```

**After:**
```yaml
on:
  workflow_dispatch:
  # schedule: # DISABLED FOR PUBLIC REPO
    # - cron: '0 10 * * *'
```

### Step 4: Commit changes

```bash
git add -A
git commit -m "Prepare for public release"
```

### Step 5: Push to publish branch

```bash
git push -f origin publish
```

### Step 6: Push to public repository

```bash
git push -f public publish:dev
```

### Step 7: Restore original branch

The script automatically switches back to the original branch.

## Remote Configuration

The script automatically adds a remote named `public` pointing to the public repository (read from `GIT_REPO_URL_REMOTE` in `config.py`):

```bash
# View current remotes
git remote -v

# SSH mode output example:
# origin  git@github.com:username/private-repo.git (fetch)
# origin  git@github.com:username/private-repo.git (push)
# public  git@github.com:username/public-repo.git (fetch)
# public  git@github.com:username/public-repo.git (push)

# HTTPS mode output example (with auth):
# public  https://username:***@github.com/username/public-repo.git (fetch)
# public  https://username:***@github.com/username/public-repo.git (push)
```

Manual management:

```bash
# Add public remote
git remote add public git@github.com:username/public-repo.git

# Update public remote URL
git remote set-url public git@github.com:username/new-public-repo.git

# Remove public remote
git remote remove public
```

## Important Notes

### ⚠️ Security Reminders

1. **Do not set secrets in the public repo** - The public repo workflow has no secrets, and shouldn't have any
2. **Check for sensitive info** - Before publishing, confirm no hardcoded passwords or API keys in the code
3. **Review changes** - Use `--dry-run` mode first to check what will be published

### FAQ

#### Q: Error "You have uncommitted changes"
A: Commit or stash your changes first:
```bash
git add .
git commit -m "Your commit message"
```

#### Q: Push failed, permission denied
A: Check SSH key configuration:
```bash
ssh -T git@github.com
```

#### Q: How to change the public repo target branch?
A: Edit the `PUBLIC_TARGET_BRANCH` variable in the script:
```bash
PUBLIC_TARGET_BRANCH="main"  # Change to target branch name
```

#### Q: Can the publish branch be deleted?
A: Yes, but it's recommended to keep it for history. Each script run overwrites this branch.

## Rollback

If you need to rollback after publishing:

```bash
# Rollback to previous commit in public repo
git push -f public HEAD~1:dev

# Or push a specific commit
git push -f public <commit-hash>:dev
```

## Automation (Optional)

To automate publishing in CI/CD, create a GitHub Actions workflow:

```yaml
name: Publish to Public Repo

on:
  workflow_dispatch:
  push:
    branches:
      - main
    paths-ignore:
      - 'reports/**'
      - 'docs/**'

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      
      - name: Setup SSH
        uses: webfactory/ssh-agent@v0.9.0
        with:
          ssh-private-key: ${{ secrets.PUBLIC_REPO_DEPLOY_KEY }}
      
      - name: Run publish script
        run: |
          chmod +x ./scripts/publish_to_public.sh
          ./scripts/publish_to_public.sh git@github.com:username/public-repo.git
```

**Note**: Automated publishing requires additional `PUBLIC_REPO_DEPLOY_KEY` secret configuration.