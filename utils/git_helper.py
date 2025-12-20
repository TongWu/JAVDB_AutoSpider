"""
Git Helper Module for JAVDB AutoSpider

Provides git commit/push functionality that can be shared across scripts.
Supports two modes:
1. Pipeline mode (--from-pipeline): Use GIT_USERNAME/GIT_PASSWORD from config
2. Standalone mode: Try github-actions[bot] first, fallback to GIT_USERNAME
"""

import os
import re
import subprocess
import logging
from datetime import datetime

# Get logger
logger = logging.getLogger(__name__)


def is_github_actions():
    """Check if running in GitHub Actions environment"""
    return os.environ.get('GITHUB_ACTIONS') == 'true'


def mask_sensitive_info(text):
    """Mask sensitive information in text to prevent exposure in logs"""
    if not text:
        return text
    
    # Mask GitHub personal access tokens (ghp_xxxxxxxxxx)
    text = re.sub(r'ghp_[a-zA-Z0-9]{35,}', 'ghp_***MASKED***', text)
    
    # Mask other potential GitHub tokens (gho_, ghr_, ghs_)
    text = re.sub(r'gh[o-r-s]_[a-zA-Z0-9]{35,}', 'gh*_***MASKED***', text)
    
    # Mask email passwords in SMTP URLs
    def mask_email_password(match):
        username, password, domain = match.groups()
        if 'github.com' in domain:
            return match.group(0)
        return f"{username}:***MASKED***@{domain}"
    
    text = re.sub(r'([a-zA-Z0-9._%+-]+):([^@]+)@([^/\s]+)', mask_email_password, text)
    
    # Mask qBittorrent passwords
    text = re.sub(r'password["\']?\s*[:=]\s*["\']?([^"\s]+)["\']?', r'password:***MASKED***', text)
    
    # Mask SMTP passwords
    text = re.sub(r'SMTP_PASSWORD["\']?\s*[:=]\s*["\']?([^"\s]+)["\']?', r'SMTP_PASSWORD:***MASKED***', text)
    
    return text


def safe_log_info(message):
    """Log message with sensitive information masked"""
    masked_message = mask_sensitive_info(message)
    logger.info(masked_message)


def safe_log_warning(message):
    """Log warning message with sensitive information masked"""
    masked_message = mask_sensitive_info(message)
    logger.warning(masked_message)


def safe_log_error(message):
    """Log error message with sensitive information masked"""
    masked_message = mask_sensitive_info(message)
    logger.error(masked_message)


def get_current_branch():
    """Get the current git branch name"""
    try:
        result = subprocess.run(['git', 'branch', '--show-current'], 
                                capture_output=True, text=True, check=True)
        branch = result.stdout.strip()
        if branch:
            return branch
    except subprocess.CalledProcessError:
        pass
    return 'main'  # Default fallback


def has_git_credentials(git_username, git_password):
    """
    Check if git credentials are available for commit/push.
    
    Note: This function only checks if actual credentials are provided.
    In GitHub Actions, if GIT_PASSWORD is empty (as set by workflow),
    scripts should NOT attempt to commit - the workflow handles commits.
    
    Args:
        git_username: Git username
        git_password: Git password/token
    
    Returns:
        bool: True if both username and password are non-empty, False otherwise
    """
    # Only return True if both username and password are actually provided
    # This ensures scripts don't attempt commits when run from GitHub Actions
    # workflow without credentials (workflow handles commits itself)
    return bool(git_username and git_password)


