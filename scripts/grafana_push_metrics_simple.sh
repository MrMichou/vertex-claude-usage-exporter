#!/bin/sh
#
# Push Claude usage metrics to Prometheus Pushgateway
# Lightweight version using only curl and jq - compatible with sh
#

set -e

PROJECT_ID="${GCP_PROJECT_ID:-}"
PUSHGATEWAY="${PUSHGATEWAY_URL:-pushgateway:9091}"
AVG_INPUT="${AVG_INPUT_TOKENS:-1500}"
AVG_OUTPUT="${AVG_OUTPUT_TOKENS:-500}"
SA_KEY_FILE="${GOOGLE_APPLICATION_CREDENTIALS:-/var/secrets/google/key.json}"

# Calculate yesterday's date (POSIX-compatible)
if command -v gdate >/dev/null 2>&1; then
    TARGET_DATE="${TARGET_DATE:-$(gdate -d 'yesterday' '+%Y-%m-%d')}"
elif date -d 'yesterday' '+%Y-%m-%d' >/dev/null 2>&1; then
    TARGET_DATE="${TARGET_DATE:-$(date -d 'yesterday' '+%Y-%m-%d')}"
else
    TARGET_DATE="${TARGET_DATE:-$(date -v-1d '+%Y-%m-%d' 2>/dev/null || date '+%Y-%m-%d')}"
fi

# Set job name after TARGET_DATE is determined (unique per date for Pushgateway)
JOB_NAME="${JOB_NAME:-claude_vertex_${TARGET_DATE}}"

if [ -z "$PROJECT_ID" ]; then
    echo "ERROR: GCP_PROJECT_ID is required"
    exit 1
fi

echo "Project: $PROJECT_ID"
echo "Date: $TARGET_DATE"
echo "Pushgateway: $PUSHGATEWAY"
echo "Token estimates: $AVG_INPUT input, $AVG_OUTPUT output"

# Get access token using service account key
get_access_token() {
    SA_EMAIL=$(jq -r '.client_email' "$SA_KEY_FILE")
    TOKEN_URI=$(jq -r '.token_uri' "$SA_KEY_FILE")

    NOW=$(date +%s)
    EXP=$((NOW + 3600))

    # Create JWT parts
    HEADER=$(printf '{"alg":"RS256","typ":"JWT"}' | openssl base64 -e | tr -d '\n=' | tr '+/' '-_')
    CLAIM=$(printf '{"iss":"%s","scope":"https://www.googleapis.com/auth/logging.read","aud":"%s","iat":%d,"exp":%d}' \
        "$SA_EMAIL" "$TOKEN_URI" "$NOW" "$EXP" | openssl base64 -e | tr -d '\n=' | tr '+/' '-_')

    # Extract private key to temp file and sign
    KEYFILE=$(mktemp)
    jq -r '.private_key' "$SA_KEY_FILE" > "$KEYFILE"
    SIGNATURE=$(printf '%s.%s' "$HEADER" "$CLAIM" | \
        openssl dgst -sha256 -sign "$KEYFILE" | \
        openssl base64 -e | tr -d '\n=' | tr '+/' '-_')
    rm -f "$KEYFILE"

    JWT="${HEADER}.${CLAIM}.${SIGNATURE}"

    # Exchange for access token
    curl -s -X POST "$TOKEN_URI" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=$JWT" \
        | jq -r '.access_token'
}

echo "Authenticating..."
ACCESS_TOKEN=$(get_access_token)

if [ -z "$ACCESS_TOKEN" ] || [ "$ACCESS_TOKEN" = "null" ]; then
    echo "ERROR: Failed to get access token"
    exit 1
fi
echo "Authentication successful"

echo "Fetching logs..."

# Calculate timestamps
START_TS="${TARGET_DATE}T00:00:00Z"
# Calculate next day (simple approach - add 1 to day)
YEAR=$(echo "$TARGET_DATE" | cut -d'-' -f1)
MONTH=$(echo "$TARGET_DATE" | cut -d'-' -f2)
DAY=$(echo "$TARGET_DATE" | cut -d'-' -f3)
NEXT_DAY=$((DAY + 1))
# Handle month overflow (simplified - assumes 31 days max)
if [ "$NEXT_DAY" -gt 28 ]; then
    if date -d "${TARGET_DATE} + 1 day" '+%Y-%m-%d' >/dev/null 2>&1; then
        END_DATE=$(date -d "${TARGET_DATE} + 1 day" '+%Y-%m-%d')
    else
        END_DATE=$(date -v+1d -j -f "%Y-%m-%d" "$TARGET_DATE" '+%Y-%m-%d' 2>/dev/null || echo "${YEAR}-${MONTH}-${NEXT_DAY}")
    fi
else
    END_DATE=$(printf '%s-%s-%02d' "$YEAR" "$MONTH" "$NEXT_DAY")
fi
END_TS="${END_DATE}T00:00:00Z"

# Build and execute query
FILTER='protoPayload.serviceName="aiplatform.googleapis.com" AND protoPayload.methodName=~"RawPredict|StreamRawPredict|Predict" AND timestamp >= "'"$START_TS"'" AND timestamp < "'"$END_TS"'"'

