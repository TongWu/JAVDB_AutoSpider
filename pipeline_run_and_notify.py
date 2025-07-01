import subprocess
import smtplib
import logging
import os
import sys
import re
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid
from email.mime.base import MIMEBase
from email import encoders
import argparse

# Import unified configuration
try:
    from config import (
        GIT_USERNAME, GIT_PASSWORD, GIT_REPO_URL, GIT_BRANCH,
        SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO,
        PIPELINE_LOG_FILE, SPIDER_LOG_FILE, UPLOADER_LOG_FILE,
        DAILY_REPORT_DIR, AD_HOC_DIR
    )
except ImportError:
    # Fallback values if config.py doesn't exist
    GIT_USERNAME = 'your_github_username'
    GIT_PASSWORD = 'your_github_password_or_token'
    GIT_REPO_URL = 'https://github.com/your_username/your_repo_name.git'
    GIT_BRANCH = 'main'
    
    SMTP_SERVER = 'smtp.gmail.com'
    SMTP_PORT = 587
    SMTP_USER = 'your_email@gmail.com'
    SMTP_PASSWORD = 'your_email_password'
    EMAIL_FROM = 'your_email@gmail.com'
    EMAIL_TO = 'your_email@gmail.com'
    
    PIPELINE_LOG_FILE = 'logs/pipeline_run_and_notify.log'
    SPIDER_LOG_FILE = 'logs/Javdb_Spider.log'
    UPLOADER_LOG_FILE = 'logs/qbtorrent_uploader.log'
    DAILY_REPORT_DIR = 'Daily Report'
    AD_HOC_DIR = 'Ad Hoc'

os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(PIPELINE_LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- FILE PATHS ---
today_str = datetime.now().strftime('%Y%m%d')
csv_path = os.path.join(DAILY_REPORT_DIR, f'Javdb_TodayTitle_{today_str}.csv')
spider_log_path = SPIDER_LOG_FILE
uploader_log_path = UPLOADER_LOG_FILE


def mask_sensitive_info(text):
    """Mask sensitive information in text to prevent exposure in logs"""
    if not text:
        return text
    
    # Mask GitHub personal access tokens (ghp_xxxxxxxxxx) - do this first
    text = re.sub(r'ghp_[a-zA-Z0-9]{35,}', 'ghp_***MASKED***', text)
    
    # Mask other potential GitHub tokens (gho_, ghr_, ghs_)
    text = re.sub(r'gh[o-r-s]_[a-zA-Z0-9]{35,}', 'gh*_***MASKED***', text)
    
    # Mask email passwords in SMTP URLs (but exclude GitHub URLs)
    # This regex matches username:password@ but only if it's not a GitHub URL
    def mask_email_password(match):
        username, password, domain = match.groups()
        if 'github.com' in domain:
            # Don't mask GitHub URLs as they're handled above
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


# --- PIPELINE EXECUTION ---
def run_script(script_path, args=None):
    cmd = ['python3', script_path]
    if args:
        cmd += args
    logger.info(f'Running: {" ".join(cmd)}')

    # Run with real-time output
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    # Print output in real-time (but don't log to pipeline log since sub-scripts have their own logging)
    output_lines = []
    if process.stdout:
        for line in iter(process.stdout.readline, ''):
            if line:
                print(line.rstrip())  # Print to console only
                output_lines.append(line)
                # Don't log to pipeline log - sub-scripts have their own logging

        process.stdout.close()

    return_code = process.wait()

    if return_code != 0:
        logger.error(f'Script {script_path} failed with return code {return_code}')
        raise RuntimeError(f'Script {script_path} failed with return code {return_code}')

    return ''.join(output_lines)


def get_log_summary(log_path, lines=200):
    if not os.path.exists(log_path):
        return f'Log file not found: {log_path}'
    with open(log_path, 'r', encoding='utf-8') as f:
        log_lines = f.readlines()
    return ''.join(log_lines[-lines:])


def send_email(subject, body, attachments=None):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    msg.set_content(body)

    if attachments:
        for file_path in attachments:
            if not os.path.exists(file_path):
                logger.warning(f'Attachment not found: {file_path}')
                continue
            with open(file_path, 'rb') as f:
                file_data = f.read()
                file_name = os.path.basename(file_path)
                maintype = 'application'
                subtype = 'octet-stream'
                msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=file_name)

    logger.info('Connecting to SMTP server...')
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    logger.info('Email sent successfully.')


