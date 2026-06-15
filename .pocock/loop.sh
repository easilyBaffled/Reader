#!/bin/bash
#
# Pocock Loop - Iterative Issue Processing
#
# Usage:
#   ./.pocock/loop.sh <iterations>              # Run N iterations on any ready issues
#   ./.pocock/loop.sh <iterations> --epic <id>  # Run N iterations on an epic
#

set -e

cd "$(dirname "$0")/.."

# Require iteration count
if [[ -z "$1" ]]; then
    echo "Usage: ./.pocock/loop.sh <iterations> [--epic <id>]"
    exit 1
fi

MAX_ITERATIONS=$1
shift

# Parse optional epic filter
EPIC_FILTER=""
BD_CMD="bd ready"
while [[ $# -gt 0 ]]; do
    case $1 in
        --epic|-e)
            EPIC_FILTER="$2"
            BD_CMD="bd ready --parent $2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

STUCK=0
METRICS_FILE=".pocock/metrics.csv"

# Initialize metrics file with header if it doesn't exist
if [[ ! -f "$METRICS_FILE" ]]; then
    echo "iteration,timestamp,duration_s,files_changed,exit_code,status" > "$METRICS_FILE"
fi

echo "=========================================="
echo "Pocock Loop - Starting"
echo "Max iterations: $MAX_ITERATIONS"
echo "Issue filter: $BD_CMD"
echo "=========================================="
echo ""

for i in $(seq 1 $MAX_ITERATIONS); do
    echo "=========================================="
    echo "ITERATION $i of $MAX_ITERATIONS"
    echo "$(date '+%Y-%m-%d %H:%M:%S')"
    echo "=========================================="

    ITER_START=$(date +%s)

    # Get ready issues from Beads
    ISSUES=$($BD_CMD 2>/dev/null || echo "")

    if [[ -z "$ISSUES" ]]; then
        echo "No issues found - scope complete"
        break
    fi

    # Mid-loop context injection
    INJECT=""
    if [[ -f .pocock/inject.md ]]; then
        echo ">> Injecting context from .pocock/inject.md"
        INJECT="
## Human Guidance (injected mid-loop)
$(cat .pocock/inject.md)

---
"
        rm .pocock/inject.md
    fi

    # Get recent git context
    RECENT_COMMITS=$(git log --oneline -10 2>/dev/null || echo "No commits")

    # Build prompt
    PROMPT="${INJECT}$ISSUES

## Recent Commits (for context)
$RECENT_COMMITS

@.pocock/progress.md @.pocock/prompt.md"

    # Run Claude and capture output
    OUTPUT=$(CLAUDE_CODE_USE_BEDROCK=0 AWS_PROFILE="" ANTHROPIC_MODEL="" ANTHROPIC_SMALL_FAST_MODEL="" \
      claude --settings ~/.claude/settings.personal.json --dangerously-skip-permissions "$PROMPT" 2>&1) || true
    EXIT_CODE=$?
    echo "$OUTPUT"

    # Measure iteration
    ITER_END=$(date +%s)
    DURATION=$((ITER_END - ITER_START))
    CHANGED=$(git diff --stat HEAD~1 HEAD 2>/dev/null | wc -l | tr -d ' ')

    # Struggle detection
    if [[ "$CHANGED" -eq 0 ]]; then
        STUCK=$((STUCK + 1))
        echo ">> No file changes detected (stuck: $STUCK/3)"
        if [[ $STUCK -ge 3 ]]; then
            echo ""
            echo "=========================================="
            echo "STUCK: 3 iterations with no changes. Pausing."
            echo "Write guidance to .pocock/inject.md and restart."
            echo "=========================================="
            echo "$i,$(date +%s),$DURATION,$CHANGED,$EXIT_CODE,stuck" >> "$METRICS_FILE"
            break
        fi
    else
        STUCK=0
    fi

    # Log metrics
    STATUS="ok"
    if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
        STATUS="complete"
    fi
    echo "$i,$(date +%s),$DURATION,$CHANGED,$EXIT_CODE,$STATUS" >> "$METRICS_FILE"

    # Check for completion signal
    if [[ "$STATUS" == "complete" ]]; then
        echo ""
        echo "=========================================="
        echo "COMPLETE signal detected - all issues done"
        echo "=========================================="
        break
    fi

    echo ""
    sleep 3
done

echo ""
echo "=========================================="
echo "Pocock Loop - Finished"
echo "=========================================="
bd stats 2>/dev/null || true
