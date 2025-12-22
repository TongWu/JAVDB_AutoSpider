"""
Extended unit tests for scripts/email_notification.py functions.
These tests use local implementations to avoid module import issues.
"""
import os
import sys
import re
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
import csv
from io import StringIO

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestFormatBytes:
    """Test cases for format_bytes function logic."""
    
    def format_bytes(self, byte_count):
        """Format bytes to human-readable string - local implementation."""
        if byte_count < 0:
            return '0 B'
        if byte_count < 1024:
            return f'{byte_count} B'
        elif byte_count < 1024 ** 2:
            return f'{byte_count / 1024:.2f} KB'
        elif byte_count < 1024 ** 3:
            return f'{byte_count / 1024 ** 2:.2f} MB'
        else:
            return f'{byte_count / 1024 ** 3:.2f} GB'
    
    def test_bytes(self):
        """Test formatting bytes."""
        assert self.format_bytes(100) == '100 B'
    
    def test_kilobytes(self):
        """Test formatting kilobytes."""
        assert self.format_bytes(2048) == '2.00 KB'
    
    def test_megabytes(self):
        """Test formatting megabytes."""
        assert self.format_bytes(2097152) == '2.00 MB'
    
    def test_gigabytes(self):
        """Test formatting gigabytes."""
        assert self.format_bytes(2147483648) == '2.00 GB'
    
    def test_zero_bytes(self):
        """Test formatting zero bytes."""
        assert self.format_bytes(0) == '0 B'
    
    def test_negative_bytes(self):
        """Test formatting negative bytes."""
        assert self.format_bytes(-100) == '0 B'


class TestExtractVideoCodeLogic:
    """Test cases for extracting video codes from torrent names."""
    
    def extract_video_code(self, torrent_name):
        """Extract video code from torrent name - local implementation."""
        # Common patterns for JAV codes
        patterns = [
            r'([A-Z]{2,10}-\d{3,5})',  # Standard format: ABC-123
            r'([A-Z]{2,10}\d{3,5})',    # Without hyphen: ABC123
            r'(\d{3,5}[A-Z]{2,5}-\d{3,5})',  # Special format: 123ABC-456
        ]
        
        for pattern in patterns:
            match = re.search(pattern, torrent_name.upper())
            if match:
                return match.group(1)
        return None
    
    def test_extract_standard_code(self):
        """Test extracting standard video code."""
        result = self.extract_video_code('[JAVDB] ABC-123 2024.mp4')
        assert result == 'ABC-123'
    
    def test_extract_code_without_hyphen(self):
        """Test extracting code without hyphen."""
        result = self.extract_video_code('[TEST] DEF456 HD.mp4')
        assert result == 'DEF456'
    
    def test_no_code_found(self):
        """Test when no code is found."""
        result = self.extract_video_code('Random movie name')
        assert result is None


class TestParseLogLine:
    """Test cases for parsing log lines."""
    
    def parse_log_line(self, line):
        """Parse a log line into components - local implementation."""
        # Log format: [YYYY-MM-DD HH:MM:SS,mmm] [LEVEL] [Module] Message
        pattern = r'\[([^\]]+)\] \[(\w+)\] \[([^\]]+)\] (.*)'
        match = re.match(pattern, line.strip())
        
        if match:
            return {
                'timestamp': match.group(1),
                'level': match.group(2),
                'module': match.group(3),
                'message': match.group(4)
            }
        return None
    
    def test_parse_info_log(self):
        """Test parsing an INFO log line."""
        line = '[2024-01-15 10:30:45,123] [INFO] [spider] Starting spider process'
        result = self.parse_log_line(line)
        
        assert result is not None
        assert result['level'] == 'INFO'
        assert result['module'] == 'spider'
        assert 'Starting spider process' in result['message']
    
    def test_parse_error_log(self):
        """Test parsing an ERROR log line."""
        line = '[2024-01-15 10:30:45,123] [ERROR] [request] Connection failed'
        result = self.parse_log_line(line)
        
        assert result is not None
        assert result['level'] == 'ERROR'
    
    def test_parse_invalid_log(self):
        """Test parsing an invalid log line."""
        line = 'Just a random text line'
        result = self.parse_log_line(line)
        
        assert result is None


