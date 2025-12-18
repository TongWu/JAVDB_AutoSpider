#!/bin/bash
# Script to run tests for JavDB Pipeline
# Usage: ./run_tests.sh [options]

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default options
RUN_ALL=true
RUN_COVERAGE=false
RUN_HTML_REPORT=false
RUN_LINT=false
RUN_SECURITY=false
VERBOSE=false
FAST=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -c|--coverage)
            RUN_COVERAGE=true
            shift
            ;;
        -h|--html)
            RUN_HTML_REPORT=true
            RUN_COVERAGE=true
            shift
            ;;
        -l|--lint)
            RUN_LINT=true
            RUN_ALL=false
            shift
            ;;
        -s|--security)
            RUN_SECURITY=true
            RUN_ALL=false
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -f|--fast)
            FAST=true
            shift
            ;;
        -a|--all)
            RUN_ALL=true
            RUN_COVERAGE=true
            RUN_HTML_REPORT=true
            RUN_LINT=true
            RUN_SECURITY=true
            shift
            ;;
        --help)
            echo "Usage: ./run_tests.sh [options]"
            echo ""
            echo "Options:"
            echo "  -c, --coverage      Run tests with coverage report"
            echo "  -h, --html          Generate HTML coverage report"
            echo "  -l, --lint          Run code quality checks"
            echo "  -s, --security      Run security scan"
            echo "  -v, --verbose       Verbose output"
            echo "  -f, --fast          Fast mode (skip slow tests)"
            echo "  -a, --all           Run all checks (tests, lint, security)"
            echo "  --help              Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  JavDB Pipeline Test Runner${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo -e "${RED}Error: pytest is not installed${NC}"
    echo "Please install test dependencies:"
    echo "  pip install -r requirements-test.txt"
    exit 1
fi

# Create necessary directories
mkdir -p logs
mkdir -p "Daily Report"
mkdir -p "Ad Hoc"

# Run unit tests
if [ "$RUN_ALL" = true ]; then
    echo -e "${GREEN}Running unit tests...${NC}"
    
    PYTEST_ARGS="-v"
    
    if [ "$VERBOSE" = true ]; then
        PYTEST_ARGS="$PYTEST_ARGS -vv -s"
    fi
    
    if [ "$FAST" = true ]; then
        PYTEST_ARGS="$PYTEST_ARGS -m 'not slow'"
        echo -e "${YELLOW}(Skipping slow tests)${NC}"
    fi
    
    if [ "$RUN_COVERAGE" = true ]; then
        PYTEST_ARGS="$PYTEST_ARGS --cov=utils --cov=pipeline"
        PYTEST_ARGS="$PYTEST_ARGS --cov-report=term-missing"
        
        if [ "$RUN_HTML_REPORT" = true ]; then
            PYTEST_ARGS="$PYTEST_ARGS --cov-report=html"
        fi
    fi
    
    if pytest $PYTEST_ARGS; then
        echo -e "${GREEN}✓ Unit tests passed${NC}"
        
        if [ "$RUN_HTML_REPORT" = true ]; then
            echo -e "${BLUE}HTML coverage report generated: htmlcov/index.html${NC}"
        fi
    else
        echo -e "${RED}✗ Unit tests failed${NC}"
        exit 1
    fi
    echo ""
fi

# Run lint checks
if [ "$RUN_LINT" = true ]; then
    echo -e "${GREEN}Running code quality checks...${NC}"
    
    # Check if linting tools are installed
    LINT_TOOLS_MISSING=false
    
    if ! command -v black &> /dev/null; then
        echo -e "${YELLOW}Warning: black is not installed${NC}"
        LINT_TOOLS_MISSING=true
    fi
    
    if ! command -v isort &> /dev/null; then
        echo -e "${YELLOW}Warning: isort is not installed${NC}"
        LINT_TOOLS_MISSING=true
    fi
    
    if ! command -v flake8 &> /dev/null; then
        echo -e "${YELLOW}Warning: flake8 is not installed${NC}"
        LINT_TOOLS_MISSING=true
    fi
    
    if [ "$LINT_TOOLS_MISSING" = true ]; then
        echo "Install linting tools with:"
        echo "  pip install black isort flake8"
        echo ""
    fi
    
    # Run black
    if command -v black &> /dev/null; then
        echo -n "  • Checking code formatting (black)... "
        if black --check --quiet utils/ tests/ 2>/dev/null; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${YELLOW}⚠ Formatting issues found${NC}"
            echo "    Run 'black utils/ tests/' to fix"
        fi
    fi
    
    # Run isort
    if command -v isort &> /dev/null; then
        echo -n "  • Checking import sorting (isort)... "
        if isort --check-only --quiet utils/ tests/ 2>/dev/null; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${YELLOW}⚠ Import sorting issues found${NC}"
            echo "    Run 'isort utils/ tests/' to fix"
        fi
    fi
    
    # Run flake8
    if command -v flake8 &> /dev/null; then
        echo -n "  • Checking code style (flake8)... "
        if flake8 utils/ tests/ --count --select=E9,F63,F7,F82 --show-source --statistics >/dev/null 2>&1; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${RED}✗ Critical issues found${NC}"
            flake8 utils/ tests/ --count --select=E9,F63,F7,F82 --show-source --statistics
        fi
    fi
    echo ""
fi

# Run security scan
if [ "$RUN_SECURITY" = true ]; then
    echo -e "${GREEN}Running security scan...${NC}"
    
    if ! command -v bandit &> /dev/null; then
        echo -e "${YELLOW}Warning: bandit is not installed${NC}"
        echo "Install with: pip install bandit"
    else
        echo -n "  • Scanning for security issues... "
        if bandit -r utils/ -q 2>/dev/null; then
            echo -e "${GREEN}✓ No issues found${NC}"
        else
            echo -e "${YELLOW}⚠ Potential issues found${NC}"
            bandit -r utils/
        fi
    fi
    echo ""
fi

echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Test run completed!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Summary
if [ "$RUN_HTML_REPORT" = true ]; then
    echo -e "📊 View coverage report: ${BLUE}htmlcov/index.html${NC}"
fi

echo -e "📖 For more testing options, see: ${BLUE}TESTING.md${NC}"
echo ""
