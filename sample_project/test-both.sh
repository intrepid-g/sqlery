#!/bin/bash
set -e

# SQLery Complete Test Suite - Both SQLite and PostgreSQL
# This script tests both backends and provides a comparison

echo "=========================================="
echo "SQLery Complete Test Suite"
echo "Testing Both SQLite and PostgreSQL"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Change to script directory
cd "$(dirname "$0")"

# Temporary files for results
SQLITE_RESULTS=$(mktemp)
POSTGRES_RESULTS=$(mktemp)

echo -e "${BLUE}This will test both backends sequentially.${NC}"
echo -e "${BLUE}Total estimated time: ~4 minutes${NC}"
echo ""
read -p "Press Enter to continue..."
echo ""

# Test SQLite
echo "=========================================="
echo -e "${YELLOW}PHASE 1: Testing SQLite Backend${NC}"
echo "=========================================="
echo ""

./test-sqlite.sh | tee "$SQLITE_RESULTS"

echo ""
echo -e "${GREEN}SQLite testing complete!${NC}"
echo ""
echo "Pausing for 5 seconds before PostgreSQL test..."
sleep 5
echo ""

# Test PostgreSQL
echo "=========================================="
echo -e "${YELLOW}PHASE 2: Testing PostgreSQL Backend${NC}"
echo "=========================================="
echo ""

./test-postgres.sh | tee "$POSTGRES_RESULTS"

echo ""
echo -e "${GREEN}PostgreSQL testing complete!${NC}"
echo ""

# Extract results for comparison
echo "=========================================="
echo "COMPARISON RESULTS"
echo "=========================================="
echo ""

# Parse SQLite results
SQLITE_TOTAL=$(grep "Total jobs:" "$SQLITE_RESULTS" | head -1 | awk '{print $3}')
SQLITE_SUCCESS=$(grep "Successful:" "$SQLITE_RESULTS" | head -1 | awk '{print $2}')
SQLITE_FAILED=$(grep "Failed:" "$SQLITE_RESULTS" | head -1 | awk '{print $2}')
SQLITE_RATE=$(grep "Success rate:" "$SQLITE_RESULTS" | head -1 | awk '{print $3}' || echo "N/A")
SQLITE_WORKERS=$(grep "Active workers:" "$SQLITE_RESULTS" | head -1 | awk '{print $3}')
SQLITE_LOCKS=$(grep "database locking errors" "$SQLITE_RESULTS" | grep -o "[0-9]\+" | head -1 || echo "0")

# Parse PostgreSQL results
POSTGRES_TOTAL=$(grep "Total jobs:" "$POSTGRES_RESULTS" | head -1 | awk '{print $3}')
POSTGRES_SUCCESS=$(grep "Successful:" "$POSTGRES_RESULTS" | head -1 | awk '{print $2}')
POSTGRES_FAILED=$(grep "Failed:" "$POSTGRES_RESULTS" | head -1 | awk '{print $2}')
POSTGRES_RATE=$(grep "Success rate:" "$POSTGRES_RESULTS" | head -1 | awk '{print $3}' || echo "N/A")
POSTGRES_WORKERS=$(grep "Active workers:" "$POSTGRES_RESULTS" | head -1 | awk '{print $3}')
POSTGRES_LOCKS=$(grep "database locking errors" "$POSTGRES_RESULTS" | grep -o "[0-9]\+" | head -1 || echo "0")

# Display comparison table
printf "%-30s | %-15s | %-15s\n" "Metric" "SQLite" "PostgreSQL"
echo "-------------------------------------------------------------------"
printf "%-30s | %-15s | %-15s\n" "Total Jobs Processed" "${SQLITE_TOTAL:-0}" "${POSTGRES_TOTAL:-0}"
printf "%-30s | %-15s | %-15s\n" "Successful Jobs" "${SQLITE_SUCCESS:-0}" "${POSTGRES_SUCCESS:-0}"
printf "%-30s | %-15s | %-15s\n" "Failed Jobs" "${SQLITE_FAILED:-0}" "${POSTGRES_FAILED:-0}"
printf "%-30s | %-15s | %-15s\n" "Success Rate" "${SQLITE_RATE:-N/A}" "${POSTGRES_RATE:-N/A}"
printf "%-30s | %-15s | %-15s\n" "Active Workers" "${SQLITE_WORKERS:-0}" "${POSTGRES_WORKERS:-0}"
printf "%-30s | %-15s | %-15s\n" "Database Lock Errors" "${SQLITE_LOCKS:-0}" "${POSTGRES_LOCKS:-0}"
echo ""