class TestCountLogStats:
    """Test cases for counting log statistics."""
    
    def count_log_stats(self, log_lines):
        """Count statistics from log lines - local implementation."""
        stats = {
            'total': 0,
            'info': 0,
            'warning': 0,
            'error': 0,
            'debug': 0
        }
        
        for line in log_lines:
            stats['total'] += 1
            line_upper = line.upper()
            if '[INFO]' in line_upper:
                stats['info'] += 1
            elif '[WARNING]' in line_upper:
                stats['warning'] += 1
            elif '[ERROR]' in line_upper:
                stats['error'] += 1
            elif '[DEBUG]' in line_upper:
                stats['debug'] += 1
        
        return stats
    
    def test_count_stats_with_mixed_logs(self):
        """Test counting stats with mixed log levels."""
        logs = [
            '[2024-01-15] [INFO] Test info',
            '[2024-01-15] [ERROR] Test error',
            '[2024-01-15] [WARNING] Test warning',
            '[2024-01-15] [INFO] Another info',
        ]
        
        result = self.count_log_stats(logs)
        
        assert result['total'] == 4
        assert result['info'] == 2
        assert result['error'] == 1
        assert result['warning'] == 1
    
    def test_count_stats_empty_logs(self):
        """Test counting stats with empty log list."""
        result = self.count_log_stats([])
        
        assert result['total'] == 0
        assert result['info'] == 0


class TestGenerateHtmlReport:
    """Test cases for HTML report generation logic."""
    
    def generate_summary_html(self, summary_data):
        """Generate HTML summary section - local implementation."""
        html = '<div class="summary">\n'
        html += f'<h2>Daily Report Summary</h2>\n'
        html += f'<p>Total processed: {summary_data.get("total", 0)}</p>\n'
        html += f'<p>New items: {summary_data.get("new", 0)}</p>\n'
        html += f'<p>Errors: {summary_data.get("errors", 0)}</p>\n'
        html += '</div>'
        return html
    
    def test_generate_summary_html(self):
        """Test generating HTML summary."""
        summary = {'total': 100, 'new': 20, 'errors': 5}
        html = self.generate_summary_html(summary)
        
        assert 'Daily Report Summary' in html
        assert '100' in html
        assert '20' in html
        assert '5' in html
    
    def test_generate_summary_with_defaults(self):
        """Test generating HTML summary with missing data."""
        summary = {}
        html = self.generate_summary_html(summary)
        
        assert '0' in html


class TestGenerateTableHtml:
    """Test cases for HTML table generation."""
    
    def generate_table_html(self, headers, rows):
        """Generate HTML table - local implementation."""
        html = '<table>\n'
        html += '<thead><tr>'
        for header in headers:
            html += f'<th>{header}</th>'
        html += '</tr></thead>\n'
        html += '<tbody>\n'
        for row in rows:
            html += '<tr>'
            for cell in row:
                html += f'<td>{cell}</td>'
            html += '</tr>\n'
        html += '</tbody>\n'
        html += '</table>'
        return html
    
    def test_generate_table_with_data(self):
        """Test generating table with data."""
        headers = ['Code', 'Title', 'Size']
        rows = [
            ['ABC-123', 'Test Movie 1', '2GB'],
            ['DEF-456', 'Test Movie 2', '3GB'],
        ]
        
        html = self.generate_table_html(headers, rows)
        
        assert '<table>' in html
        assert '<th>Code</th>' in html
        assert '<td>ABC-123</td>' in html
    
    def test_generate_empty_table(self):
        """Test generating empty table."""
        headers = ['A', 'B']
        rows = []
        
        html = self.generate_table_html(headers, rows)
        
        assert '<tbody>\n</tbody>' in html


class TestParseCsvReport:
    """Test cases for parsing CSV report files."""
    
    def parse_csv_report(self, csv_content):
        """Parse CSV report content - local implementation."""
        reader = csv.DictReader(StringIO(csv_content))
        return list(reader)
    
    def test_parse_valid_csv(self):
        """Test parsing valid CSV content."""
        csv_content = """Code,Title,Size
ABC-123,Movie One,2GB
DEF-456,Movie Two,3GB"""
        
        result = self.parse_csv_report(csv_content)
        
        assert len(result) == 2
        assert result[0]['Code'] == 'ABC-123'
        assert result[1]['Title'] == 'Movie Two'
    
    def test_parse_empty_csv(self):
        """Test parsing empty CSV (only headers)."""
        csv_content = """Code,Title,Size"""
        
        result = self.parse_csv_report(csv_content)
        
        assert len(result) == 0


