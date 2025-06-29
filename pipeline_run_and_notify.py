import subprocess
import smtplib
import logging
import os
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid
from email.mime.base import MIMEBase
from email import encoders

# Import unified configuration
try:
    from config import (
        GIT_USERNAME, GIT_PASSWORD, GIT_REPO_URL, GIT_BRANCH,
        SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO,
        PIPELINE_LOG_FILE, SPIDER_LOG_FILE, UPLOADER_LOG_FILE,
        DAILY_REPORT_DIR
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


def git_add_commit(step):
    """Commit and push Daily Report and logs files to GitHub"""
    try:
        logger.info(f"Step {step}: Committing and pushing files to GitHub...")

        # Configure git with credentials
        subprocess.run(['git', 'config', 'user.name', GIT_USERNAME], check=True)
        subprocess.run(['git', 'config', 'user.email', f'{GIT_USERNAME}@users.noreply.github.com'], check=True)

        # Pull latest changes from remote to avoid push conflicts
        logger.info("Pulling latest changes from remote repository...")
        try:
            # Use git pull with credentials in URL to avoid authentication issues
            remote_url_with_auth = GIT_REPO_URL.replace('https://', f'https://{GIT_USERNAME}:{GIT_PASSWORD}@')
            subprocess.run(['git', 'pull', remote_url_with_auth, GIT_BRANCH], check=True)
            logger.info("✓ Successfully pulled latest changes from remote")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Pull failed (this might be normal for new repos): {e}")
            # Continue with commit/push even if pull fails (e.g., new repository)

        # Add all files in Daily Report and logs folders
        logger.info("Adding files to git...")
        subprocess.run(['git', 'add', DAILY_REPORT_DIR], check=True)
        subprocess.run(['git', 'add', 'logs/'], check=True)

        # Check if there are any changes to commit
        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            logger.info(f"No changes to commit - files are already up to date")
            return True

        # Commit with timestamp
        commit_message = f"Auto-commit: JavDB pipeline {step} results {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(['git', 'commit', '-m', commit_message], check=True)
        logger.info(f"✓ Committed changes for {step}")

        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e}")
        logger.error(f"Command output: {e.output if hasattr(e, 'output') else 'No output available'}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during git operations: {e}")
        return False


def main():
    pipeline_success = False
    git_success = False
    try:
        logger.info("=" * 60)
        logger.info("STARTING JAVDB PIPELINE")
        logger.info("=" * 60)

        # 1. Run Javdb_Spider
        logger.info("Step 1: Running JavDB Spider...")
        run_script('Javdb_Spider.py')
        logger.info("✓ JavDB Spider completed successfully")

        # Commit spider results immediately
        logger.info("Step 1.5: Committing spider results to GitHub...")
        spider_git_success = git_add_commit("spider")
        if spider_git_success:
            logger.info("✓ Spider results committed successfully")
        else:
            logger.warning("⚠ Spider commit failed, but pipeline continues")

        # 2. Run qbtorrent_uploader
        logger.info("Step 2: Running qBittorrent Uploader...")
        run_script('qbtorrent_uploader.py')
        logger.info("✓ qBittorrent Uploader completed successfully")

        # Commit uploader results immediately
        logger.info("Step 2.5: Committing uploader results to GitHub...")
        uploader_git_success = git_add_commit("uploader")
        if uploader_git_success:
            logger.info("✓ Uploader results committed successfully")
        else:
            logger.warning("⚠ Uploader commit failed, but pipeline continues")

        # 3. Final git commit and push (in case there are any remaining changes)
        logger.info("Step 3: Final commit and push to GitHub...")
        git_success = git_add_commit("final")
        if git_success:
            logger.info("✓ Final git operations completed successfully")
        else:
            logger.warning("⚠ Final git operations failed, but pipeline continues")

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
    if pipeline_success:
        # Pipeline succeeded - send detailed report with attachments
        spider_summary = get_log_summary(spider_log_path, lines=35)
        uploader_summary = get_log_summary(uploader_log_path, lines=13)

        git_status = "✓ SUCCESS" if git_success else "⚠ FAILED"

        body = f"""
JavDB Spider and qBittorrent Uploader Pipeline Completed Successfully.

Git Operations: {git_status}

--- JavDB Spider Summary ---
{spider_summary}

--- qBittorrent Uploader Summary ---
{uploader_summary}
"""
        attachments = [csv_path, spider_log_path, uploader_log_path]
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
                attachments=None  # No attachments for failure
            )
        except Exception as e:
            logger.error(f'Failed to send failure email: {e}')

    # Final commit for pipeline log
    logger.info("Final commit for pipeline log...")
    git_add_commit("pipeline_log")
    # Push to remote repository
    remote_url_with_auth = GIT_REPO_URL.replace('https://', f'https://{GIT_USERNAME}:{GIT_PASSWORD}@')
    subprocess.run(['git', 'push', remote_url_with_auth, GIT_BRANCH], check=True)


if __name__ == '__main__':
    main()
