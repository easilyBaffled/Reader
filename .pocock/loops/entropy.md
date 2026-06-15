# Entropy Loop

Fight software entropy. Clean up the codebase.

## Process

1. Scan for code smells:
   - Unused imports
   - Dead code
   - Inconsistent patterns
   - Duplicate logic
   - TODO/FIXME comments
   - Bare `except` clauses
   - Print statements (should be logging)

2. Fix ONE issue per iteration
   - Keep changes small and focused
   - Don't refactor everything at once

3. Run feedback loops:

   ```bash
   uv run pytest && uv run ruff check . && uv run ruff format --check .
   ```

4. Document what you changed in progress.md

5. Commit with message like:

   ```
   chore: remove unused import from pipeline.py
   ```

6. Repeat until codebase is clean

If no more code smells found, output:
<promise>COMPLETE</promise>
