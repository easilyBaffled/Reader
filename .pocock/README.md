# Pocock Loop

A streamlined autonomous issue processor inspired by [Matt Pocock's approach](https://www.aihero.dev/tips-for-ai-coding-with-ralph-wiggum).

## Philosophy

1. **Simple over complex** - The prompt is ~100 lines, not 400
2. **Learnings persist** - Each iteration leaves notes for the next
3. **Context via commits** - Recent git history provides awareness
4. **One issue, one commit** - Focused, atomic changes
5. **Fight entropy** - Leave the codebase better than you found it

## Files

| File             | Purpose                                               |
| ---------------- | ----------------------------------------------------- |
| `prompt.md`      | Main instructions (minimal, focused)                  |
| `progress.md`    | Rolling context - last 3 iterations + learnings       |
| `archive.md`     | Historical iteration logs (moved from progress.md)    |
| `inject.md`      | Mid-loop human guidance (consumed + deleted per iter) |
| `metrics.csv`    | Iteration metrics log (auto-created)                  |
| `once.sh`        | Run single iteration                                  |
| `loop.sh`        | Run multiple iterations locally                       |
| `loop-custom.sh` | Run alternative prompts in a loop                     |
| `loops/`         | Alternative loop prompts                              |

## Usage

### Single iteration

```bash
./.pocock/once.sh
./.pocock/once.sh --epic reader-xyz
```

### Multiple iterations (local)

```bash
./.pocock/loop.sh 10
./.pocock/loop.sh 5 --epic reader-xyz
```

### Alternative loops

```bash
# Test coverage loop
./.pocock/loop-custom.sh 10 .pocock/loops/coverage.md

# Entropy/cleanup loop
./.pocock/loop-custom.sh 20 .pocock/loops/entropy.md

# Linting loop
./.pocock/loop-custom.sh 15 .pocock/loops/lint.md
```

## Struggle Detection

All loop scripts track consecutive iterations with no file changes:

- After **3 stuck iterations**, the loop pauses automatically
- Console message tells you to write `.pocock/inject.md` with guidance
- Prevents burning tokens on a stuck task

## Mid-Loop Context Injection

Steer the loop without stopping it:

```bash
# While loop is running, drop guidance into inject.md
echo "Skip the RSS extractor — focus on core URL pipeline first" > .pocock/inject.md
```

Next iteration picks it up, acts on it, and deletes the file.

## Iteration Metrics

Every iteration logs to `.pocock/metrics.csv`:

```csv
iteration,timestamp,duration_s,files_changed,exit_code,status
1,1718400000,145,7,0,ok
2,1718400150,89,4,0,ok
3,1718400240,12,0,0,stuck
```

## Task Prioritization

The agent chooses tasks based on this priority:

1. **Architectural decisions** - Core abstractions, patterns
2. **Integration points** - Where modules connect
3. **Unknown unknowns** - Spike work, risky experiments
4. **Standard features** - Normal implementation
5. **Polish/quick wins** - Easy stuff last

Fail fast on risky work. Save easy wins for later.

## Progress.md Structure

```markdown
## Recent Context (Last 3 Iterations)

<!-- Rolling window with learnings, decisions, roadblocks -->

## Active Roadblocks

<!-- Issues that need attention -->

## Project Learnings

<!-- Organized by topic -->
```

**Key rule:** Sacrifice grammar for concision. This file helps future iterations skip exploration.

## Alternative Loop Types

| Loop         | Purpose                                          |
| ------------ | ------------------------------------------------ |
| **Coverage** | Increase test coverage systematically            |
| **Entropy**  | Clean up code smells, dead code, inconsistencies |
| **Lint**     | Fix linting errors one at a time                 |

Any task that fits "look at repo, improve something, report findings" works.

## References

- [11 Tips For AI Coding With Ralph Wiggum](https://www.aihero.dev/tips-for-ai-coding-with-ralph-wiggum)
- [Getting Started With Ralph](https://www.aihero.dev/getting-started-with-ralph)
