# Linting Loop

Fix all linting errors systematically.

## Process

1. Run lint:

   ```bash
   uv run ruff check .
   ```

2. Fix ONE linting error at a time
   - Don't batch fixes - one error per iteration
   - This keeps changes reviewable

3. Run lint again to verify the fix

4. Document in progress.md:
   - Error fixed
   - File changed

5. Commit:

   ```
   fix: resolve ruff <rule-code> in <file>
   ```

6. Repeat until no errors remain

If lint passes with no errors, output:
<promise>COMPLETE</promise>