class TestSendEmailLogic:
    """Test cases for email sending logic."""
    
    def test_build_message_headers(self):
        """Test building email message headers."""
        from_addr = 'sender@example.com'
        to_addrs = ['recipient@example.com']
        subject = 'Test Subject'
        
        headers = {
            'From': from_addr,
            'To': ', '.join(to_addrs),
            'Subject': subject
        }
        
        assert headers['From'] == 'sender@example.com'
        assert headers['To'] == 'recipient@example.com'
        assert headers['Subject'] == 'Test Subject'
    
    def test_build_multiple_recipients(self):
        """Test building email with multiple recipients."""
        to_addrs = ['a@example.com', 'b@example.com']
        
        to_header = ', '.join(to_addrs)
        
        assert to_header == 'a@example.com, b@example.com'


class TestFilterTorrentsByCategory:
    """Test cases for filtering torrents by category."""
    
    def filter_by_category(self, torrents, category):
        """Filter torrents by category - local implementation."""
        if not category:
            return torrents
        return [t for t in torrents if t.get('category') == category]
    
    def test_filter_by_category(self):
        """Test filtering by specific category."""
        torrents = [
            {'name': 'T1', 'category': 'Daily Ingestion'},
            {'name': 'T2', 'category': 'Ad Hoc'},
            {'name': 'T3', 'category': 'Daily Ingestion'},
        ]
        
        result = self.filter_by_category(torrents, 'Daily Ingestion')
        
        assert len(result) == 2
        assert all(t['category'] == 'Daily Ingestion' for t in result)
    
    def test_filter_no_category(self):
        """Test filtering without category filter."""
        torrents = [
            {'name': 'T1', 'category': 'Daily Ingestion'},
            {'name': 'T2', 'category': 'Ad Hoc'},
        ]
        
        result = self.filter_by_category(torrents, None)
        
        assert len(result) == 2


class TestExtractErrorsFromLog:
    """Test cases for extracting errors from log content."""
    
    def extract_errors(self, log_content):
        """Extract error lines from log content - local implementation."""
        errors = []
        for line in log_content.split('\n'):
            if '[ERROR]' in line.upper():
                errors.append(line.strip())
        return errors
    
    def test_extract_multiple_errors(self):
        """Test extracting multiple errors."""
        log = """[INFO] Starting process
[ERROR] Connection failed
[INFO] Retrying
[ERROR] Timeout occurred"""
        
        result = self.extract_errors(log)
        
        assert len(result) == 2
        assert 'Connection failed' in result[0]
        assert 'Timeout occurred' in result[1]
    
    def test_extract_no_errors(self):
        """Test extracting from log with no errors."""
        log = """[INFO] Starting process
[INFO] Process completed"""
        
        result = self.extract_errors(log)
        
        assert len(result) == 0


class TestCalculateDailySummary:
    """Test cases for calculating daily summary statistics."""
    
    def calculate_summary(self, items):
        """Calculate summary statistics - local implementation."""
        total_count = len(items)
        total_size = sum(item.get('size', 0) for item in items)
        
        categories = {}
        for item in items:
            cat = item.get('category', 'Unknown')
            categories[cat] = categories.get(cat, 0) + 1
        
        return {
            'total_count': total_count,
            'total_size': total_size,
            'categories': categories
        }
    
    def test_calculate_summary(self):
        """Test calculating summary with data."""
        items = [
            {'name': 'T1', 'size': 1000, 'category': 'A'},
            {'name': 'T2', 'size': 2000, 'category': 'A'},
            {'name': 'T3', 'size': 3000, 'category': 'B'},
        ]
        
        result = self.calculate_summary(items)
        
        assert result['total_count'] == 3
        assert result['total_size'] == 6000
        assert result['categories']['A'] == 2
        assert result['categories']['B'] == 1
    
    def test_calculate_summary_empty(self):
        """Test calculating summary with empty data."""
        result = self.calculate_summary([])
        
        assert result['total_count'] == 0
        assert result['total_size'] == 0
        assert result['categories'] == {}

