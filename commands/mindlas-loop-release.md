---
description: Release the Tool Failure Loop stop boundary — the guard stands down and LOOP re-arms
allowed-tools: Bash(mindlas loop release)
---

!`mindlas loop release`

The stop boundary is released (artifacts are kept under `.mindlas/stops/`). The previously
stopped command may run again, and a future failure loop can trigger Stop again.

Release only when the *reason* for the loop has genuinely changed — a fixed environment,
a corrected command, or new information. Releasing to blindly retry recreates the loop.
