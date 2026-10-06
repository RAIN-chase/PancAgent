from __future__ import annotations

from IPython.display import Markdown, display


def mermaid_markdown(source: str) -> Markdown:
    return Markdown(f"```mermaid\n{source}\n```")


def display_mermaid(source: str) -> Markdown:
    markdown = mermaid_markdown(source)
    display(markdown)
    return markdown
