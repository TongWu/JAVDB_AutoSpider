#!/bin/bash
# =============================================================================
# Publish to Public Repository Script
# =============================================================================
# 
# This script pushes code from a private repository to a public repository
# Flow: private repo -> publish branch -> public repo dev branch
#
# Usage:
#   ./scripts/publish_to_public.sh [public_repo_url]
#
# Arguments:
#   public_repo_url - Public repository Git URL (optional, reads from config.py)
#
# =============================================================================

set -e  # Exit on error

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# =============================================================================
# Configuration
# =============================================================================
PUBLISH_BRANCH="publish"
PUBLIC_REMOTE_NAME="public"
PUBLIC_TARGET_BRANCH="dev"
PRIVATE_REMOTE_NAME=""  # Will be auto-detected (origin or private)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$PROJECT_ROOT/config.py"

# Files and directories to exclude (only git-tracked files)
# NOTE: config.py is NOT included here because it's in .gitignore
#       and should never be deleted from local filesystem
EXCLUDE_PATTERNS=(
    "reports/"
    "scripts/publish_to_public.sh"
    "docs/PUBLISH_TO_PUBLIC.md"
)

# Workflow files to modify (disable schedule)
WORKFLOW_FILES=(
    ".github/workflows/DailyIngestion.yml"
    ".github/workflows/QBFileFilter.yml"
)

# =============================================================================
# Read configuration from config.py
# =============================================================================

# Read a config value from config.py using Python
read_config_value() {
    local key="$1"
    local config_file="$2"
    
    if [ ! -f "$config_file" ]; then
        return 1
    fi
    
    # Use Python to safely read config values
    python3 -c "
import sys
sys.path.insert(0, '$(dirname "$config_file")')
try:
    import config
    value = getattr(config, '$key', None)
    if value is not None:
        print(value)
except Exception as e:
    sys.exit(1)
" 2>/dev/null
}

# Load Git configuration from config.py
load_git_config() {
    log_info "Loading Git configuration from config.py..."
    
    if [ ! -f "$CONFIG_FILE" ]; then
        log_warn "config.py not found, will use command line arguments"
        return 1
    fi
    
    # Read config values
    CONFIG_GIT_USERNAME=$(read_config_value "GIT_USERNAME" "$CONFIG_FILE")
    CONFIG_GIT_PASSWORD=$(read_config_value "GIT_PASSWORD" "$CONFIG_FILE")
    CONFIG_GIT_REPO_URL=$(read_config_value "GIT_REPO_URL" "$CONFIG_FILE")
    CONFIG_GIT_REPO_URL_REMOTE=$(read_config_value "GIT_REPO_URL_REMOTE" "$CONFIG_FILE")
    
    # Display loaded config (hide sensitive info)
    if [ -n "$CONFIG_GIT_USERNAME" ]; then
        log_info "  GIT_USERNAME: $CONFIG_GIT_USERNAME"
    fi
    if [ -n "$CONFIG_GIT_PASSWORD" ]; then
        log_info "  GIT_PASSWORD: ******** (loaded)"
    fi
    if [ -n "$CONFIG_GIT_REPO_URL" ]; then
        log_info "  GIT_REPO_URL: $CONFIG_GIT_REPO_URL"
    fi
    if [ -n "$CONFIG_GIT_REPO_URL_REMOTE" ]; then
        log_info "  GIT_REPO_URL_REMOTE: $CONFIG_GIT_REPO_URL_REMOTE"
    fi
    
    return 0
}

