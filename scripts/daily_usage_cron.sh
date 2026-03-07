#!/bin/bash
# Daily Claude usage report generator for cron
# Generates yesterday's usage report and saves to daily_reports/

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${GCP_PROJECT_ID:-your-gcp-project-id}"
OUTPUT_DIR="${SCRIPT_DIR}/daily_reports"
AVG_INPUT_TOKENS=11000
AVG_OUTPUT_TOKENS=4000
LOG_FILE="${SCRIPT_DIR}/daily_usage_cron.log"

# Activate virtual environment
source "${SCRIPT_DIR}/venv/bin/activate"

# Create output directory if needed
mkdir -p "${OUTPUT_DIR}"

# Get yesterday's date
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
OUTPUT_FILE="${OUTPUT_DIR}/claude_cost_${YESTERDAY}.csv"

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

# Skip if report already exists
if [[ -f "${OUTPUT_FILE}" ]]; then
    log "Report for ${YESTERDAY} already exists, skipping"
    exit 0
fi

log "Generating report for ${YESTERDAY}..."

# Run the report
python "${SCRIPT_DIR}/claude_usage_with_cost.py" \
    -p "${PROJECT_ID}" \
    -d "${YESTERDAY}" \
    -o "${OUTPUT_FILE}" \
    --avg-input-tokens "${AVG_INPUT_TOKENS}" \
    --avg-output-tokens "${AVG_OUTPUT_TOKENS}" >> "${LOG_FILE}" 2>&1

# Check if file was created (may be empty day with no usage)
if [[ -f "${OUTPUT_FILE}" ]]; then
    log "Report generated: ${OUTPUT_FILE}"
else
    log "No usage data for ${YESTERDAY} (file not created)"
fi
