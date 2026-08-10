"""Native Gemini client for Nimbus (T1-1, T1-2, T1-7).

Lives in its own module rather than inside ``ai.py`` because the native SDK brings
its own type vocabulary (``types.Part``, ``types.Tool``, ``types.ThinkingConfig``)
that nothing else in Nimbus needs. ``ai.create_ai_client`` imports it lazily, so a
user on any other provider never pays the import cost and the frozen build stays
importable even if the SDK is absent.

Why this exists at all (T1-1): ``ai.GeminiClient`` reaches Gemini through an
OpenAI-compatibility shim, which exposes only the intersection of two API surfaces.
That intersection excludes every capability below.

What it adds over the shim:

* **T1-2 — structured geometry.** Geometry arrives as a ``function_call`` part,
  never inside the spoken text. Verified live: the spoken answer comes back free of
  brackets while ``point_at`` carries the coordinates. This makes the entire class
  of tag-leak bug fixed in T0-3 *structurally impossible* for this provider rather
  than merely guarded against.
* **T1-7 — thinking budgets.** Verified live: dropping the budget to zero moved
  time-to-first-token from 3.97s to 1.18s. That is the single largest latency lever
  measured anywhere in Nimbus.
* **T1-5 — Search grounding**, opt-in.
* **T1-3 — Agentic Vision**, opt-in.

Coordinate contract: the SDK returns values normalised to 0..1000 with **y first**
(``point`` is ``[y, x]``; ``box_2d`` is ``[ymin, xmin, ymax, xmax]``). All conversion
to Space C goes through ``ai.normalised_point_to_space_c`` /
``ai.normalised_box_to_space_c``, which take y and x as separate named arguments
precisely so the ordering cannot be silently transposed. No fourth coordinate space
is introduced.

Testability mirrors ``stt.py`` and ``realtime.py``: the SDK client is injectable via
``client_factory``, so the whole class is unit-testable with no network and no key.
"""
from __future__ import annotations

import threading
from io import BytesIO
from typing import Callable, Iterator

from PIL import Image

from ai import (
    _NIMBUS_ANNOTATION_SYSTEM_PROMPT,
    _NIMBUS_STRUCTURED_ANNOTATION_PROMPT,
    _NIMBUS_STRUCTURED_SYSTEM_PROMPT,
    _NIMBUS_SYSTEM_PROMPT,
    AIClient,
    HISTORY_IMAGE_MEDIA_TYPE,
    PointParseResult,
    classify_query,
    iter_history_blocks,
    normalised_box_to_space_c,
    normalised_point_to_space_c,
    strip_non_speech,
)


# Budget per query class (T1-7). Verified live on gemini-3-flash-preview.
#
# `pro` models REJECT a zero budget outright ("Budget 0 is invalid"), so the
# per-model floor in _clamp_thinking_budget is load-bearing, not defensive.
THINKING_BUDGET_BY_CLASS: dict[str, int] = {
    "locate": 0,        # pure grounding; reasoning adds latency, not accuracy
    "conceptual": 512,  # explanation, no spatial work
    "diagnostic": 4096,  # genuine multi-step reasoning
}

_MIN_BUDGET_MODELS = ("pro",)
"""Model-name fragments whose models refuse a zero thinking budget."""

_PRO_MIN_BUDGET = 128
"""Smallest budget a `pro` model accepts. Used instead of 0 for those models."""

_AGENTIC_THINKING_BUDGET = 2048
"""Thinking budget for the geometry call when Agentic Vision is enabled (T1-3).

The geometry call normally runs at the minimum budget because locating a control is
perception, not reasoning. Agentic Vision inverts that: the model is asked to inspect,
compare candidates, and verify boundaries before answering, which is reasoning and needs
room to happen. This is the latency cost of the feature, and the reason it is opt-in."""


def _clamp_thinking_budget(budget: int, model_id: str) -> int:
    """Raise a zero budget to the model's floor where zero is rejected (T1-7).

    Verified live: ``gemini-3.1-pro-preview`` returns 400 "Budget 0 is invalid"
    while ``gemini-3-flash-preview`` accepts 0 happily. Silently clamping keeps the
    latency optimisation safe to enable across every model.
    """
    if budget > 0:
        return budget
    if any(fragment in model_id.lower() for fragment in _MIN_BUDGET_MODELS):
        return _PRO_MIN_BUDGET
    return 0


