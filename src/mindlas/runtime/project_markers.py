"""Canonical project-marker sets — the single source of truth for "how Mindlas recognizes
kinds of files in a repo" (config markers, test/production classification, central-config
blast markers). Previously these tuples were RE-DEFINED in verification_state.py, verify_plan.py,
blast_state.py, and testtier.py, where they had already started to diverge (see the setup.py
note below). Consolidating them here means one place to edit and no silent drift.

Pure literals, no imports — so any module (runtime or vitals) can import this without a cycle.

Divergence made visible (was a latent bug): `setup.py` is a PYTHON_PROJECT_MARKER (it marks a
Python project and gates the verify planner) but is deliberately NOT in CONFIG_FILE_NAMES, so a
changed `setup.py` still classifies as `production` via its `.py` suffix. That asymmetry is
preserved here on purpose; if it should be reconciled, this is now the one place to do it."""
from __future__ import annotations

# --- file classification (runtime.verification_state.classify_path) ---------------------------
# Test files: name globs + directory names.
TEST_FILE_GLOBS = ("test_*.py", "*_test.py", "*.spec.ts", "*.spec.tsx", "*.spec.js",
                   "*.test.ts", "*.test.tsx", "*.test.js")
TEST_DIR_NAMES = ("tests", "__tests__")

# Production source: file extensions treated as runnable product code (multi-language).
PRODUCTION_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".java", ".go", ".rs",
                         ".cpp", ".c", ".h", ".hpp", ".rb", ".php", ".swift", ".kt")

# Config files: exact names + globs. Multi-language; the broad "is this a config file" set.
CONFIG_FILE_NAMES = ("pyproject.toml", "package.json", "tsconfig.json", "Cargo.toml", "go.mod",
                     "Makefile", "pytest.ini", "setup.cfg", "tox.ini")
CONFIG_FILE_GLOBS = ("*.csproj", "*.sln")

# --- Python verify planner (runtime.verify_plan) ----------------------------------------------
# "Is this a Python project?" — gates detect_project_stack + the L2 ruff tier. Includes setup.py.
PYTHON_PROJECT_MARKERS = ("pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "tox.ini")
# "Where can pytest config live?" — gates the L3/L4 pytest tiers.
PYTEST_CONFIG_FILES = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")
# testtier's deliberately NARROWER heuristic for auto-launching a background pytest run. Kept
# distinct from PYTEST_CONFIG_FILES on purpose: auto-spawning a suite is a stronger action than
# gating, so it fires only on the two strongest pytest signals (not setup.cfg / tox.ini).
PYTEST_AUTORUN_MARKERS = ("pyproject.toml", "pytest.ini")

# --- Change Blast Radius central-config markers (runtime.blast_state) --------------------------
# Config whose change has wide coordination blast radius -> "high" centrality.
CENTRAL_CONFIG_FILES = frozenset({"package.json", "pyproject.toml", "Cargo.toml", "go.mod"})
CENTRAL_CONFIG_GLOBS = ("*.sln", "*.csproj")
