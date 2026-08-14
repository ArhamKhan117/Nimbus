"""GitHub Release update checks for Nimbus.

Network I/O stays outside Qt. ``app.py`` runs ``check_for_update`` on a
daemon thread, then presents any result through a Qt signal on the main
thread.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.request import Request, urlopen

from version import APP_VERSION


REPOSITORY = "ArhamKhan117/Nimbus"
"""Where releases live. **Must be a repository that is readable with no credentials.**

The failure mode is the reason this docstring is long. This module calls the GitHub API with no
credentials, and anything it cannot read answers 404: a repository that is private, renamed, or simply
misspelled all look identical from here. None of them raise. The check reads the 404 as "no newer
release" and reports the user up to date forever, with no error anywhere and nothing to notice.

So the requirement is not "a repository" but *this* one, public, holding the releases, verified by
opening the API URL below in a browser with no session. Releases live in the source repository, beside
the commits they describe, so the changelog matches the build and there is one place to look.

This constant is baked into every installer, so getting it wrong is not correctable for anyone who
already has one."""

RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str


def _version_key(value: str) -> tuple[int, ...] | None:
    """Parse ``v1.2.3`` / ``1.2.3`` into a comparable numeric tuple."""
    value = value.strip().removeprefix("v")
    try:
        return tuple(int(part) for part in value.split("."))
    except (TypeError, ValueError):
        return None


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    """Return whether a valid release version is newer than this build."""
    candidate_key = _version_key(candidate)
    current_key = _version_key(current)
    if candidate_key is None or current_key is None:
        return False
    width = max(len(candidate_key), len(current_key))
    return candidate_key + (0,) * (width - len(candidate_key)) > current_key + (0,) * (width - len(current_key))


def check_for_update(
    *,
    current_version: str = APP_VERSION,
    opener=urlopen,
) -> UpdateInfo | None:
    """Return the latest GitHub release when it is newer than this build.

    Fail closed on offline machines, rate limits, malformed responses, or a
    missing release. An update check must never delay or prevent Nimbus from
    starting.
    """
    request = Request(
        LATEST_RELEASE_API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Nimbus"},
    )
    try:
        with opener(request, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        version = str(payload.get("tag_name", ""))
        url = str(payload.get("html_url", RELEASES_URL))
    except Exception:
        return None
    if not is_newer_version(version, current_version):
        return None
    return UpdateInfo(version=version.removeprefix("v"), url=url)
