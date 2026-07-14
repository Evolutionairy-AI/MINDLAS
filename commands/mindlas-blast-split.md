---
description: Run the Mindlas Patch Splitter — partition a broad change set into smaller, coherent, reviewable bundles and lower the Change Blast Radius (BLAST) gauge
allowed-tools: Bash(mindlas blast split --apply:*)
---

!`mindlas blast split --apply --session ${CLAUDE_SESSION_ID}`

The Patch Splitter planned a deterministic split of this session's diff and wrote a validated
split queue under `.mindlas/splits/` (see the manifest path above). The `before → planned` move
reflects the BLAST the split **would** achieve once the bundles are landed separately — it is a
*planned* reduction, never modeled and never evidence-based, so the gauge stays honest.

**No source files were touched.** The Splitter only reads the diff and writes bundle metadata —
it never modifies, stages, commits, or rewrites your code. Landing each bundle is a human step by
design.

**If it reported no split:** the diff is already small and coherent (one bundle, or the dominant
bundle already carries the whole BLAST), so no artifacts were written and no win was claimed —
there is nothing to partition.

**Next step:** review the bundles in the manifest and land them one coherent group at a time.
After each bundle, run `/mindlas-verify` (`mindlas verify gate --preview`) so verification tracks
the narrower change set rather than the original broad patch.
