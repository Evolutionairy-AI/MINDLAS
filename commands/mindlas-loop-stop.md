---
description: Stop a Tool Failure Loop — record a controlled stop boundary; the LOOP gauge falls to controlled
allowed-tools: Bash(mindlas loop stop --apply:*)
---

!`mindlas loop stop --apply --session ${CLAUDE_SESSION_ID}`

The stop boundary is now recorded (see the stop card above). The same failing command must not
be retried unchanged — change the plan first: a different command, a fixed environment
assumption, or ask the user.

When the reason for the loop has genuinely changed, re-arm the gauge with `mindlas loop release`.
