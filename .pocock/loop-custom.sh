#!/bin/bash
#
# Pocock Custom Loop - Run any prompt in a loop
#
# Usage:
#   ./.pocock/loop-custom.sh <iterations> <prompt-file>
#
# Example:
#   ./.pocock/loop-custom.sh 10 .pocock/loops/coverage.md
#   ./.pocock/loop-custom.sh 20 .pocock/loops/entropy.md
#

set -e

cd "$(dirname "$0")/.."

if [[ -z "$1" || -z "$2" ]]; then
    echo "Usage: ./.pocock/loop-custom.sh <iterations> <prompt-file>"
    echo ""
    echo "Available prompts:"
    ls -1 .pocock/loops/*.md 2>/dev/null | sed 's/^/  /'
    exit 1
fi

MAX_ITERATIONS=$1
PROMPT_FILE=$2

if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "ERROR: Prompt file not found: $PROMPT_FILE"
    exit 1
fi

STUCK=0
METRICS_FILE=".pocock/metrics.csv"

# Initialize metrics file with header if it doesn't exist
if [[ ! -f "$METRICS_FILE" ]]; then
    echo "iteration,timestamp,duration_s,files_changed,exit_code,status" > "$METRICS_FILE"
fi

echo "=========================================="
echo "Pocock Custom Loop"
echo "Max iterations: $MAX_ITERATIONS"
echo "Prompt: $PROMPT_FILE"
echo "=========================================="
echo ""

for i in $(seq 1 $MAX_ITERATIONS); do
    echo "=========================================="
    echo "ITERATION $i of $MAX_ITERATIONS"
    echo "$(date '+%Y-%m-%d %H:%M:%S')"
    echo "=========================================="

    ITER_START=$(date +%s)

    # Mid-loop context injection
    INJECT=""
    if [[ -f .pocock/inject.md ]]; then
        echo ">> Injecting context from .pocock/inject.md"
        INJECT="## Human Guidance (injected mid-loop)
$(cat .pocock/inject.md)

---

"
        rm .pocock/inject.md
    fi

    # Get recent git context
    RECENT_COMMITS=$(git log --oneline -10 2>/dev/null || echo "No commits")

    # Build prompt with context
    PROMPT="${INJECT}## Recent Commits (for context)
$RECENT_COMMITS

$(cat .pocock/progress.md)

---

$(cat "$PROMPT_FILE")"

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
        echo "COMPLETE signal detected"
        echo "=========================================="
        break
    fi

    echo ""
    sleep 3
done

echo ""
echo "=========================================="
echo "Custom Loop - Finished"
echo "=========================================="
