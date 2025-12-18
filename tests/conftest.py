"""
Pytest configuration and shared fixtures

This file contains pytest configuration and fixtures that are shared across all tests.
"""
import pytest
import os
import sys
import tempfile
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(scope="session")
def temp_dir():
    """Create a temporary directory for the test session"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def temp_logs_dir(temp_dir):
    """Create temporary logs directory"""
    logs_dir = os.path.join(temp_dir, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


@pytest.fixture
def temp_daily_report_dir(temp_dir):
    """Create temporary Daily Report directory"""
    daily_dir = os.path.join(temp_dir, 'Daily Report')
    os.makedirs(daily_dir, exist_ok=True)
    return daily_dir


@pytest.fixture
def temp_adhoc_dir(temp_dir):
    """Create temporary Ad Hoc directory"""
    adhoc_dir = os.path.join(temp_dir, 'Ad Hoc')
    os.makedirs(adhoc_dir, exist_ok=True)
    return adhoc_dir


@pytest.fixture
def mock_config(monkeypatch):
    """Mock configuration values for testing"""
    config_values = {
        'LOG_LEVEL': 'INFO',
        'DETAIL_PAGE_SLEEP': 0,  # No sleep in tests
        'PHASE2_MIN_RATE': 4.0,
        'PHASE2_MIN_COMMENTS': 85,
        'IGNORE_RELEASE_DATE_FILTER': False,
    }
    
    # Mock config imports
    for key, value in config_values.items():
        monkeypatch.setattr(f'config.{key}', value, raising=False)
    
    return config_values


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging configuration between tests"""
    import logging
    # Clear all handlers
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    yield
    # Clear again after test
    for handler in root.handlers[:]:
        root.removeHandler(handler)


@pytest.fixture
def sample_magnet_link():
    """Provide a sample magnet link for testing"""
    return "magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12"


@pytest.fixture
def sample_video_codes():
    """Provide sample video codes for testing"""
    return [
        'TEST-001',
        'TEST-002',
        'TEST-003',
        'SAMPLE-100',
        'DEMO-999'
    ]


def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "network: mark test as requiring network access"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add default markers"""
    for item in items:
        # Add 'unit' marker to all tests by default if no other marker is present
        if not any(marker.name in ['integration', 'slow', 'network'] 
                  for marker in item.iter_markers()):
            item.add_marker(pytest.mark.unit)