def git_add_commit_push(step):
    """Commit and push Daily Report and logs files to GitHub"""
    try:
        safe_log_info(f"Step {step}: Committing and pushing files to GitHub...")

        # Configure git with credentials
        subprocess.run(['git', 'config', 'user.name', GIT_USERNAME], check=True)
        subprocess.run(['git', 'config', 'user.email', f'{GIT_USERNAME}@users.noreply.github.com'], check=True)

        # Pull latest changes from remote to avoid push conflicts
        safe_log_info("Pulling latest changes from remote repository...")
        try:
            # Use git pull with credentials in URL to avoid authentication issues
            remote_url_with_auth = GIT_REPO_URL.replace('https://', f'https://{GIT_USERNAME}:{GIT_PASSWORD}@')
            subprocess.run(['git', 'pull', remote_url_with_auth, GIT_BRANCH], check=True)
            safe_log_info("✓ Successfully pulled latest changes from remote")
        except subprocess.CalledProcessError as e:
            # Mask the command that contains sensitive information
            masked_cmd = mask_sensitive_info(str(e.cmd)) if hasattr(e, 'cmd') else str(e)
            safe_log_warning(f"Pull failed (this might be normal for new repos): Command {masked_cmd} returned non-zero exit status {e.returncode}")
            # Continue with commit/push even if pull fails (e.g., new repository)

        # Add all files in Daily Report and logs folders
        safe_log_info("Adding files to git...")
        subprocess.run(['git', 'add', DAILY_REPORT_DIR], check=True)
        subprocess.run(['git', 'add', AD_HOC_DIR], check=True)
        subprocess.run(['git', 'add', 'logs/'], check=True)

        # Check if there are any changes to commit
        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            safe_log_info(f"No changes to commit - files are already up to date")
            return True

        # Commit with timestamp
        commit_message = f"Auto-commit: JavDB pipeline {step} results {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        safe_log_info(f"Commit changes for {step}")
        subprocess.run(['git', 'add', 'logs/'], check=True)
        subprocess.run(['git', 'commit', '-m', commit_message], check=True)

        # Push to remote repository
        remote_url_with_auth = GIT_REPO_URL.replace('https://', f'https://{GIT_USERNAME}:{GIT_PASSWORD}@')
        subprocess.run(['git', 'push', remote_url_with_auth, GIT_BRANCH], check=True)

        return True

    except subprocess.CalledProcessError as e:
        # Mask the command that contains sensitive information
        masked_cmd = mask_sensitive_info(str(e.cmd)) if hasattr(e, 'cmd') else str(e)
        safe_log_error(f"Git operation failed: Command {masked_cmd} returned non-zero exit status {e.returncode}")
        
        # Mask output if it contains sensitive information
        if hasattr(e, 'output') and e.output:
            masked_output = mask_sensitive_info(e.output)
            safe_log_error(f"Command output: {masked_output}")
        else:
            safe_log_error("Command output: No output available")
        return False
    except Exception as e:
        safe_log_error(f"Unexpected error during git operations: {e}")
        return False


def parse_arguments():
    """Parse command line arguments for the pipeline"""
    parser = argparse.ArgumentParser(description='JavDB Pipeline - Run spider and uploader with optional arguments')
    # Javdb_Spider arguments
    parser.add_argument('--url', type=str, help='Custom URL to scrape (add ?page=x for pages)')
    parser.add_argument('--start-page', type=int, help='Starting page number')
    parser.add_argument('--end-page', type=int, help='Ending page number')
    parser.add_argument('--all', action='store_true', help='Parse all pages until an empty page is found')
    parser.add_argument('--ignore-history', action='store_true', help='Ignore history file and scrape all pages')
    parser.add_argument('--phase', choices=['1', '2', 'all'], help='Which phase to run: 1 (subtitle+today), 2 (today only), all (default)')
    parser.add_argument('--output-file', type=str, help='Specify output CSV file name')
    parser.add_argument('--dry-run', action='store_true', help='Print items that would be written without changing CSV file')
    return parser.parse_args()