def git_commit_and_push(files_to_add, commit_message, from_pipeline=False, 
                        git_username=None, git_password=None, git_repo_url=None, git_branch=None,
                        skip_push=None):
    """
    Commit and push files to git repository.
    
    Args:
        files_to_add: List of file paths or patterns to add
        commit_message: Commit message
        from_pipeline: If True, use git_username/git_password; if False, try github-actions[bot] first
        git_username: Git username (required if from_pipeline=True)
        git_password: Git password/token (required if from_pipeline=True)
        git_repo_url: Git repository URL (required if from_pipeline=True)
        git_branch: Git branch name (optional, uses current branch if not specified)
        skip_push: If True, skip the push step. If None (default), skip push in GitHub Actions 
                   when not from_pipeline (let workflow handle push)
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Determine the branch to use
        current_branch = git_branch or get_current_branch()
        safe_log_info(f"Git commit/push: branch={current_branch}, from_pipeline={from_pipeline}")
        
        # Configure git user based on mode
        if from_pipeline:
            # Pipeline mode: use provided credentials
            if not git_username or not git_password or not git_repo_url:
                safe_log_error("Pipeline mode requires git_username, git_password, and git_repo_url")
                return False
            
            subprocess.run(['git', 'config', 'user.name', git_username], check=True)
            subprocess.run(['git', 'config', 'user.email', f'{git_username}@users.noreply.github.com'], check=True)
            safe_log_info(f"Configured git user: {git_username}")
            
        elif is_github_actions():
            # Standalone in GitHub Actions: try github-actions[bot]
            try:
                subprocess.run(['git', 'config', 'user.name', 'github-actions[bot]'], check=True)
                subprocess.run(['git', 'config', 'user.email', 'github-actions[bot]@users.noreply.github.com'], check=True)
                safe_log_info("Configured git user: github-actions[bot]")
            except subprocess.CalledProcessError:
                # Fallback to GIT_USERNAME if github-actions[bot] fails
                if git_username:
                    subprocess.run(['git', 'config', 'user.name', git_username], check=True)
                    subprocess.run(['git', 'config', 'user.email', f'{git_username}@users.noreply.github.com'], check=True)
                    safe_log_info(f"Fallback to git user: {git_username}")
                else:
                    safe_log_error("Failed to configure git user and no fallback username provided")
                    return False
        else:
            # Local standalone: use GIT_USERNAME from config
            if git_username:
                subprocess.run(['git', 'config', 'user.name', git_username], check=True)
                subprocess.run(['git', 'config', 'user.email', f'{git_username}@users.noreply.github.com'], check=True)
                safe_log_info(f"Configured git user: {git_username}")
            else:
                safe_log_warning("No git username provided for local commit")
                return False
        
        # Add files
        for file_pattern in files_to_add:
            try:
                subprocess.run(['git', 'add', file_pattern], check=True)
                safe_log_info(f"Added to git: {file_pattern}")
            except subprocess.CalledProcessError as e:
                safe_log_warning(f"Failed to add {file_pattern}: {e}")
        
        # Check if there are any changes to commit
        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            safe_log_info("No changes to commit - files are already up to date")
            return True
        
        # Commit
        subprocess.run(['git', 'commit', '-m', commit_message], check=True)
        safe_log_info(f"Committed with message: {commit_message}")
        
        # Determine whether to skip push
        # Default: skip push in GitHub Actions when not from_pipeline (let workflow handle push)
        should_skip_push = skip_push
        if should_skip_push is None:
            should_skip_push = is_github_actions() and not from_pipeline
        
        if should_skip_push:
            safe_log_info("✓ Successfully committed changes (push skipped, will be handled by workflow)")
            return True
        
        # Push
        if from_pipeline and git_username and git_password and git_repo_url:
            # Use authenticated URL for pipeline mode
            remote_url_with_auth = git_repo_url.replace('https://', f'https://{git_username}:{git_password}@')
            subprocess.run(['git', 'push', remote_url_with_auth, current_branch], check=True)
        elif is_github_actions():
            # In GitHub Actions, push normally (GITHUB_TOKEN should be configured)
            subprocess.run(['git', 'push'], check=True)
        else:
            # Local: try with credentials if available
            if git_username and git_password and git_repo_url:
                remote_url_with_auth = git_repo_url.replace('https://', f'https://{git_username}:{git_password}@')
                subprocess.run(['git', 'push', remote_url_with_auth, current_branch], check=True)
            else:
                # Try normal push (might work if SSH keys are configured)
                subprocess.run(['git', 'push'], check=True)
        
        safe_log_info("✓ Successfully committed and pushed changes")
        return True
        
    except subprocess.CalledProcessError as e:
        masked_cmd = mask_sensitive_info(str(e.cmd)) if hasattr(e, 'cmd') else str(e)
        safe_log_error(f"Git operation failed: Command {masked_cmd} returned non-zero exit status {e.returncode}")
        if hasattr(e, 'output') and e.output:
            masked_output = mask_sensitive_info(e.output)
            safe_log_error(f"Command output: {masked_output}")
        return False
    except Exception as e:
        safe_log_error(f"Unexpected error during git operations: {e}")
        return False


def flush_log_handlers():
    """Flush all log handlers to ensure logs are written before commit"""
    # Get root logger and all its handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.flush()
    
    # Also flush handlers for common module loggers
    for name in logging.Logger.manager.loggerDict:
        log = logging.getLogger(name)
        for handler in log.handlers:
            handler.flush()