# Convert HTTPS URL to authenticated URL
# Format: https://github.com/user/repo.git -> https://username:password@github.com/user/repo.git
convert_to_auth_url() {
    local url="$1"
    local username="$2"
    local password="$3"
    
    if [[ "$url" == https://* ]] && [ -n "$username" ] && [ -n "$password" ]; then
        # URL encode password (handle special characters)
        local encoded_password=$(python3 -c "from urllib.parse import quote; print(quote('$password', safe=''))")
        echo "$url" | sed "s|https://|https://${username}:${encoded_password}@|"
    else
        echo "$url"
    fi
}

# Convert HTTPS URL to SSH URL
# Format: https://github.com/user/repo.git -> git@github.com:user/repo.git
convert_to_ssh_url() {
    local url="$1"
    
    if [[ "$url" == https://github.com/* ]]; then
        echo "$url" | sed 's|https://github.com/|git@github.com:|'
    elif [[ "$url" == https://*@github.com/* ]]; then
        # Remove auth info and convert to SSH
        echo "$url" | sed 's|https://[^@]*@github.com/|git@github.com:|'
    else
        echo "$url"
    fi
}

# =============================================================================
# Function definitions
# =============================================================================

# Check if we're in a git repository
check_git_repo() {
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        log_error "Current directory is not a Git repository"
        exit 1
    fi
}

# Detect private repository remote name (origin or private)
detect_private_remote() {
    # Check for 'origin' first, then 'private'
    if git remote | grep -q "^origin$"; then
        PRIVATE_REMOTE_NAME="origin"
    elif git remote | grep -q "^private$"; then
        PRIVATE_REMOTE_NAME="private"
    else
        # Use the first remote that's not 'public'
        PRIVATE_REMOTE_NAME=$(git remote | grep -v "^${PUBLIC_REMOTE_NAME}$" | head -1)
    fi
    
    if [ -z "$PRIVATE_REMOTE_NAME" ]; then
        log_error "No private remote found. Please add a remote for your private repository."
        exit 1
    fi
    
    log_info "Private remote detected: $PRIVATE_REMOTE_NAME"
}

# Check for uncommitted changes
check_uncommitted_changes() {
    if ! git diff-index --quiet HEAD --; then
        log_error "You have uncommitted changes. Please commit or stash them first"
        exit 1
    fi
}

# Save current branch
save_current_branch() {
    ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    log_info "Current branch: $ORIGINAL_BRANCH"
}

# Setup public repository remote
setup_public_remote() {
    local public_url="$1"
    local use_ssh="$2"
    
    # If no URL provided, try to get from config.py
    if [ -z "$public_url" ] && [ -n "$CONFIG_GIT_REPO_URL_REMOTE" ]; then
        public_url="$CONFIG_GIT_REPO_URL_REMOTE"
        log_info "Using GIT_REPO_URL_REMOTE from config.py: $public_url"
    fi
    
    # Convert URL based on mode
    if [ "$use_ssh" = true ] && [[ "$public_url" == https://* ]]; then
        public_url=$(convert_to_ssh_url "$public_url")
        log_info "Converted to SSH URL: $public_url"
    elif [ "$use_ssh" = false ] && [[ "$public_url" == https://* ]]; then
        # Use HTTPS with authentication
        if [ -n "$CONFIG_GIT_USERNAME" ] && [ -n "$CONFIG_GIT_PASSWORD" ]; then
            public_url=$(convert_to_auth_url "$public_url" "$CONFIG_GIT_USERNAME" "$CONFIG_GIT_PASSWORD")
            log_info "Using HTTPS with authentication"
        fi
    fi
    
    # Check if public remote already exists
    if git remote | grep -q "^${PUBLIC_REMOTE_NAME}$"; then
        existing_url=$(git remote get-url "$PUBLIC_REMOTE_NAME")
        # Hide password in display
        display_existing=$(echo "$existing_url" | sed 's|://[^:]*:[^@]*@|://***:***@|')
        log_info "Public remote already exists: $display_existing"
        
        if [ -n "$public_url" ] && [ "$public_url" != "$existing_url" ]; then
            display_new=$(echo "$public_url" | sed 's|://[^:]*:[^@]*@|://***:***@|')
            log_warn "Updating public repo URL: $display_new"
            git remote set-url "$PUBLIC_REMOTE_NAME" "$public_url"
        fi
    else
        if [ -z "$public_url" ]; then
            log_error "Public repo URL required. Provide it as argument or set GIT_REPO_URL_REMOTE in config.py"
            echo ""
            echo "Usage: $0 <public_repo_url>"
            echo "Example: $0 git@github.com:username/public-repo.git"
            echo ""
            echo "Or set in config.py:"
            echo "  GIT_REPO_URL_REMOTE = 'https://github.com/username/public-repo.git'"
            exit 1
        fi
        display_url=$(echo "$public_url" | sed 's|://[^:]*:[^@]*@|://***:***@|')
        log_info "Adding public remote: $display_url"
        git remote add "$PUBLIC_REMOTE_NAME" "$public_url"
    fi
}

# Create or update publish branch
create_publish_branch() {
    log_info "Creating/updating $PUBLISH_BRANCH branch..."
    
    # Delete publish branch if it exists
    if git show-ref --verify --quiet "refs/heads/$PUBLISH_BRANCH"; then
        log_warn "Deleting existing $PUBLISH_BRANCH branch"
        git branch -D "$PUBLISH_BRANCH"
    fi
    
    # Create new publish branch from current branch
    git checkout -b "$PUBLISH_BRANCH"
    log_success "Created $PUBLISH_BRANCH branch"
}

# Remove excluded files (only git-tracked files, preserves untracked/ignored files)
remove_excluded_files() {
    log_info "Removing excluded files from git..."
    
    for pattern in "${EXCLUDE_PATTERNS[@]}"; do
        local full_path="$PROJECT_ROOT/$pattern"
        # Only remove if file/directory exists AND is tracked by git
        if [ -e "$full_path" ]; then
            # Check if the file is tracked by git
            if git ls-files --error-unmatch "$full_path" > /dev/null 2>&1; then
                log_info "Removing from git: $pattern"
                git rm -rf "$full_path" 2>/dev/null || true
            else
                log_warn "Skipping untracked file: $pattern"
            fi
        fi
    done
}

# Disable workflow schedules
disable_workflow_schedules() {
    log_info "Disabling workflow schedules..."
    
    for workflow in "${WORKFLOW_FILES[@]}"; do
        local filepath="$PROJECT_ROOT/$workflow"
        if [ -f "$filepath" ]; then
            log_info "Processing: $workflow"
            
            # Use sed to comment out schedule section
            if [[ "$OSTYPE" == "darwin"* ]]; then
                # macOS sed
                sed -i '' 's/^  schedule:$/  # schedule: # DISABLED FOR PUBLIC REPO/g' "$filepath"
                sed -i '' 's/^    - cron:/    # - cron:/g' "$filepath"
            else
                # Linux sed
                sed -i 's/^  schedule:$/  # schedule: # DISABLED FOR PUBLIC REPO/g' "$filepath"
                sed -i 's/^    - cron:/    # - cron:/g' "$filepath"
            fi
            
            log_success "Disabled schedule in $workflow"
        else
            log_warn "File not found: $workflow"
        fi
    done
}

# Commit changes
commit_changes() {
    log_info "Committing changes..."
    
    git add -A
    
    # Check if there are changes to commit
    if git diff --cached --quiet; then
        log_info "No changes to commit"
    else
        git commit -m "Prepare for public release

Changes:
- Removed private reports directory
- Removed publish scripts and docs
- Disabled scheduled workflows for public repo

This commit is auto-generated by publish_to_public.sh"
        log_success "Changes committed"
    fi
}

# Push to publish branch (private repo)
push_to_publish() {
    log_info "Pushing to $PRIVATE_REMOTE_NAME/$PUBLISH_BRANCH..."
    
    # If using HTTPS mode, construct authenticated URL for push
    if [ "$USE_SSH_MODE" = false ] && [ -n "$CONFIG_GIT_USERNAME" ] && [ -n "$CONFIG_GIT_PASSWORD" ] && [ -n "$CONFIG_GIT_REPO_URL" ]; then
        local auth_url=$(convert_to_auth_url "$CONFIG_GIT_REPO_URL" "$CONFIG_GIT_USERNAME" "$CONFIG_GIT_PASSWORD")
        log_info "Using HTTPS with authentication for private repo"
        git push -f "$auth_url" "$PUBLISH_BRANCH"
    else
        git push -f "$PRIVATE_REMOTE_NAME" "$PUBLISH_BRANCH"
    fi
    log_success "Pushed to $PRIVATE_REMOTE_NAME/$PUBLISH_BRANCH"
}

# Push to public repository
push_to_public() {
    log_info "Pushing to public repository $PUBLIC_REMOTE_NAME/$PUBLIC_TARGET_BRANCH..."
    
    # If using HTTPS mode, construct authenticated URL for push
    if [ "$USE_SSH_MODE" = false ] && [ -n "$CONFIG_GIT_USERNAME" ] && [ -n "$CONFIG_GIT_PASSWORD" ] && [ -n "$CONFIG_GIT_REPO_URL_REMOTE" ]; then
        local auth_url=$(convert_to_auth_url "$CONFIG_GIT_REPO_URL_REMOTE" "$CONFIG_GIT_USERNAME" "$CONFIG_GIT_PASSWORD")
        log_info "Using HTTPS with authentication for public repo"
        git push -f "$auth_url" "$PUBLISH_BRANCH:$PUBLIC_TARGET_BRANCH"
    else
        git push -f "$PUBLIC_REMOTE_NAME" "$PUBLISH_BRANCH:$PUBLIC_TARGET_BRANCH"
    fi
    log_success "Pushed to public repository $PUBLIC_REMOTE_NAME/$PUBLIC_TARGET_BRANCH"
}

# Restore original branch
restore_original_branch() {
    log_info "Restoring to original branch: $ORIGINAL_BRANCH"
    git checkout "$ORIGINAL_BRANCH"
}

# Cleanup function
cleanup() {
    if [ -n "$ORIGINAL_BRANCH" ]; then
        log_info "Cleanup: restoring to original branch..."
        git checkout "$ORIGINAL_BRANCH" 2>/dev/null || true
    fi
}

# Show help
show_help() {
    echo "
=============================================================================
Publish to Public Repository Script
=============================================================================

This script pushes code from a private repository to a public repository
Flow: private repo -> publish branch -> public repo dev branch

Configuration:
  The script reads the following from config.py:
  - GIT_USERNAME          Git username (for HTTPS auth)
  - GIT_PASSWORD          Git password/token (for HTTPS auth)
  - GIT_REPO_URL          Private repository URL
  - GIT_REPO_URL_REMOTE   Public repository URL

Usage:
  $0 [options] [public_repo_url]

Arguments:
  public_repo_url    Public repository Git URL (optional, reads from config.py)

Options:
  -h, --help         Show this help message
  --dry-run          Simulate run without pushing
  --skip-public      Only push to publish branch, skip public repo
  --ssh              Use SSH URL instead of HTTPS (requires SSH key)
  --https            Use HTTPS URL with auth from config.py (default)

Examples:
  # Use config.py settings with HTTPS auth (recommended, default)
  $0

  # Use SSH mode (requires SSH key configured)
  $0 --ssh

  # Manually specify public repo URL
  $0 https://github.com/username/public-repo.git

  # Only push to publish branch
  $0 --skip-public

  # Dry run to check what would happen
  $0 --dry-run

Excluded files/directories:
$(printf '  - %s\n' "${EXCLUDE_PATTERNS[@]}")

Workflows with disabled schedule:
$(printf '  - %s\n' "${WORKFLOW_FILES[@]}")
"
}

# =============================================================================
# Main flow
# =============================================================================

main() {
    local public_url=""
    local dry_run=false
    local skip_public=false
    USE_SSH_MODE=false  # Default to HTTPS (uses GIT_USERNAME/GIT_PASSWORD from config.py)
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            --dry-run)
                dry_run=true
                shift
                ;;
            --skip-public)
                skip_public=true
                shift
                ;;
            --ssh)
                USE_SSH_MODE=true
                shift
                ;;
            --https)
                USE_SSH_MODE=false
                shift
                ;;
            *)
                public_url="$1"
                shift
                ;;
        esac
    done
    
    # Set error handler
    trap cleanup EXIT
    
    echo ""
    echo "=============================================="
    echo "  Publish to Public Repository"
    echo "=============================================="
    echo ""
    
    # Change to project root directory
    cd "$PROJECT_ROOT"
    log_info "Working directory: $PROJECT_ROOT"
    
    # Load Git config from config.py
    load_git_config || true
    
    # Pre-flight checks
    check_git_repo
    detect_private_remote
    check_uncommitted_changes
    save_current_branch
    
    # Setup public repository remote
    setup_public_remote "$public_url" "$USE_SSH_MODE"
    
    # Create publish branch
    create_publish_branch
    
    # Remove excluded files
    remove_excluded_files
    
    # Disable workflow schedules
    disable_workflow_schedules
    
    # Commit changes
    commit_changes
    
    if [ "$dry_run" = true ]; then
        log_warn "Dry run mode: skipping push"
    else
        # Push to publish branch
        push_to_publish
        
        # Push to public repository
        if [ "$skip_public" = false ]; then
            push_to_public
        else
            log_info "Skipping push to public repository"
        fi
    fi
    
    # Restore original branch
    restore_original_branch
    
    echo ""
    echo "=============================================="
    log_success "Publish process completed!"
    echo "=============================================="
    echo ""
    
    if [ "$skip_public" = false ] && [ "$dry_run" = false ]; then
        public_url=$(git remote get-url "$PUBLIC_REMOTE_NAME" 2>/dev/null || echo "not set")
        echo "Private repo publish branch: $PRIVATE_REMOTE_NAME/$PUBLISH_BRANCH"
        echo "Public repo target branch: $PUBLIC_REMOTE_NAME/$PUBLIC_TARGET_BRANCH"
        echo "Public repo URL: $public_url"
    fi
}

# Run main function
main "$@"
