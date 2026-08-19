# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Nimbus.

Key points:
  - Entry point is ``app.py`` at repo root.
  - Qt binding is PyQt6, not PySide6 — replaced ``collect_data_files``
    target + all hidden-import names.
  - Dropped ``qasync`` (we don't use async-Qt bridge).
  - Added ``anthropic``, ``openai``, ``cartesia``, ``assemblyai``
    explicit hidden imports for SDK dependencies.
  - Output bundle named ``Nimbus`` (so ``dist/Nimbus/Nimbus.exe``).

Build:
    py -3.13 -m PyInstaller nimbus.spec --noconfirm

Output: ``dist/Nimbus/`` containing ``Nimbus.exe`` plus all bundled
DLLs/Python stdlib/site-packages. Inno Setup wraps this folder into
``Nimbus-Windows-Setup.exe`` (see ``installer/nimbus.iss``).

Build tooling installed via pip:
    pip install pyinstaller>=6.20

Inno Setup (separate install — not a Python dep):
    https://jrsoftware.org/isdl.php  (free, ~3MB)
"""
import glob
import os

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


# Qt 6 plugins required at runtime — the platform shim DLL (windows.dll
# under plugins/platforms/) is what makes PyQt6 actually render on
# Windows. Without it, the app crashes at QApplication construction.
pyqt6_data = collect_data_files(
    "PyQt6",
    includes=[
        "Qt6/plugins/platforms/**",
        "Qt6/plugins/imageformats/**",
        "Qt6/plugins/multimedia/**",
        "Qt6/plugins/styles/**",
    ],
)
pyqt6_libs = collect_dynamic_libs("PyQt6")

# collect_all the local-provider stack so the frozen EXE includes their
# native libs + data files, not just the Python modules. listed these in
# hiddenimports but PyInstaller did not recurse into faster-whisper's own imports,
# so `av` (PyAV, the audio decoder) was missing and local STT crashed on launch.
_local_datas, _local_bins, _local_hidden = [], [], []
for _pkg in (
    "faster_whisper", "ctranslate2", "onnxruntime",
    "kokoro_onnx", "soundfile", "tokenizers",
    # Kokoro TTS grapheme->phoneme: espeakng_loader ships espeak-ng.dll +
    # ~15MB espeak-ng-data; phonemizer-fork drives it. Both resolve their
    # paths via __file__ so collect_all (in-package data) bundles them safely.
    "espeakng_loader", "phonemizer",
    # phonemizer-fork imports its `segments` backend at module load (even
    # though Kokoro only uses espeak), which drags in segments -> csvw ->
    # jsonschema. Each needs its bundled data or the frozen import crashes.
    # Verified end-to-end in a frozen test EXE (synth + transcribe). The
    # rfc3987_syntax/lark URI checker that jsonschema *optionally* pulls is
    # NOT needed and is excluded below so jsonschema skips it cleanly.
    "segments", "csvw", "language_tags",
    "jsonschema", "jsonschema_specifications", "referencing",
):
    try:
        _d, _b, _h = collect_all(_pkg)
        _local_datas += _d
        _local_bins += _b
        _local_hidden += _h
    except Exception:
        pass  # not installed in this build env; skip

# av (PyAV) needs special handling: collect_all misclassifies its .pyd as datas
# (returns 0 binaries) and its ffmpeg DLLs live in a sibling `av.libs` dir
# (delvewheel layout). collect_submodules forces the .pyd to bundle as proper
# extensions; the av.libs DLLs go in as binaries. This combo was verified
# importable inside a frozen test EXE before shipping (crashed without it).
_local_hidden += collect_submodules("av")

# Build-time licence constants (tools/set_licence_key.py). Git-ignored and absent in a fresh
# checkout, so it is named only when it exists: PyInstaller warns loudly about a hidden import it
# cannot resolve, and a dev build legitimately has no key. `licensing._baked` treats the missing
# module as "no key" and `tools.build_release` reports which case a build is in.
_spec_dir = globals().get("SPECPATH") or os.getcwd()
if os.path.isfile(os.path.join(_spec_dir, "licence_key.py")):
    _local_hidden.append("licence_key")
try:
    import av as _av
    _av_libs = os.path.dirname(_av.__file__) + ".libs"
    if os.path.isdir(_av_libs):
        _local_bins += [(_f, "av.libs") for _f in glob.glob(os.path.join(_av_libs, "*.dll"))]
except Exception:
    pass


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=pyqt6_libs + _local_bins,
    datas=pyqt6_data + _local_datas + [
        # Brand artwork, read at runtime by brand.py: the orange mark for every window icon,
        # title bar and the chat panel's header, and the pointer source that
        # tools/trace_cursor.py derived overlay.py's vector from. Path-relative like the tray
        # icon below, so both need an explicit entry or the logo silently vanishes in the
        # frozen build -- and brand.py degrades to a null pixmap rather than failing loudly,
        # which is right at runtime and exactly why this line has to be here.
        ("assets/Nimbus tranparent .png", "assets"),
        ("assets/cursor.png", "assets"),
        # Tray icon — referenced by tray.py at runtime via Path-relative
        # lookup. Without this entry, the .ico is missing from the
        # bundle and the tray icon shows blank.
        ("assets/nimbus_tray.ico", "assets"),
    ],
    hiddenimports=[
        # Qt 6 sub-modules — PyInstaller's hook misses some by default.
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.QtMultimedia",
        # Audio I/O
        "sounddevice",
        "numpy",
        # Hotkey + mouse — pynput's platform-specific shims
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        # Screen capture
        "mss.windows",
        # SDK deps — explicit so PyInstaller doesn't miss them
        "anthropic",
        "openai",
        # Native Gemini SDK (T1-1). google.genai loads its auth backend and websocket
        # transport dynamically, so the submodules are named explicitly rather than
        # left to PyInstaller's import graph.
        "google.genai",
        "google.genai.types",
        "google.auth",
        "google.auth.transport.requests",
        "gemini_native",
        # T1-6a KB context caching and T1-4 Live speech-to-speech. Both are imported
        # lazily behind experimental toggles, so PyInstaller's static graph cannot see
        # them from ai.py alone.
        "gemini_cache",
        "gemini_live",
        # T2-1 Privacy Guard policy and T2-5 per-app prompt addenda. privacy is
        # imported lazily at the point of use, so name both explicitly.
        "privacy",
        "prompts",
        # T3-3 Knowledge Journal. Lazily imported behind a property, so name it explicitly.
        "review",
        # Design system (SHELL_AND_CHAT.md §2). Imported by overlay.py, the shell and the
        # chat HUD, so all three share one palette.
        "theme",
        # Shared logo loader, imported by shell/titlebar.py and chat_hud.py.
        "brand",
        # Chat HUD and its persistence (SHELL_AND_CHAT.md §4). Constructed in main() only
        # when CHAT_HUD is on, which is a lazy import by any other name.
        "chat_hud",
        "sessions",
        # Licence gate (SHELL_AND_CHAT.md §5). Both are imported inside a try block in __main__,
        # so PyInstaller's static graph does not see them -- and a build missing these would fail
        # at the very first thing a new user does.
        "licensing",
        "activation_dialog",
        # Ed25519 verification. `cryptography` ships native bindings, so the submodules the
        # licence path touches are named rather than left to the hook.
        "cryptography",
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        "cryptography.hazmat.primitives.serialization",
        # Application shell (SHELL_AND_CHAT.md §3). Every module named individually:
        # shell/__init__.py resolves MainWindow through a module-level __getattr__, which
        # PyInstaller's static graph cannot follow, so importing "shell" alone would bundle
        # the package and none of the windows inside it. Exactly the gap that caught
        # gemini_cache and gemini_live in Tier 1.
        "shell",
        "shell.window",
        "shell.nav",
        "shell.titlebar",
        "shell.widgets",
        "shell.pages",
        "shell.pages.home",
        "shell.pages.knowledge",
        "shell.pages.journal",
        "shell.pages.settings",
        "shell.pages.account",
        # T3-2 knowledge-base document formats. Lazily imported inside kb.extract_*, so
        # PyInstaller's static graph cannot see them.
        "pypdf",
        "docx",
        "lxml",       # python-docx's XML backend
        "lxml.etree",
        "cartesia",
        "elevenlabs",  # — opt-in alternative TTS
        "assemblyai",
        # Local offline providers (opt-in) — faster-whisper STT + Kokoro TTS.
        # Lazy-imported at runtime; bundled so one installer carries both the
        # cloud (default) and local lanes. Model weights download on first use.
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "kokoro_onnx",
        "soundfile",
        # HTTP / networking deps used transitively by the SDKs
        "websockets",
        "httpx",
        "httpx._transports.default",
        # Image processing
        "PIL",
        "PIL.Image",
        # Keyring — Windows Credential Manager backend is loaded
        # dynamically via entry_points; PyInstaller's hook can miss it.
        "keyring",
        "keyring.backends",
        "keyring.backends.Windows",
        # Startup release notification + build-time version identifier.
        "updates",
        "version",
        *_local_hidden,  # submodules pulled by collect_all for the local stack
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pytest",
        "pytest_mock",
        # Other Qt bindings — including any of these by accident bloats
        # the bundle and can cause runtime symbol clashes.
        "PySide6",
        "PyQt5",
        "PySide2",
        # Heavy ML / scientific stack pulled in transitively (likely via
        # optional deps in some package's deep dep graph) but NEVER used
        # by Nimbus's runtime — we route vision via the LLM's HTTP
        # SDK, audio via streaming HTTP/WebSocket, and screen capture via
        # mss. No tensors, no JIT, no dataframes. First build was 1.1GB;
        # excluding these drops it ~60% to ~440MB.
        "torch",          # 315MB — PyTorch
        "torchvision",
        "torchaudio",
        "llvmlite",       # 102MB — LLVM bindings (numba transitive)
        "numba",          # JIT — not used
        "pyarrow",        # 76MB — Apache Arrow
        # av is NO LONGER excluded — faster-whisper (local STT) imports PyAV.
        "scipy",          # 53MB — scientific computing
        # onnxruntime is NO LONGER excluded — Kokoro local TTS requires it.
        "pandas",         # 17MB — dataframes
        # jsonschema's OPTIONAL URI-format checkers. Kokoro/phonemizer never
        # use them; excluding lets jsonschema skip them so we don't bundle
        # rfc3987_syntax's .lark grammar. Verified safe in a frozen test EXE.
        "rfc3987_syntax",
        "rfc3987",
        "lark",
        # Dev / interactive tooling — never used at runtime
        "IPython",
        "ipykernel",
        "jedi",
        "parso",
        "jupyter",
        "jupyter_client",
        "notebook",
        "matplotlib",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Nimbus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app — no console window flash on launch
    icon="assets/nimbus_tray.ico",  # embedded as Windows resource in
                                    # the EXE — used by taskbar,
                                    # Alt-Tab, Start Menu shortcut,
                                    # Apps & features uninstall list.
                                    # Multi-res .ico (16/32/48/64/128/256)
                                    # so Windows picks native size for
                                    # each surface (no blur).
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Nimbus",
)
