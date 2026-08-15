"""Nimbus application shell: the windowed app (SHELL_AND_CHAT.md §3).

A package rather than one module because a single ``window.py`` covering a custom title bar,
navigation and five pages becomes unreviewable fast.

    window.py       MainWindow -- title bar + nav + page stack, and the integration surface
    nav.py          Sidebar, NavItem, the footer status block
    titlebar.py     the frameless title bar (drag, minimise, maximise, close)
    widgets.py      shared design-system primitives (Card, PowerToggle, grain overlay)
    pages/          home, knowledge, journal, settings, account

``MainWindow`` is importable as ``shell.MainWindow``, but **lazily** -- the ``__getattr__``
below means ``import shell.pages.knowledge`` does not drag in the whole window and every page
with it. Everything visual lives behind a real import so nothing here needs Qt at package
import time.
"""
from __future__ import annotations

__all__ = ["MainWindow"]


def __getattr__(name: str):
    if name == "MainWindow":
        from shell.window import MainWindow
        return MainWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
