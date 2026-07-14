"""The Action interface: every correction exposes a dry-run preview and an apply.
Apply must be non-native (no built-in agent command) and must record a before/after."""
from __future__ import annotations


class Action:
    action_id: str = ""

    def preview(self, state):
        raise NotImplementedError

    def apply(self, state):
        raise NotImplementedError
