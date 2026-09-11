"""Presentation layer for the interactive CLI.

Pure rendering. It is driven by ``orchestrator.delta`` events and protocol run results only; it
never imports from ``bricks/`` and knows nothing about any provider or model. Swapping the
inference or orchestrator brick leaves everything in here untouched.
"""

from __future__ import annotations

from veridian.cli.ui.activity import Activity
from veridian.cli.ui.gitinfo import current_branch
from veridian.cli.ui.markdown import model_markdown
from veridian.cli.ui.renderer import Renderer
from veridian.cli.ui.selector import select
from veridian.cli.ui.theme import THEME

__all__ = ["Renderer", "Activity", "model_markdown", "THEME", "current_branch", "select"]
