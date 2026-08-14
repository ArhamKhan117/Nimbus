from __future__ import annotations

import json


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_is_newer_version_compares_numeric_segments():
    from updates import is_newer_version

    assert is_newer_version("v1.0.12", "1.0.9") is True
    assert is_newer_version("1.0.0", "1.0.0") is False
    assert is_newer_version("not-a-version", "1.0.0") is False


def test_check_for_update_returns_newer_release():
    from updates import check_for_update

    info = check_for_update(
        current_version="1.0.2",
        opener=lambda *_args, **_kwargs: _Response({
            "tag_name": "v1.0.3",
            "html_url": "https://example.test/releases/v1.0.3",
        }),
    )

    assert info is not None
    assert info.version == "1.0.3"
    assert info.url == "https://example.test/releases/v1.0.3"


def test_check_for_update_ignores_network_errors():
    from updates import check_for_update

    def offline(*_args, **_kwargs):
        raise OSError("offline")

    assert check_for_update(opener=offline) is None


def test_nothing_calls_the_update_check_at_startup():
    """The update dialog was removed; this keeps it removed.

    `app.py` used to hit the GitHub Releases API on a background thread at launch and, on a newer
    tag, open a modal offering to download it. It fired on first run, it could not actually
    install anything -- "Open" just opened a browser -- and it was an unannounced outbound call
    from an app whose Settings screen promises nothing leaves the machine.

    `updates.py` itself is kept and still tested above: it is pure version comparison plus a
    release-feed query, with no UI, and it is what a real updater would be built on. This guard
    is about the *wiring*. A one-line reintroduction in `app.py` is easy and would be invisible
    until a user complained again, so the absence is asserted rather than described in a comment.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app.py"
    text = source.read_text(encoding="utf-8")

    # Only in the comment that explains the removal, never as code.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "check_for_updates_async" not in stripped
        assert "sig_update_available" not in stripped
        assert "from updates import" not in stripped
