#!/bin/bash
#
# Push Claude usage metrics to Prometheus Pushgateway using gcloud CLI
# No Python dependencies required - uses only gcloud and bash
#

set -e

# Configuration from environment or defaults
PROJECT_ID="${GCP_PROJECT_ID:-}"
PUSHGATEWAY="${PUSHGATEWAY_URL:-pushgateway:9091}"
AVG_INPUT="${AVG_INPUT_TOKENS:-3000}"
AVG_OUTPUT="${AVG_OUTPUT_TOKENS:-1200}"
JOB_NAME="${JOB_NAME:-claude_vertex_usage}"
TARGET_DATE="${TARGET_DATE:-$(date -d 'yesterday' '+%Y-%m-%d')}"

if [ -z "$PROJECT_ID" ]; then
    echo "ERROR: GCP_PROJECT_ID is required"
    exit 1
fi

echo "Project: $PROJECT_ID"
echo "Date: $TARGET_DATE"
echo "Pushgateway: $PUSHGATEWAY"
echo "Token estimates: $AVG_INPUT input, $AVG_OUTPUT output"
echo "Fetching logs..."

# Build timestamp filters
START_TS="${TARGET_DATE}T00:00:00Z"
END_TS=$(date -d "$TARGET_DATE + 1 day" '+%Y-%m-%dT00:00:00Z')

# Fetch logs using gcloud
FILTER='protoPayload.serviceName="aiplatform.googleapis.com" AND protoPayload.methodName=~"rawPredict|streamRawPredict|Predict" AND timestamp >= "'"$START_TS"'" AND timestamp < "'"$END_TS"'"'

LOGS=$(gcloud logging read "$FILTER" \
    --project="$PROJECT_ID" \
    --format="json" \
    --limit=10000 2>/dev/null || echo "[]")

# Count log entries
ENTRY_COUNT=$(echo "$LOGS" | jq 'length')
echo "Log entries fetched: $ENTRY_COUNT"

if [ "$ENTRY_COUNT" -eq 0 ]; then
    echo "No log entries found."
    exit 0
fi

# Process logs and aggregate by user/model
# Filter for Claude models and extract user + model
AGGREGATED=$(echo "$LOGS" | jq -r '
    [.[] |
        select(.protoPayload.resourceName | test("claude|anthropic"; "i")) |
        select((.operation.last == true and .operation.first != true) | not) |
        {
            email: .protoPayload.authenticationInfo.principalEmail,
            model: (.protoPayload.resourceName | capture("models/(?<m>[^@/]+)") | .m // "unknown")
        }
    ] | group_by([.email, .model]) |
    map({
        email: .[0].email,
        model: .[0].model,
        count: length
    })
')

CALL_COUNT=$(echo "$AGGREGATED" | jq '[.[].count] | add // 0')
echo "Claude API calls identified: $CALL_COUNT"

if [ "$CALL_COUNT" -eq 0 ]; then
    echo "No Claude API calls identified."
    exit 0
fi

# Pricing per million tokens
get_price() {
    local model="$1"
    local type="$2"  # input or output
    model_lower=$(echo "$model" | tr '[:upper:]' '[:lower:]')

    case "$model_lower" in
        *haiku*) [ "$type" = "input" ] && echo "1.00" || echo "5.00" ;;
        *opus-4-5*) [ "$type" = "input" ] && echo "5.00" || echo "25.00" ;;
        *opus*) [ "$type" = "input" ] && echo "15.00" || echo "75.00" ;;
        *) [ "$type" = "input" ] && echo "3.00" || echo "15.00" ;;  # sonnet default
    esac
}

# Generate Prometheus metrics
METRICS=""

# Header for requests
METRICS+="# HELP claude_vertex_requests_total Total Claude API requests on Vertex AI\n"
METRICS+="# TYPE claude_vertex_requests_total gauge\n"

# Header for cost
COST_METRICS="# HELP claude_vertex_estimated_cost_usd Estimated cost in USD\n"
COST_METRICS+="# TYPE claude_vertex_estimated_cost_usd gauge\n"

# Track totals per model
declare -A MODEL_REQUESTS
declare -A MODEL_COSTS
declare -A USERS

# Process each user/model combination
while IFS= read -r line; do
    email=$(echo "$line" | jq -r '.email')
    model=$(echo "$line" | jq -r '.model')
    count=$(echo "$line" | jq -r '.count')

    # Sanitize email for Prometheus label
    sanitized_email=$(echo "$email" | sed 's/@/_at_/g; s/\./_/g')

    # Calculate cost
    input_price=$(get_price "$model" "input")
    output_price=$(get_price "$model" "output")
    est_input=$((count * AVG_INPUT))
    est_output=$((count * AVG_OUTPUT))
    cost=$(echo "scale=4; ($est_input / 1000000) * $input_price + ($est_output / 1000000) * $output_price" | bc)

    # Per-user metrics
    METRICS+="claude_vertex_requests_total{user=\"$sanitized_email\",model=\"$model\",date=\"$TARGET_DATE\",project=\"$PROJECT_ID\"} $count\n"
    COST_METRICS+="claude_vertex_estimated_cost_usd{user=\"$sanitized_email\",model=\"$model\",date=\"$TARGET_DATE\",project=\"$PROJECT_ID\"} $cost\n"

    # Accumulate model totals
    MODEL_REQUESTS[$model]=$((${MODEL_REQUESTS[$model]:-0} + count))
    MODEL_COSTS[$model]=$(echo "${MODEL_COSTS[$model]:-0} + $cost" | bc)
    USERS[$email]=1

done < <(echo "$AGGREGATED" | jq -c '.[]')

METRICS+="$COST_METRICS"

# Aggregated metrics per model
METRICS+="# HELP claude_vertex_total_requests Total requests per model\n"
METRICS+="# TYPE claude_vertex_total_requests gauge\n"
for model in "${!MODEL_REQUESTS[@]}"; do
    METRICS+="claude_vertex_total_requests{model=\"$model\",date=\"$TARGET_DATE\",project=\"$PROJECT_ID\"} ${MODEL_REQUESTS[$model]}\n"
done

METRICS+="# HELP claude_vertex_total_cost_usd Total cost per model\n"
METRICS+="# TYPE claude_vertex_total_cost_usd gauge\n"
for model in "${!MODEL_COSTS[@]}"; do
    METRICS+="claude_vertex_total_cost_usd{model=\"$model\",date=\"$TARGET_DATE\",project=\"$PROJECT_ID\"} ${MODEL_COSTS[$model]}\n"
done

# Unique users
UNIQUE_USERS=${#USERS[@]}
METRICS+="# HELP claude_vertex_unique_users Number of unique users\n"
METRICS+="# TYPE claude_vertex_unique_users gauge\n"
METRICS+="claude_vertex_unique_users{date=\"$TARGET_DATE\",project=\"$PROJECT_ID\"} $UNIQUE_USERS\n"

# Push to Pushgateway
echo -e "$METRICS" | curl -s --data-binary @- "http://${PUSHGATEWAY}/metrics/job/${JOB_NAME}"

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Metrics pushed to $PUSHGATEWAY"
    echo "  Total requests: $CALL_COUNT"
    TOTAL_COST=$(echo "${MODEL_COSTS[@]}" | tr ' ' '+' | bc 2>/dev/null || echo "0")
    echo "  Estimated cost: \$$TOTAL_COST"
    echo "  Unique users: $UNIQUE_USERS"
else
    echo "✗ Failed to push metrics"
    exit 1
fi
