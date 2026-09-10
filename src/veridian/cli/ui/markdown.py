"""Render model prose as Markdown with syntax-highlighted code fences.

The model contract only promises text. When that text is Markdown (it usually is), rendering it as
Markdown makes headings, lists, and code blocks legible; when it is plain prose, Markdown rendering
is a no-op. Either way the output is the model's, untouched in meaning.
"""

from __future__ import annotations

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text

from veridian.cli.ui.theme import CODE_THEME


def model_markdown(text: str) -> Group:
    """A renderable for one chunk of model output."""
    body = text.strip("\n")
    if not body:
        return Group(Text(""))
    return Group(Markdown(body, code_theme=CODE_THEME, inline_code_lexer="text"))