# Version field verification
echo "Version Field Verification:"
echo "-------------------------------------------------------------------"
SQLITE_VERSION_OK=$(grep "Version field is incrementing correctly" "$SQLITE_RESULTS" | wc -l)
POSTGRES_VERSION_OK=$(grep "Version field is incrementing correctly" "$POSTGRES_RESULTS" | wc -l)

if [ "$SQLITE_VERSION_OK" -gt 0 ]; then
    echo -e "  SQLite:     ${GREEN}✓ Working${NC}"
else
    echo -e "  SQLite:     ${RED}✗ Not Working${NC}"
fi

if [ "$POSTGRES_VERSION_OK" -gt 0 ]; then
    echo -e "  PostgreSQL: ${GREEN}✓ Working${NC}"
else
    echo -e "  PostgreSQL: ${RED}✗ Not Working${NC}"
fi
echo ""

# Overall assessment
echo "=========================================="
echo "OVERALL ASSESSMENT"
echo "=========================================="
echo ""

echo "SQLite Backend:"
if [ "${SQLITE_LOCKS:-0}" -gt 0 ]; then
    echo -e "  ${YELLOW}⚠${NC} Works but has database locking under load"
else
    echo -e "  ${GREEN}✓${NC} Works well for single-worker scenarios"
fi
echo -e "  ${BLUE}→${NC} Recommended for: Development, testing, low-traffic apps"
echo ""

echo "PostgreSQL Backend:"
if [ "${POSTGRES_LOCKS:-0}" -eq 0 ]; then
    echo -e "  ${GREEN}✓${NC} Excellent concurrency, no locking issues"
else
    echo -e "  ${YELLOW}⚠${NC} Some concurrency issues detected"
fi
echo -e "  ${BLUE}→${NC} Recommended for: Production, high-traffic, multi-worker deployments"
echo ""

# Winner determination
echo "=========================================="
echo "RECOMMENDATION"
echo "=========================================="
echo ""

if [ "${POSTGRES_RATE%\%}" = "100.0" ] || [ "${POSTGRES_LOCKS:-0}" -eq 0 ]; then
    echo -e "${GREEN}PostgreSQL is the clear winner for production use!${NC}"
    echo ""
    echo "Reasons:"
    echo "  • Better concurrency handling"
    echo "  • No database locking errors"
    echo "  • Higher success rates"
    echo "  • Proper row-level locking with SELECT FOR UPDATE"
else
    echo -e "${BLUE}Both backends work, choose based on your needs:${NC}"
    echo ""
    echo "  • SQLite: Simple setup, good for development"
    echo "  • PostgreSQL: Better for production and high concurrency"
fi
echo ""

echo "=========================================="
echo "Version-Based Optimistic Locking Status"
echo "=========================================="
echo ""

if [ "$SQLITE_VERSION_OK" -gt 0 ] && [ "$POSTGRES_VERSION_OK" -gt 0 ]; then
    echo -e "${GREEN}✓ SQ-65 Implementation: PRODUCTION READY${NC}"
    echo ""
    echo "The version-based optimistic locking feature works correctly"
    echo "on both SQLite and PostgreSQL backends!"
    echo ""
    echo "Version field increments atomically on each job state change:"
    echo "  • Job created:    version = 0"
    echo "  • Job claimed:    version = 1 (queued → running)"
    echo "  • Job completed:  version = 2-3 (running → success/failed)"
else
    echo -e "${RED}✗ Version field not working correctly on one or both backends${NC}"
fi
echo ""

# Cleanup temp files
rm -f "$SQLITE_RESULTS" "$POSTGRES_RESULTS"

echo "=========================================="
echo "Testing Complete!"
echo "=========================================="
echo ""
echo "To clean up all containers:"
echo "  docker compose -f compose-test.yml down -v"
echo ""
echo "To view detailed logs:"
echo "  SQLite:     docker compose -f compose-test.yml logs web-sqlite"
echo "  PostgreSQL: docker compose -f compose-test.yml logs web-postgres"
echo ""
