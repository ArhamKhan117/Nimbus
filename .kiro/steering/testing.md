# How tests are written here, and what they are for

2,030 collected tests across 45 files, 24,721 lines — roughly four fifths the volume of the
application code they guard (31,251 lines). The point is not coverage. It is that most of these tests
guard a *decision*, and a decision is the thing that gets quietly reversed.

## Conventions — match the existing suite exactly

```python
class TestParsePointTag:                        # Test<SubjectUnderTest>, grouped by unit
    """One line stating what this group guards."""

    def test_negative_x_coordinate_parses(self):
        from ai import parse_point_tag          # import INSIDE the test, not at module top
        result = parse_point_tag("here. [POINT:-3,50:save]")
        assert result.coordinate == (-3, 50)
```

- **Imports go inside the test method.** `config.py` runs `load_dotenv()` and touches the keyring at
  import; keeping that contained is what makes it mockable.
- **`mocker` from `pytest-mock` for all patching, at the *use* site.** `mocker.patch("app.resolve_setting")`,
  never the definition site.
- **Dependency injection over patching wherever a factory hook exists**: `client_factory`,
  `audio_stream_factory`, `player_factory`, `model_factory`, `overlay_factory`, `screens`,
  `cursor_pos_fn`, `connection_factory`, `mic_stream_factory`, `speaker_factory`, `listener_class`,
  `exclude`, `screen_geometry_fn`, `open_folder`, and every `*_provider` in `shell/`.
  **Any new external dependency must get a factory hook.**
- **No test touches** a real audio device, a real network call, a real `QWidget`, or the real
  `~/.nimbus/`. Use `tmp_path`; `test_memory.py` and `test_kb.py` show the pattern.
- **Extract new maths as a module-level pure function** so it is testable with no `QApplication`.
  Existing precedent: `pick_resolution`, `unscale_model_coords`, `physical_to_local_logical`,
  `annotations_to_local`, `_bezier_position`, `_waveform_bar_height`, `_spinner_angle_deg`,
  `parse_hotkey`, `parse_point_tag`, `parse_annotations`, `is_newer_version`, `clamp_size`,
  `auto_title`, `build_history`, `should_auto_new_session`, `contrast_ratio`, `accuracy_text`.
- **A regression test names its bug in the docstring.** That is why this suite is maintainable.
- **`tests/conftest.py` stays minimal.** It holds `first_run_config` and `fake_keyring` and should
  not grow: the suite's convention is self-contained tests.

## The four kinds of test here

**1. Unit tests over pure functions.** The bulk. Fast, deterministic, no Qt.

**2. Constant drift guards.** `test_overlay.py` asserts the exact bit pattern `0x080800A8` *and* the
OR expression that produces it, so a typo in one Win32 constant cannot silently break click-through.
Apply the same technique to any new bit flag or magic number.

**3. Intent guards — the ones worth having.** These fail when a *judgement call* is reversed:

| Guard | What it protects |
|---|---|
| `TestNoEmojiInTheUi` | No emoji in any UI string literal. Walks the AST, skips docstrings, narrowed to `Emoji_Presentation=Yes` so deliberate monochrome glyphs pass |
| `test_addendum_appended_not_replacing_base_prompt` | A prompt is appended to, never substituted |
| no-private-key-in-repo | The signing key never gets committed |
| no shell module imports crypto | The shell stays a view |
| `test_qss_references_no_literal_colours` | Every colour comes from `theme.py` |
| `test_every_nav_item_maps_to_a_page` | A nav item without a page is impossible by construction |
| `test_history_window_matches_the_app_constant` | Two deliberately duplicated constants cannot drift |
| `test_existing_memory_and_review_tables_untouched` | A new table is purely additive against live user databases |
| restart-label coverage | Every restart-requiring setting carries the `⟳` marker |
| `test_settings_page_has_exactly_one_scroll_area` | Save cannot fall below the fold on a 1366x768 laptop |
| `test_the_guard_would_actually_catch_one` | **A guard nobody has seen fail is a guard nobody knows works** |

That last row is the pattern to copy: every non-obvious guard should have a sibling test proving the
guard itself can fail.

**4. Contract tests across a boundary.** `web/src/lib/licence.test.ts` asserts the token format the
Python client will accept, byte for byte — sorted keys, no whitespace, no base64 padding.

## Definition of Done

An item is not done until every box is ticked:

- [ ] Pre-flight verification for the item completed — assumptions checked against live sources
- [ ] New tests written **and failing before the change, passing after**. Prove they test something
- [ ] Any updated expectation carries a comment saying *why* it changed. Without that comment a real
      regression gets laundered into a green suite
- [ ] Full suite green, **zero** regressions, and the collected count went up
- [ ] `python -m app --selftest` prints `SELFTEST OK`
- [ ] Any lazily-imported new module registered in BOTH `nimbus.spec` and `_run_selftest`
- [ ] Manual smoke test performed, if any invariant was touched
- [ ] `IMPROVEMENTS.md` / `SHELL_AND_CHAT.md` status flipped in the same commit
- [ ] `README.md` updated if behaviour changed in a user-visible way

## Manual smoke test

No automated test covers the real pipeline end to end. Five steps:

1. Launch. Tray icon appears; log shows `Listening for ctrl+alt+space...`.
2. Hold the hotkey over a browser, ask *"where's the address bar?"*, release. Expect: chime,
   waveform, spinner, speech, orange pointer flying to the target and dwelling 3 s.
3. Ask a conceptual question (*"what is HTTP?"*). Expect speech, **no pointer**.
4. Re-press mid-response. Expect the old response cancelled cleanly, no double audio.
5. Right-click tray -> Settings opens. Quit exits with no orphaned `python.exe` in Task Manager.

## Measure, do not ask

Every number in this repository came from running the thing and writing down what happened. That is
how the split-role architecture was found, how the thinking budgets were set, how `TEXT_MUTED` was
caught at 3.49:1 against a 4.5:1 requirement, and how a 55 ms capture cycle that looked deletable
turned out to be load-bearing. If a change claims an improvement, the claim needs a measurement —
`tools/bench.py` for latency, `tools/bench_grounding.py` for pointing accuracy.
