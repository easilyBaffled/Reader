# Test Coverage Loop

Increase test coverage systematically.

## Process

1. Run coverage report:

   ```bash
   uv run pytest --cov=audibleweb --cov-report=term-missing
   ```

2. Find uncovered lines in the coverage report

3. Write tests for the most critical uncovered code paths
   - Focus on business logic first
   - Then edge cases
   - Then error paths

4. Run tests to verify they pass

5. Run coverage again

6. Update progress.md with:
   - Coverage before/after
   - Files tested
   - Decisions made

7. Commit changes

**Target:** Keep going until coverage hits target or no more meaningful code to cover.

If coverage target reached, output:
<promise>COMPLETE</promise>
