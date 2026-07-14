---
description: Run Mindlas Context Repair — save a validated working-state pack, mark a resume, then prompt for /clear
allowed-tools: Bash(mindlas context repair --apply:*)
---

!`mindlas context repair --apply --session ${CLAUDE_SESSION_ID}`

The Context Repair pack and resume marker are now written (see the paths above).

**Next step — run `/clear` now.** Resetting the context is the one action a hook cannot
perform; it is a human keystroke by design. On the next session start, Mindlas detects the
resume marker and automatically reseeds the validated pack as additional context, so the
fresh session begins lean but fully grounded.
