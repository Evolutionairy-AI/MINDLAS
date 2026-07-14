# Configuration

All variables are optional; defaults apply with none set.

## Mindlas variables

| Variable | Default | Effect |
|---|---|---|
| `MINDLAS_HOME` | `~/.mindlas` | Per-machine state root (session ledgers, statusline backup). |
| `MINDLAS_PROJECT_ROOT` | current dir | Read-only override for the project-local `.mindlas/` root. The hooks resolve the project root from the hook payload's `cwd` and never write this variable — set it yourself only for tests or unusual layouts. |
| `MINDLAS_GATE` | `shadow` | Trust gate at propagation boundaries (commit/push/deploy/publish): `off` / `shadow` (decide and record, never halt) / `live` (may block the tool call). |
| `MINDLAS_LOOP_GUARD` | `warn` | Stop-boundary retry guard at `PreToolUse`: `off` / `warn` (terminal notice, never blocks) / `block` (deny the exact stopped command signature, citing the stop id). |
| `MINDLAS_INTERCEPT_COMPACT` | `warn` | `PreCompact` behavior: `off` / `warn` / `block` — `block` currently behaves as `warn`; native compact is never blocked. |
| `MINDLAS_TESTTIER` | on | `0` disables the debounced background test tier. Independent of `MINDLAS_GATE`. |
| `MINDLAS_TEST_PATTERNS` | *(unset)* | Comma-separated extra substrings that classify a command as a test run (appended to built-ins: pytest, npm/yarn test, jest, go test, cargo test, mvn test, unittest, rspec, phpunit, dotnet test). |
| `MINDLAS_BAR` | `segments` | Progress-bar glyphs for the gauges: `segments` (`▰▱`), `blocks` (`█░`), or `ascii` (`#-`). Set `blocks`/`ascii` on a legacy Windows console whose raster font renders the parallelograms as tofu boxes. |

## Claude-provided variables

| Variable | Effect |
|---|---|
| `CLAUDE_CONFIG_DIR` | Where the `install-*` commands read/write Claude Code's `settings.json` (default `~/.claude`). |
| `CLAUDE_PLUGIN_DATA` | When set (plugin loaded), cold-store transcript snapshots go under `${CLAUDE_PLUGIN_DATA}/.mindlas`; else the project `.mindlas/`. |
| `CLAUDE_SESSION_ID` | Expanded by the plugin slash commands into `--session <uuid>`; not read by the Python code directly. |

## Test-suite only

`MINDLAS_RUN_TESTTIER=1` enables the 8 real-subprocess test-tier integration tests that the default suite skips (~3s extra; CI sets it in `.github/workflows/ci.yml`).
