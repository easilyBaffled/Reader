#!/bin/bash
#
# Pocock Loop - Single Iteration
# Runs Claude once with Beads issues and progress context
#
# Usage:
#   ./.pocock/once.sh                    # Work on any ready issue
#   ./.pocock/once.sh --epic <epic-id>   # Work on issues in a specific epic
#

set -e

cd "$(dirname "$0")/.."

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

# Get ready issues from Beads
ISSUES=$($BD_CMD 2>/dev/null || echo "No issues found")

# Get recent git context (last 10 commits)
RECENT_COMMITS=$(git log --oneline -10 2>/dev/null || echo "No commits")

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

# Build the prompt
PROMPT="${INJECT}$ISSUES

## Recent Commits (for context)
$RECENT_COMMITS

@.pocock/progress.md @.pocock/prompt.md"

# Run Claude
echo "Starting Pocock iteration..."
echo "Issues: $BD_CMD"
echo ""

CLAUDE_CODE_USE_BEDROCK=0 AWS_PROFILE="" ANTHROPIC_MODEL="" ANTHROPIC_SMALL_FAST_MODEL="" \
  claude --settings ~/.claude/settings.personal.json --dangerously-skip-permissions "$PROMPT"
