"""Shell pages (SHELL_AND_CHAT.md §3 `S-2`).

    home.py       power, provider, this-week, recent activity, privacy
    knowledge.py  per-app knowledge base browser with drag-and-drop
    journal.py    the T3-3 review queue
    settings.py   hosts settings_dialog.SettingsForm
    account.py    licence status, device, deactivate, sign out, quit

Every page is constructible with **no arguments** and renders an honest empty state, because
every data source reaches it as an injected callable. That is what lets the whole shell be
tested without ``NimbusApp``, and it is also why a page can never reach into the pipeline.

Three pages were considered and deliberately left out (§3): a diagnostics/log viewer (Explorer
and the debug log already serve it), a memory browser (``memory.py``'s contract is plain
Markdown the user can edit anywhere, and a bespoke viewer weakens that), and a charts
dashboard (the reference is a trading app where the numbers *are* the product; Nimbus's
numbers are incidental).
"""
