#!/bin/bash
# Génère les rapports de coûts quotidiens depuis le 1er janvier 2025

PROJECT_ID="${GCP_PROJECT_ID:-your-gcp-project-id}"
OUTPUT_DIR="daily_reports"
AVG_INPUT=11000
AVG_OUTPUT=4000

START_DATE="2025-01-01"
END_DATE="2026-01-19"

current="$START_DATE"
while [[ "$current" < "$END_DATE" ]] || [[ "$current" == "$END_DATE" ]]; do
    output_file="${OUTPUT_DIR}/claude_cost_${current}.csv"

    if [[ -f "$output_file" ]]; then
        echo "Skipping $current (already exists)"
    else
        echo "Processing $current..."
        python claude_usage_with_cost.py \
            -p "$PROJECT_ID" \
            -d "$current" \
            -o "$output_file" \
            --avg-input-tokens "$AVG_INPUT" \
            --avg-output-tokens "$AVG_OUTPUT"
    fi

    # Incrémenter la date
    current=$(date -d "$current + 1 day" +%Y-%m-%d)
done

echo "Done! Reports saved in $OUTPUT_DIR/"