# Use jq to properly build JSON payload
PAYLOAD=$(jq -n \
    --arg project "projects/$PROJECT_ID" \
    --arg filter "$FILTER" \
    '{resourceNames: [$project], filter: $filter, pageSize: 1000, orderBy: "timestamp desc"}')

LOGS=$(curl -s -X POST \
    "https://logging.googleapis.com/v2/entries:list" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

# Check for errors
if echo "$LOGS" | jq -e '.error' >/dev/null 2>&1; then
    echo "ERROR: $(echo "$LOGS" | jq -r '.error.message')"
    exit 1
fi

ENTRIES=$(echo "$LOGS" | jq '.entries // []')
ENTRY_COUNT=$(echo "$ENTRIES" | jq 'length')
echo "Log entries fetched: $ENTRY_COUNT"

if [ "$ENTRY_COUNT" -eq 0 ]; then
    echo "No log entries found."
    exit 0
fi

# Process and aggregate by user/model
AGGREGATED=$(echo "$ENTRIES" | jq '
    [.[] |
        select(.protoPayload.resourceName | test("claude|anthropic"; "i")) |
        select((.operation.last == true and .operation.first != true) | not) |
        {
            email: .protoPayload.authenticationInfo.principalEmail,
            model: (.protoPayload.resourceName | capture("models/(?<m>[^@/]+)") | .m // "unknown")
        }
    ] | group_by([.email, .model]) |
    map({email: .[0].email, model: .[0].model, count: length})
')

CALL_COUNT=$(echo "$AGGREGATED" | jq '[.[].count] | add // 0')
echo "Claude API calls identified: $CALL_COUNT"

if [ "$CALL_COUNT" -eq 0 ]; then
    echo "No Claude API calls identified."
    exit 0
fi

# Generate Prometheus metrics using jq
METRICS=$(echo "$AGGREGATED" | jq -r --arg date "$TARGET_DATE" --arg project "$PROJECT_ID" \
    --arg avg_in "$AVG_INPUT" --arg avg_out "$AVG_OUTPUT" '
    def get_price(model):
        (model | ascii_downcase) as $m |
        if ($m | contains("haiku")) then {input: 1, output: 5}
        elif ($m | contains("opus-4-5")) then {input: 5, output: 25}
        elif ($m | contains("opus")) then {input: 15, output: 75}
        else {input: 3, output: 15}
        end;

    def sanitize_email: gsub("@"; "_at_") | gsub("\\."; "_");

    . as $data |

    # Per-user metrics
    "# HELP claude_vertex_requests_total Total Claude API requests\n# TYPE claude_vertex_requests_total gauge",
    ($data[] | "claude_vertex_requests_total{user=\"\(.email | sanitize_email)\",model=\"\(.model)\",date=\"\($date)\",project=\"\($project)\"} \(.count)"),

    "# HELP claude_vertex_estimated_cost_usd Estimated cost in USD\n# TYPE claude_vertex_estimated_cost_usd gauge",
    ($data[] |
        get_price(.model) as $price |
        ((.count * ($avg_in | tonumber) / 1000000) * $price.input + (.count * ($avg_out | tonumber) / 1000000) * $price.output) as $cost |
        "claude_vertex_estimated_cost_usd{user=\"\(.email | sanitize_email)\",model=\"\(.model)\",date=\"\($date)\",project=\"\($project)\"} \($cost)"
    ),

    # Aggregated by model
    "# HELP claude_vertex_total_requests Total requests per model\n# TYPE claude_vertex_total_requests gauge",
    (group_by(.model) | .[] |
        {model: .[0].model, total: (map(.count) | add)} |
        "claude_vertex_total_requests{model=\"\(.model)\",date=\"\($date)\",project=\"\($project)\"} \(.total)"
    ),

    "# HELP claude_vertex_total_cost_usd Total cost per model\n# TYPE claude_vertex_total_cost_usd gauge",
    ($data | group_by(.model) | .[] |
        .[0].model as $model |
        get_price($model) as $price |
        (map(.count) | add) as $total |
        (($total * ($avg_in | tonumber) / 1000000) * $price.input + ($total * ($avg_out | tonumber) / 1000000) * $price.output) as $cost |
        "claude_vertex_total_cost_usd{model=\"\($model)\",date=\"\($date)\",project=\"\($project)\"} \($cost)"
    ),

    # Unique users
    "# HELP claude_vertex_unique_users Number of unique users\n# TYPE claude_vertex_unique_users gauge",
    "claude_vertex_unique_users{date=\"\($date)\",project=\"\($project)\"} \([.[].email] | unique | length)"
')

# Push to Pushgateway
echo "$METRICS" | curl -s --data-binary @- "http://${PUSHGATEWAY}/metrics/job/${JOB_NAME}"
RESULT=$?

if [ $RESULT -eq 0 ]; then
    echo ""
    echo "Metrics pushed to $PUSHGATEWAY"
    echo "  Total requests: $CALL_COUNT"
    UNIQUE=$(echo "$AGGREGATED" | jq '[.[].email] | unique | length')
    echo "  Unique users: $UNIQUE"
else
    echo "Failed to push metrics"
    exit 1
fi