class GeminiNativeClient(AIClient):
    """Gemini via the native ``google-genai`` SDK.

    Public surface is byte-identical to every other ``AIClient``:
    ``ask_stream(...)`` returns a context manager exposing ``.text_deltas()`` and
    ``.final_result() -> PointParseResult``. ``app.py::_pipeline_worker`` cannot tell
    which client is behind it, which is the property that made this a one-class
    addition rather than a refactor.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str,
        client_factory: Callable[..., object] | None = None,
        enable_search_grounding: bool = False,
        enable_agentic_vision: bool = False,
        enable_code_execution: bool = False,
        enable_kb_cache: bool = True,
        vertex_project: str = "",
        vertex_location: str = "global",
    ) -> None:
        # Accept a namespaced slug ("google/gemini-3-flash-preview") for parity with
        # the OpenRouter path, but the native API wants the bare model name.
        self.model_id = model_id.split("/", 1)[1] if "/" in model_id else model_id
        self._api_key = api_key
        self._vertex_project = vertex_project
        self._vertex_location = vertex_location or "global"
        self._client_factory = client_factory
        self._enable_search_grounding = enable_search_grounding
        self._enable_agentic_vision = enable_agentic_vision
        self._enable_code_execution = enable_code_execution
        self._enable_kb_cache = enable_kb_cache
        self._client = None
        self._kb_cache = None
        self.last_citations: list[dict] = []
        """T1-5: citations from the most recent turn.

        Read by ``app.py`` for the debug log and memory record. Deliberately NOT part
        of ``spoken_text``: reading URLs aloud would violate the write-for-the-ear
        contract in the system prompt."""
        self.last_search_queries: list[str] = []
        """T1-5: queries the model issued, when reported. Diagnostic only."""

    # -- capability flags -----------------------------------------------------

    def supports_structured_geometry(self) -> bool:
        return True

    def supports_thinking_budget(self) -> bool:
        return True

    # -- lazy SDK client ------------------------------------------------------

    def uses_vertex(self) -> bool:
        """Whether this client talks to Vertex AI rather than the Gemini API.

        Read by the Settings and Account pages so a user can see which backend is
        live. "Which Google endpoint am I billing against" is not a question anyone
        should have to answer by reading configuration.
        """
        return bool(self._vertex_project)

    def _get_client(self):
        """Build the SDK client on first use so construction stays cheap and
        offline. Injectable for tests.

        One SDK, two backends. With a Google Cloud project configured this is Vertex
        AI, authenticated by Application Default Credentials, so **no key is passed and
        none is held** — the credential belongs to the machine or the service account.
        Without one it is the Gemini API with an AI Studio key, which stays the
        zero-configuration path for an individual.

        Everything downstream is unchanged either way: the tool declarations, the
        thinking budgets, the split speech and geometry calls, the explicit cache. That
        equivalence is the whole reason the backend is a setting and not a fork.
        """
        if self._client is None:
            if self._client_factory is not None:
                # The Vertex kwargs are passed ONLY when Vertex is configured. Existing
                # injected factories are declared as ``lambda api_key=None: ...``, so
                # widening the call unconditionally would break every test that predates
                # this backend rather than any real behaviour.
                if self._vertex_project:
                    self._client = self._client_factory(
                        api_key=self._api_key,
                        vertex_project=self._vertex_project,
                        vertex_location=self._vertex_location,
                    )
                else:
                    self._client = self._client_factory(api_key=self._api_key)
            else:
                from google import genai
                if self._vertex_project:
                    self._client = genai.Client(
                        vertexai=True,
                        project=self._vertex_project,
                        location=self._vertex_location,
                    )
                else:
                    self._client = genai.Client(api_key=self._api_key)
        return self._client

    # -- tool declarations ----------------------------------------------------

    def supports_agentic_refinement(self) -> bool:
        """Whether this client can refine its own grounding without a second call (T1-3).

        ``app.py`` checks this before running ``refine_point_via_crop``: when the model
        inspects the image itself there is nothing left for the crop pass to add, and
        running both would pay for two refinements.
        """
        return self._enable_agentic_vision

    @staticmethod
    def _agentic_instruction() -> str:
        """Extra grounding guidance for the geometry call when Agentic Vision is on (T1-3).

        Replaces Nimbus's own crop-and-recheck pass: rather than the app cropping a
        900px window around a first guess and asking again, the model is told to do that
        inspection itself before committing to coordinates.

        Kept separate from the speech prompt because it is pure grounding instruction —
        the user never hears any of it.
        """
        return (
            "\n\nlocating precisely: before you call the pointing function, examine the "
            "screenshot carefully. zoom in mentally on the region you believe contains "
            "the target and check the exact pixel boundaries of the control itself, not "
            "its label or the whitespace around it. small icons and toolbar buttons are "
            "easy to miss by tens of pixels. if two controls look similar, compare them "
            "before choosing. report the centre of the control."
        )

    @staticmethod
    def _build_tools(annotation_mode: bool):
        """Declare the geometry tools (T1-2).

        ``y`` and ``x`` are separate named integer fields rather than an array, so
        the model cannot transpose them and the wire format is self-documenting.
        This is the whole reason geometry never touches the speech channel.
        """
        from google.genai import types

        point_at = types.FunctionDeclaration(
            name="point_at",
            description=(
                "Point the on-screen cursor at a UI element the user asked about "
                "(button, menu item, link, field, icon). Call this whenever pointing "
                "would genuinely help. Do NOT call it for purely conceptual questions."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "y": types.Schema(
                        type=types.Type.INTEGER,
                        description="Vertical position normalised 0-1000, top to bottom.",
                    ),
                    "x": types.Schema(
                        type=types.Type.INTEGER,
                        description="Horizontal position normalised 0-1000, left to right.",
                    ),
                    "label": types.Schema(
                        type=types.Type.STRING,
                        description="Short 1-3 word name of the element.",
                    ),
                },
                required=["y", "x", "label"],
            ),
        )
        declarations = [point_at]

        if annotation_mode:
            def _box_schema(description: str, extra: dict | None = None):
                """Shared [ymin, xmin, ymax, xmax] schema (T3-5).

                draw_box and highlight_region take an identical box; declaring it once
                keeps the y-first note in a single place, since a transposition here is
                silent and systematic.
                """
                properties = {
                    "box_2d": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.INTEGER),
                        description=(
                            "[ymin, xmin, ymax, xmax] normalised 0-1000. "
                            "Note y comes first."
                        ),
                    ),
                    "label": types.Schema(
                        type=types.Type.STRING,
                        description="Short 1-3 word name of the element.",
                    ),
                }
                properties.update(extra or {})
                return types.Schema(
                    type=types.Type.OBJECT,
                    properties=properties,
                    required=["box_2d", "label"],
                )

            declarations.append(types.FunctionDeclaration(
                name="draw_box",
                description=(
                    "Draw a rectangle framing a UI element, for teaching mode. Use "
                    "instead of point_at when outlining the element is clearer -- "
                    "prefer it for anything box-shaped such as a button, text field "
                    "or menu item, where a circle would clip it or cover its neighbours."
                ),
                parameters=_box_schema("framing box"),
            ))
            # T3-5. Both ride the same geometry call, so they cost no extra request.
            declarations.append(types.FunctionDeclaration(
                name="highlight_region",
                description=(
                    "Dim the entire screen EXCEPT this region, to make one area "
                    "unmissable on a busy screen. Call at most ONCE per reply: two "
                    "competing dimmed layers cancel each other out visually."
                ),
                parameters=_box_schema("region to keep bright"),
            ))
            declarations.append(types.FunctionDeclaration(
                name="mark_step",
                description=(
                    "Place a numbered badge at a point, for an answer with an order "
                    "('first here, then here'). Call once per step, numbering from 1 in "
                    "the order you describe them."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "y": types.Schema(
                            type=types.Type.INTEGER,
                            description="Vertical position normalised 0-1000.",
                        ),
                        "x": types.Schema(
                            type=types.Type.INTEGER,
                            description="Horizontal position normalised 0-1000.",
                        ),
                        "n": types.Schema(
                            type=types.Type.INTEGER,
                            description="Step number, starting at 1.",
                        ),
                        "label": types.Schema(
                            type=types.Type.STRING,
                            description="Short 1-3 word description of the step.",
                        ),
                    },
                    required=["y", "x", "n", "label"],
                ),
            ))
        return [types.Tool(function_declarations=declarations)]

    # -- request assembly -----------------------------------------------------

    def _build_config(
        self,
        system_prompt: str,
        max_tokens: int,
        transcript: str,
        annotation_mode: bool,
        with_tools: bool,
        force_minimal_budget: bool = False,
        cached_content: str | None = None,
    ):
        """Assemble a request config.

        ``with_tools`` is the split-role switch (T1-9): the speech call must declare
        NO tools, because declaring them suppresses prose entirely.

        ``force_minimal_budget`` applies to the geometry call — locating a UI element
        is pure perception, so reasoning tokens there are latency with no accuracy
        return.
        """
        from google.genai import types

        if force_minimal_budget:
            budget = _clamp_thinking_budget(0, self.model_id)
        else:
            budget = _clamp_thinking_budget(
                THINKING_BUDGET_BY_CLASS.get(classify_query(transcript), 512),
                self.model_id,
            )

        kwargs: dict = {
            "system_instruction": system_prompt,
            "max_output_tokens": max_tokens,
            "thinking_config": types.ThinkingConfig(thinking_budget=budget),
        }

        if with_tools:
            kwargs["tools"] = self._build_tools(annotation_mode)
            if self._enable_agentic_vision:
                # T1-3: applied to the GEOMETRY call only. Also lift the minimal budget,
                # because self-inspection is exactly the reasoning we normally suppress
                # for speed — with zero budget there is no room to do it.
                kwargs["system_instruction"] = (
                    system_prompt + self._agentic_instruction()
                )
                kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=_clamp_thinking_budget(
                        _AGENTIC_THINKING_BUDGET, self.model_id,
                    )
                )
        else:
            # T1-5 / T1-6b. Both ride the SPEECH call: grounding informs the words and
            # code execution produces the answer. Kept off the geometry call so neither
            # search nor sandbox latency can delay the pointer.
            speech_tools = []
            if self._enable_search_grounding:
                speech_tools.append(types.Tool(google_search=types.GoogleSearch()))
            if self._enable_code_execution:
                speech_tools.append(types.Tool(code_execution=types.ToolCodeExecution()))
            if speech_tools:
                kwargs["tools"] = speech_tools

        if cached_content is not None:
            # T1-6a. The cache carries system_instruction, so passing it again is both
            # redundant and rejected by the API.
            kwargs.pop("system_instruction", None)
            kwargs["cached_content"] = cached_content

        return types.GenerateContentConfig(**kwargs)

    def _resolve_kb_cache(
        self, kb_content: str, kb_app_name: str, system_prompt: str,
    ) -> str | None:
        """Return a cache resource name for the KB, or ``None`` to inject inline (T1-6a).

        Lazily builds the manager so a user with no KB never constructs one.
        """
        if not (self._enable_kb_cache and kb_content):
            return None
        from gemini_cache import KBCacheManager

        if self._kb_cache is None:
            self._kb_cache = KBCacheManager(self._get_client(), self.model_id)
        return self._kb_cache.get_or_create(
            app_name=kb_app_name or "unknown",
            kb_content=kb_content,
            system_instruction=system_prompt,
        )

    def close(self) -> None:
        """Release billed cache resources. Called from ``app.py``'s shutdown path."""
        if self._kb_cache is not None:
            self._kb_cache.invalidate_all()

    def _build_contents(
        self,
        images: list[tuple[Image.Image, str]],
        transcript: str,
        history: list[dict],
        kb_content: str,
        kb_app_name: str,
    ) -> list:
        """Convert Nimbus's provider-neutral message shape to native ``Content``.

        History arrives in Anthropic content-block form. Text blocks are
        concatenated and image blocks dropped, matching ``GeminiClient``'s existing
        behaviour. Empty turns are skipped because the API rejects empty content —
        the same guard ``GeminiClient`` and ``OllamaClient`` already carry.
        """
        from google.genai import types

        contents: list = []
        for turn in history:
            # T2-4: history screenshots become image Parts rather than being dropped.
            text_parts: list[str] = []
            image_data: list[str] = []
            for kind, payload in iter_history_blocks(turn):
                (text_parts if kind == "text" else image_data).append(payload)
            if not any(p.strip() for p in text_parts):
                continue
            turn_parts = [types.Part.from_text(text=" ".join(text_parts))]
            # Only a user turn may carry an image: the model did not send one, and
            # attaching one to a "model" turn is rejected.
            if image_data and turn.get("role") != "assistant":
                import base64
                for data in image_data:
                    try:
                        turn_parts.append(types.Part.from_bytes(
                            data=base64.b64decode(data),
                            mime_type=HISTORY_IMAGE_MEDIA_TYPE,
                        ))
                    except Exception:
                        # A corrupt history image must not fail a live request.
                        continue
            contents.append(types.Content(
                # Gemini's assistant role is "model", not "assistant".
                role="model" if turn.get("role") == "assistant" else "user",
                parts=turn_parts,
            ))

        parts: list = []
        for img, label in images:
            buf = BytesIO()
            img.save(buf, "JPEG", quality=85)
            parts.append(types.Part.from_bytes(
                data=buf.getvalue(), mime_type="image/jpeg",
            ))
            parts.append(types.Part.from_text(text=label))

        if kb_content:
            display = kb_app_name.removesuffix(".exe") or "this software"
            parts.append(types.Part.from_text(text=(
                f"app knowledge base:\nyou are helping the user with {display}. "
                f"treat this documentation as authoritative:\n\n{kb_content}"
            )))

        parts.append(types.Part.from_text(text=transcript))
        contents.append(types.Content(role="user", parts=parts))
        return contents

    # -- public API -----------------------------------------------------------

    def ask_stream(
        self,
        images: list[tuple[Image.Image, str]],
        transcript: str,
        history: list[dict],
        system_prompt: str = _NIMBUS_SYSTEM_PROMPT,
        max_tokens: int = 1024,
        kb_content: str = "",
        kb_app_name: str = "",
        annotation_mode: bool = False,
    ):
        """Open a streaming native Gemini call.

        Same contract as every other provider. ``annotation_mode`` additionally
        declares the ``draw_box`` tool; it defaults to ``False`` so existing callers
        are unaffected.

        **Split-role architecture (T1-9), forced by a measured API property.**
        Gemini returns *either* prose *or* a function call in a turn — never both.
        Measured across thinking budgets 0, 64, 128, 256 and 512: whenever the model
        chose to call ``point_at`` it emitted zero text, so a single tool-enabled call
        produced a pointer with total silence. Silence is a correctness failure, not a
        cosmetic one: the user held a hotkey and asked a question.

        So the two roles are split onto two calls that run **concurrently**:

        * **speech call** — no tools declared, so the model must answer in prose.
          Streams straight to sentence-level TTS, preserving Nimbus's largest
          latency win.
        * **geometry call** — tools only, minimal thinking budget, launched on a
          daemon thread and harvested in ``final_result()``.

        Wall-clock is therefore ``max(speech, geometry)``, not their sum — the same
        overlapping trick ``_release_capture_worker`` already uses. Geometry resolves
        while the first sentence is still playing, which is exactly the latency
        headroom the pointer needs since the cursor flies *after* speech begins.

        The geometry call is skipped entirely for conceptual questions, so a
        "what is HTTP" turn costs exactly one request.
        """
        target_w, target_h = self._infer_target_dims(images)
        speech_prompt = self._select_system_prompt(system_prompt, annotation_mode)
        self.last_citations = []

        # T1-6a: when the KB is cached, it must NOT also be inlined or it is paid for
        # twice — once in the cache and once in the request body.
        kb_cache_name = self._resolve_kb_cache(kb_content, kb_app_name, speech_prompt)
        speech_contents = self._build_contents(
            images, transcript, history,
            "" if kb_cache_name else kb_content, kb_app_name,
        )
        # The geometry call never receives the KB at all: locating a pixel is visual, and
        # app documentation cannot help with it. Smaller payload, faster pointer.
        geometry_contents = self._build_contents(
            images, transcript, history, "", "",
        )

        # A custom prompt (e.g. locator.py's crop-refinement prompt) explicitly wants
        # a [POINT] tag in TEXT, so it must not be split — tools would suppress the
        # very text it needs.
        is_nimbus_prompt = self._is_structured_nimbus_prompt(speech_prompt)
        # Annotation mode ALWAYS attempts geometry: the user explicitly turned on
        # draw-on-screen teaching, so drawing is the point of the turn. Without this,
        # "circle the search bar" classifies as conceptual (it contains no directional
        # word) and silently produces no annotation at all.
        wants_geometry = is_nimbus_prompt and (
            annotation_mode or classify_query(transcript) != "conceptual"
        )

        try:
            speech_stream = self._get_client().models.generate_content_stream(
                model=self.model_id,
                contents=speech_contents,
                # No tools on the speech call: their presence is what silences prose.
                config=self._build_config(
                    speech_prompt, max_tokens, transcript,
                    annotation_mode=annotation_mode, with_tools=False,
                    cached_content=kb_cache_name,
                ),
            )
        except Exception as exc:
            raise RuntimeError(
                f"Gemini native request failed (model={self.model_id!r}). "
                "Checklist:\n"
                "  1. Is GEMINI_API_KEY valid? Get one at https://aistudio.google.com/apikey\n"
                f"  2. Does your key have access to {self.model_id!r}? Try "
                "GEMINI_NATIVE_MODEL=gemini-3-flash-preview\n"
                "  3. Is your internet connection up?\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc

        geometry_worker = None
        if wants_geometry:
            geometry_worker = _GeometryWorker(
                client=self._get_client(),
                model_id=self.model_id,
                contents=geometry_contents,
                config=self._build_config(
                    speech_prompt, max_tokens, transcript,
                    annotation_mode=annotation_mode, with_tools=True,
                    force_minimal_budget=True,
                ),
            )
            geometry_worker.start()

        return _GeminiNativeStreamingResponse(
            speech_stream, target_w, target_h, geometry_worker, owner=self,
        )

    @staticmethod
    def _select_system_prompt(requested: str, annotation_mode: bool) -> str:
        """Swap a tag-based prompt for its structured equivalent (T1-2).

        Callers pass whichever prompt they already used, so this stays a drop-in
        provider. But a tag-based prompt actively breaks the structured path: the
        live test showed the model obeys the prompt's "append [POINT:x,y]"
        instruction rather than calling the tool, putting coordinates straight back
        into the speech channel.

        A CUSTOM prompt (neither Nimbus default — for example ``locator.py``'s
        refinement prompt, which genuinely wants a ``[POINT]`` tag back) is passed
        through untouched, so the crop-verification path keeps working.

        **Prefix matching, not equality (T2-5).** A Nimbus prompt may carry an appended
        per-app addendum ("this is a code editor…"). Matching on equality treated such a
        prompt as fully custom, which silently disabled structured geometry *and* the
        geometry call itself — Code Mode would have stopped Nimbus pointing at anything on
        this provider. So the base is swapped while any suffix is preserved.
        """
        for base, structured in (
            # Annotation base first: in annotation mode it is the more specific match.
            (_NIMBUS_ANNOTATION_SYSTEM_PROMPT, _NIMBUS_STRUCTURED_ANNOTATION_PROMPT),
            (_NIMBUS_SYSTEM_PROMPT,
             _NIMBUS_STRUCTURED_ANNOTATION_PROMPT if annotation_mode
             else _NIMBUS_STRUCTURED_SYSTEM_PROMPT),
        ):
            if requested.startswith(base):
                return structured + requested[len(base):]
        return requested

    @staticmethod
    def _is_structured_nimbus_prompt(prompt: str) -> bool:
        """Whether ``prompt`` is a Nimbus structured prompt, addendum or not (T2-5).

        Separate from ``_select_system_prompt`` so the geometry decision and the prompt
        swap cannot disagree about what counts as "ours" — they previously did, via two
        independent equality checks.
        """
        return prompt.startswith((
            _NIMBUS_STRUCTURED_SYSTEM_PROMPT, _NIMBUS_STRUCTURED_ANNOTATION_PROMPT,
        ))

    @staticmethod
    def _infer_target_dims(images: list[tuple[Image.Image, str]]) -> tuple[int, int]:
        """Space C dimensions, taken from the first (cursor-screen) image.

        ``capture_all_screens`` sorts the cursor screen first, and normalised
        coordinates are relative to the image the model was pointing at.
        """
        if not images:
            return (1, 1)
        first = images[0][0]
        return (first.width, first.height)

    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Batch wrapper, for parity with the other clients."""
        label = (
            f"primary focus (image dimensions: {declared_w}x{declared_h} pixels)"
        )
        with self.ask_stream([(image, label)], transcript, history) as stream:
            for _ in stream.text_deltas():
                pass
            result = stream.final_result()
        points = []
        if result.coordinate:
            x, y = result.coordinate
            points.append({"x": x, "y": y, "label": result.element_label or ""})
        return {"text": result.spoken_text, "points": points}


class _GeometryWorker:
    """Runs the tools-only geometry call on a daemon thread (T1-9).

    Concurrent with the speech call so wall-clock is ``max()`` rather than the sum.
    Every failure is captured rather than raised: losing the pointer degrades the
    answer, whereas failing the interaction loses it entirely. The speech call is
    the load-bearing half and must never depend on this one succeeding.
    """

    def __init__(self, client, model_id: str, contents, config) -> None:
        self._client = client
        self._model_id = model_id
        self._contents = contents
        self._config = config
        self._calls: list[tuple[str, dict]] = []
        self._error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="nimbus-gemini-geometry",
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            response = self._client.models.generate_content(
                model=self._model_id, contents=self._contents, config=self._config,
            )
            for candidate in (getattr(response, "candidates", None) or []):
                content = getattr(candidate, "content", None)
                if content is None:
                    continue
                for part in (getattr(content, "parts", None) or []):
                    call = getattr(part, "function_call", None)
                    if call is not None and getattr(call, "name", None):
                        self._calls.append((call.name, dict(call.args or {})))
        except Exception as exc:  # never propagate into the speech path
            self._error = exc

    def result(self, timeout: float = 8.0) -> list[tuple[str, dict]]:
        """Join and return harvested calls. Empty on timeout or failure.

        The timeout exists so a hung geometry request cannot stall the pipeline: the
        user already heard their answer, and an absent pointer is far better than a
        blocked worker thread.
        """
        self._thread.join(timeout=timeout)
        return list(self._calls)

    @property
    def error(self) -> Exception | None:
        return self._error


class _GeminiNativeStreamingResponse:
    """Adapts the native streaming iterator to Nimbus's streaming contract.

    Keeps the two channels apart: ``text`` parts accumulate into the spoken answer,
    ``function_call`` parts into geometry. Because they are distinct part types (and,
    under the split-role design, distinct *requests*), a coordinate can never reach
    ``spoken_text`` — the guarantee ``parse_point_tag`` needs three regexes to
    enforce for text-based providers.
    """

    def __init__(
        self,
        stream,
        target_w: int,
        target_h: int,
        geometry_worker: "_GeometryWorker | None" = None,
        owner: "GeminiNativeClient | None" = None,
    ) -> None:
        self._stream = stream
        self._target_w = target_w
        self._target_h = target_h
        self._geometry_worker = geometry_worker
        self._owner = owner
        self._accumulated = ""
        self._calls: list[tuple[str, dict]] = []
        self._citations: list[dict] = []
        self._search_queries: list[str] = []
        self._deltas_exhausted = False
        self._geometry_collected = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        close = getattr(self._stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        return False  # never swallow exceptions

    def text_deltas(self) -> Iterator[str]:
        """Yield spoken-text deltas, harvesting geometry as a side effect.

        The ``finally`` mirrors ``_OllamaStreamingResponse``: the exhausted flag must
        be set even when the transport raises mid-stream, or ``final_result()`` would
        re-enter this generator and re-raise the same error instead of returning
        whatever was accumulated.
        """
        try:
            for chunk in self._stream:
                self._harvest_citations(chunk)
                for part in self._iter_parts(chunk):
                    text = getattr(part, "text", None)
                    if text:
                        self._accumulated += text
                        yield text
                    call = getattr(part, "function_call", None)
                    if call is not None and getattr(call, "name", None):
                        self._calls.append((call.name, dict(call.args or {})))
                    # Code-execution parts (executable_code, code_execution_result) are
                    # deliberately ignored: T1-6b wants the model's PROSE conclusion, not
                    # the sandbox transcript, which would be unspeakable read aloud.
        finally:
            self._deltas_exhausted = True

    def _harvest_citations(self, chunk) -> None:
        """Collect grounding citations from a chunk (T1-5).

        Verified shape: ``candidate.grounding_metadata.grounding_chunks[].web.{title,uri}``.
        Stored for the debug log and memory record, never for speech.
        """
        for candidate in (getattr(chunk, "candidates", None) or []):
            meta = getattr(candidate, "grounding_metadata", None)
            if meta is None:
                continue
            for entry in (getattr(meta, "grounding_chunks", None) or []):
                web = getattr(entry, "web", None)
                if web is None:
                    continue
                citation = {
                    "title": getattr(web, "title", None) or "",
                    "uri": str(getattr(web, "uri", "") or ""),
                }
                if citation["uri"] and citation not in self._citations:
                    self._citations.append(citation)
            # Measured limitation: a strong persona system_instruction suppresses
            # grounding_chunks even when the search demonstrably ran and improved the
            # answer. Capturing the queries means the debug log can still show THAT
            # search happened, which is the difference between "grounding is broken" and
            # "grounding worked but returned no attribution".
            for query in (getattr(meta, "web_search_queries", None) or []):
                text = str(query)
                if text and text not in self._search_queries:
                    self._search_queries.append(text)

    def citations(self) -> list[dict]:
        """Grounding citations for this turn: ``[{"title": ..., "uri": ...}]``."""
        return list(self._citations)

    def search_queries(self) -> list[str]:
        """Queries the model actually issued, when the provider reports them."""
        return list(self._search_queries)

    @staticmethod
    def _iter_parts(chunk):
        """Yield content parts from one chunk, tolerating empty candidates.

        A chunk can legitimately carry no candidate (for example a safety or usage
        frame), so every level is guarded rather than assumed.
        """
        candidates = getattr(chunk, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if content is None:
                continue
            for part in (getattr(content, "parts", None) or []):
                yield part

    def final_result(self) -> PointParseResult:
        """Return the spoken text plus geometry recovered from the tool channel.

        ``spoken_text`` is used verbatim: no tag stripping is applied or needed,
        because geometry never entered it. ``malformed_tags`` stays empty for the
        same reason.
        """
        if not self._deltas_exhausted:
            try:
                for _ in self.text_deltas():
                    pass
            except Exception:
                pass
        self._collect_geometry()
        if self._owner is not None:
            # Publish citations where app.py can reach them without threading a new
            # return value through the provider-neutral PointParseResult contract.
            self._owner.last_citations = list(self._citations)
            self._owner.last_search_queries = list(self._search_queries)

        coordinate: tuple[int, int] | None = None
        label: str | None = None
        for name, args in self._calls:
            if name == "point_at" and coordinate is None:
                point = self._point_from_args(args)
                if point is not None:
                    coordinate, label = point, (args.get("label") or None)
            elif name == "draw_box" and coordinate is None:
                rect = normalised_box_to_space_c(
                    args.get("box_2d"), self._target_w, self._target_h,
                )
                if rect is not None:
                    coordinate, label = rect.center, (args.get("label") or None)

        if coordinate is not None:
            # Tool channel won. Geometry never entered the speech text, so there is no
            # tag to strip — but strip_non_speech still runs, because a model told
            # about tools may write the call it *would* have made as a markdown block.
            return PointParseResult(
                spoken_text=strip_non_speech(self._accumulated),
                coordinate=coordinate,
                element_label=label,
                screen_number=None,
                malformed_tags=(),
            )

        # No usable tool call. Fall back to text-tag parsing, which covers two real
        # cases: `locator.refine_point_via_crop` deliberately passes a custom prompt
        # asking for a [POINT] tag rather than a tool call, and a model may
        # occasionally answer in text despite the tools being declared. Routing
        # through parse_point_tag also means the T0-3 fail-closed stripping still
        # protects the spoken text, so a stray tag can never be read aloud.
        from ai import parse_point_tag

        parsed = parse_point_tag(self._accumulated)
        coordinate = parsed.coordinate
        if coordinate is not None:
            # Gemini emits NORMALISED 0-1000 coordinates in a [POINT] tag too, not
            # pixels -- even when the prompt explicitly states the pixel dimensions and
            # asks for pixels. This code previously assumed the opposite, and the wrong
            # assumption was the cause of a real accuracy regression.
            #
            # Measured directly (see IMPROVEMENTS.md §11.3). A dead-centre target on
            # 900x900, 1920x1080, 600x400 and 400x1200 images returned `[POINT:500,500]`
            # every time; pixels would have been (450,450), (960,540), (300,200) and
            # (200,600) respectively. The convention is trained in, and the prompt does
            # not override it.
            #
            # Consuming those values as pixels inflated every refined point by
            # `dimension / 1000`, so on the 900px refinement crop a pixel-perfect seed
            # was pushed ~50px off target and OUTSIDE small icons -- refinement made
            # pointing worse instead of better, which is exactly what it exists to
            # prevent. Only this provider was affected: the other clients' models do
            # return pixels here.
            coordinate = normalised_point_to_space_c(
                norm_y=coordinate[1],
                norm_x=coordinate[0],
                target_w=self._target_w,
                target_h=self._target_h,
            )
        return PointParseResult(
            spoken_text=strip_non_speech(parsed.spoken_text),
            coordinate=coordinate,
            element_label=parsed.element_label,
            screen_number=parsed.screen_number,
            malformed_tags=parsed.malformed_tags,
        )

    def _point_from_args(self, args: dict) -> tuple[int, int] | None:
        """Convert normalised tool args to Space C, tolerating bad input.

        A malformed tool call is dropped rather than raised: the spoken answer is
        already correct and useful, so losing the pointer is strictly better than
        failing the interaction.
        """
        try:
            return normalised_point_to_space_c(
                norm_y=int(args["y"]),
                norm_x=int(args["x"]),
                target_w=self._target_w,
                target_h=self._target_h,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _collect_geometry(self) -> None:
        """Join the concurrent geometry call once, merging its results (T1-9).

        Idempotent because both ``final_result()`` and ``geometry()`` may be called,
        in either order, and the worker must only be joined once.
        """
        if self._geometry_collected or self._geometry_worker is None:
            self._geometry_collected = True
            return
        self._geometry_collected = True
        self._calls.extend(self._geometry_worker.result())

    def geometry(self) -> list:
        """Structured annotation shapes in Space C, for teaching mode (T1-2).

        Returns ``annotations.Rect`` / ``Circle`` objects the overlay can render
        directly. Separate from ``final_result()`` so the pointer path and the
        annotation path stay independent.
        """
        from annotations import Circle, Highlight, StepBadge

        self._collect_geometry()
        shapes: list = []
        for name, args in self._calls:
            if name == "draw_box":
                rect = normalised_box_to_space_c(
                    args.get("box_2d"), self._target_w, self._target_h,
                )
                if rect is not None:
                    shapes.append(rect.__class__(
                        rect.x, rect.y, rect.w, rect.h, args.get("label") or "",
                    ))
            # T3-5. Reuses the same box conversion, so the y-first contract has exactly
            # one implementation for both shapes.
            elif name == "highlight_region":
                rect = normalised_box_to_space_c(
                    args.get("box_2d"), self._target_w, self._target_h,
                )
                if rect is not None:
                    shapes.append(Highlight(
                        rect.x, rect.y, rect.w, rect.h, args.get("label") or "",
                    ))
            elif name == "mark_step":
                point = self._point_from_args(args)
                if point is not None:
                    try:
                        number = int(args.get("n", 0))
                    except (TypeError, ValueError):
                        continue
                    if number > 0:
                        shapes.append(StepBadge(
                            point[0], point[1], number, args.get("label") or "",
                        ))
            elif name == "point_at":
                point = self._point_from_args(args)
                if point is not None:
                    # A bare point becomes a small circle so it is visible as an
                    # annotation; the pointer path uses the coordinate directly.
                    shapes.append(Circle(point[0], point[1], 28, args.get("label") or ""))
        return shapes