def main():
    args = parse_arguments()
    is_adhoc_mode = args.url is not None
    # Determine CSV path based on mode
    if is_adhoc_mode:
        import Javdb_Spider
        csv_filename = Javdb_Spider.generate_output_csv_name(args.url)
        csv_path = os.path.join(AD_HOC_DIR, csv_filename)
        logger.info(f"Ad hoc mode detected. Expected CSV: {csv_path}")
    else:
        today_str = datetime.now().strftime('%Y%m%d')
        csv_path = os.path.join('Daily Report', f'Javdb_TodayTitle_{today_str}.csv')
        logger.info(f"Daily mode. Expected CSV: {csv_path}")

    # Build arguments for Javdb_Spider
    spider_args = []
    if args.url:
        spider_args.extend(['--url', args.url])
    if args.start_page is not None:
        spider_args.extend(['--start-page', str(args.start_page)])
    if args.end_page is not None:
        spider_args.extend(['--end-page', str(args.end_page)])
    if args.all:
        spider_args.append('--all')
    if args.ignore_history:
        spider_args.append('--ignore-history')
    if args.phase:
        spider_args.extend(['--phase', args.phase])
    if args.output_file:
        spider_args.extend(['--output-file', args.output_file])
    if args.dry_run:
        spider_args.append('--dry-run')

    # Build arguments for qbtorrent_uploader
    uploader_args = []
    if is_adhoc_mode:
        uploader_args.extend(['--mode', 'adhoc'])
    else:
        uploader_args.extend(['--mode', 'daily'])

    pipeline_success = False
    try:
        logger.info("=" * 60)
        logger.info("STARTING JAVDB PIPELINE")
        if is_adhoc_mode:
            logger.info("MODE: Ad Hoc")
            logger.info(f"Custom URL: {args.url}")
        else:
            logger.info("MODE: Daily")
        logger.info("=" * 60)

        # 1. Run Javdb_Spider
        logger.info("Step 1: Running JavDB Spider...")
        run_script('Javdb_Spider.py', spider_args)
        logger.info("✓ JavDB Spider completed successfully")

        # 2. Run qbtorrent_uploader
        logger.info("Step 2: Running qBittorrent Uploader...")
        run_script('qbtorrent_uploader.py', uploader_args)
        logger.info("✓ qBittorrent Uploader completed successfully")

        pipeline_success = True
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)

    except Exception as e:
        logger.error("=" * 60)
        logger.error("PIPELINE FAILED")
        logger.error("=" * 60)
        logger.error(f'Error: {e}')
        pipeline_success = False

    # Send email based on pipeline result
    today_str = datetime.now().strftime('%Y%m%d')
    if pipeline_success:
        # Pipeline succeeded - send detailed report with attachments
        spider_summary = get_log_summary(SPIDER_LOG_FILE, lines=35)
        uploader_summary = get_log_summary(UPLOADER_LOG_FILE, lines=13)
        body = f"""
JavDB Spider and qBittorrent Uploader Pipeline Completed Successfully.
--- JavDB Spider Summary ---
{spider_summary}
--- qBittorrent Uploader Summary ---
{uploader_summary}
"""
        attachments = [csv_path, SPIDER_LOG_FILE, UPLOADER_LOG_FILE, PIPELINE_LOG_FILE]
        try:
            send_email(
                subject=f'JavDB Pipeline Report {today_str} - SUCCESS',
                body=body,
                attachments=attachments
            )
        except Exception as e:
            logger.error(f'Failed to send success email: {e}')
    else:
        # Pipeline failed - send simple failure notification
        body = f"""
JavDB Pipeline Failed
The pipeline encountered an error and could not complete successfully.
Please check the logs for more details.
Error occurred at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        try:
            send_email(
                subject=f'JavDB Pipeline Report {today_str} - FAILED',
                body=body,
                attachments=[PIPELINE_LOG_FILE]  # Include pipeline log for debugging
            )
        except Exception as e:
            logger.error(f'Failed to send failure email: {e}')

    # Final commit for pipeline log
    logger.info("Final commit for pipeline log...")
    git_add_commit_push("pipeline_log")


if __name__ == '__main__':
    main()
