"""Explicit context caching for the native Gemini path (T1-6a).

The knowledge base is by far the largest stable payload Nimbus sends: a max-size KB is
``KB_RECALL_MAX_CHARS = 60_000`` chars, measured at **10,002 tokens**. It is re-sent on
every single interaction within an app session even though it never changes, so it is
the ideal caching target.

Verified live before building: a 10,002-token payload caches successfully and reports
``cached_content_token_count: 10008`` against ``prompt_token_count: 10013`` — virtually
the whole prompt served from cache.

**Design constraint that shaped this.** Gemini bundles ``system_instruction`` and tool
declarations into the cache, and a request using ``cached_content`` cannot then override
them. Nimbus's split-role architecture (T1-9) issues two calls with *different* system
prompts and *different* tools, so one shared cache is not possible.

The resolution is also the better design: cache the KB and use it on the **speech call
only**. The geometry call is pure visual grounding — it locates pixels, and app
documentation does not help with that. So the KB is excluded from the geometry request
entirely, which shrinks that payload as a side benefit.

``ai.py``'s existing caching comment applies unchanged and is respected here: cache
stable prefixes, never the current transcript.
"""
from __future__ import annotations

import hashlib
import threading


MIN_CACHEABLE_TOKENS = 4096
"""Below this, caching is not worth a round trip.

Creating a cache is itself an API call, and small KBs are cheap to just resend. The
figure is deliberately conservative: a max-size KB measures ~10k tokens, so this admits
roughly the top half of realistic KB sizes while skipping trivial ones.

Also a safety margin against provider minimums, which have historically existed and may
differ per model. A cache-create rejection is handled gracefully regardless."""

CACHE_TTL_SECONDS = 900
"""15 minutes. Long enough to cover a working session in one app, short enough that a
user editing their KB file sees the change without restarting Nimbus."""

_CHARS_PER_TOKEN_ESTIMATE = 4
"""Rough chars-per-token ratio used to avoid a ``count_tokens`` round trip.

Measured: 60,000 chars -> 10,002 tokens, i.e. ~6 chars/token for this content. Using 4
is deliberately pessimistic — it *over*-estimates the token count, so the threshold
admits slightly more content to caching rather than silently skipping it."""


def estimate_tokens(text: str) -> int:
    """Cheap token estimate. Avoids a network call just to decide on caching."""
    return len(text) // _CHARS_PER_TOKEN_ESTIMATE


def is_worth_caching(kb_content: str) -> bool:
    """Whether this KB payload justifies a cache round trip."""
    return bool(kb_content) and estimate_tokens(kb_content) >= MIN_CACHEABLE_TOKENS


def content_key(app_name: str, kb_content: str) -> str:
    """Stable identity for a cached payload.

    Includes a content hash, not just the app name, so editing the KB file invalidates
    the cache immediately rather than serving stale documentation for up to the TTL.
    """
    digest = hashlib.sha256(kb_content.encode("utf-8", "replace")).hexdigest()[:16]
    return f"{app_name}:{digest}"


class KBCacheManager:
    """Caches KB payloads per app, keyed by content (T1-6a).

    Every failure path degrades to ``None``, which the caller treats as "inject the KB
    inline as before". Caching is a cost optimisation; it must never be able to break an
    interaction. That mirrors how ``app.py`` already wraps ``kb.recall`` in a try/except
    because KB files are user-controlled.

    Thread-safe because the split-role design means two threads may touch this during a
    single turn.
    """

    def __init__(self, client, model_id: str, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self._client = client
        self._model_id = model_id
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, str] = {}   # content_key -> cache resource name
        self.stats = {"hits": 0, "misses": 0, "failures": 0, "skipped": 0}

    def get_or_create(
        self,
        app_name: str,
        kb_content: str,
        system_instruction: str,
    ) -> str | None:
        """Return a cache resource name, or ``None`` to inject the KB inline.

        ``None`` is returned for a payload below the threshold, for any SDK failure, and
        when caching is unsupported — all of which are normal, not errors.
        """
        if not is_worth_caching(kb_content):
            self.stats["skipped"] += 1
            return None

        key = content_key(app_name, kb_content)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                self.stats["hits"] += 1
                return existing

        name = self._create(kb_content, system_instruction)
        if name is None:
            self.stats["failures"] += 1
            return None

        with self._lock:
            # Another thread may have won the race; keep whichever landed first and let
            # the loser's cache expire naturally via its TTL.
            winner = self._entries.setdefault(key, name)
        self.stats["misses"] += 1
        return winner

    def _create(self, kb_content: str, system_instruction: str) -> str | None:
        try:
            from google.genai import types

            cache = self._client.caches.create(
                model=self._model_id,
                config=types.CreateCachedContentConfig(
                    contents=[types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=kb_content)],
                    )],
                    system_instruction=system_instruction,
                    ttl=f"{self._ttl}s",
                ),
            )
            return getattr(cache, "name", None)
        except Exception:
            # Unsupported model, payload under a provider minimum, quota, network — all
            # handled identically: fall back to inline injection.
            return None

    def invalidate_all(self) -> None:
        """Best-effort delete of every live cache. Called on shutdown.

        Caches are billed for their storage duration, so leaking them past process exit
        costs the user money for nothing.
        """
        with self._lock:
            names = list(self._entries.values())
            self._entries.clear()
        for name in names:
            try:
                self._client.caches.delete(name=name)
            except Exception:
                continue
