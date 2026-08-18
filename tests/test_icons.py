"""Tests for tools/make_icons.py — the Windows icon artwork.

These assert things a screenshot cannot: that the shipped ``.ico`` holds **every** size Windows
asks for, that its pixels are the orange mark rather than the old blue one, and that it still
matches the source artwork it was generated from.

That last one is the point of the file. The icon is a *derived* asset committed to the repository,
because Windows reads it out of the executable's PE header before any Python runs, so it cannot be
produced at runtime. Committed derived files go stale silently, and a stale app icon is invisible
in code review and obvious on a desktop.
"""

import pytest


ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def load_frame(size: int):
    """One frame of the icon at ``size``, as RGBA."""
    from PIL import Image

    import tools.make_icons as make_icons

    image = Image.open(make_icons.TRAY_ICO)
    image.size = (size, size)
    image.load()
    return image.convert("RGBA")


def average_colour(image) -> tuple[int, int, int]:
    """Mean RGB of the non-transparent pixels."""
    pixels = [pixel for pixel in image.getdata() if pixel[3] > 40]
    assert pixels, "the frame is entirely transparent"
    return tuple(sum(pixel[index] for pixel in pixels) // len(pixels) for index in range(3))


class TestTheShippedIcon:
    def test_the_icon_exists_and_is_an_ico(self):
        from PIL import Image

        import tools.make_icons as make_icons

        assert make_icons.TRAY_ICO.is_file()
        assert Image.open(make_icons.TRAY_ICO).format == "ICO"

    def test_every_size_windows_asks_for_is_present(self):
        """An ``.ico`` holding one large bitmap looks fine at 256px and turns to mush at 16px,
        because Explorer downsamples it with no idea which details matter.

        16 and 24 are the taskbar and tray; 32 and 48 are Explorer's list and tile views; 64, 128
        and 256 are large icons and the Alt-Tab overlay.
        """
        from PIL import Image

        import tools.make_icons as make_icons

        sizes = Image.open(make_icons.TRAY_ICO).info.get("sizes", set())
        for size in ICO_SIZES:
            assert (size, size) in sizes, f"{size}px frame missing"

    @pytest.mark.parametrize("size", ICO_SIZES)
    def test_every_frame_is_the_orange_mark(self, size):
        """The regression that prompted this: the file held the old **blue** artwork while every
        in-app surface had moved to orange, so the desktop and taskbar were off-brand."""
        red, green, blue = average_colour(load_frame(size))

        assert red > blue + 60, f"{size}px frame is not warm (rgb {red},{green},{blue})"
        assert red > green > blue, f"{size}px frame is not orange (rgb {red},{green},{blue})"

    @pytest.mark.parametrize("size", (16, 32, 256))
    def test_the_mark_fills_its_frame(self, size):
        """The source is a 1536x1024 canvas with the mark floating in the middle of it. Scaling
        it directly gives a mark a third of the size in a box of padding -- which at 16px is a
        handful of pixels. Trimming first is what makes the small frames legible."""
        image = load_frame(size)
        opaque = sum(1 for pixel in image.getdata() if pixel[3] > 40)
        coverage = opaque / (size * size)

        assert coverage > 0.20, f"{size}px frame is mostly empty ({coverage:.0%} covered)"

    @pytest.mark.parametrize("size", ICO_SIZES)
    def test_every_frame_has_transparency(self, size):
        """A logo on an opaque square looks like a sticker on every surface Windows draws it."""
        image = load_frame(size)
        assert any(pixel[3] < 20 for pixel in image.getdata()), (
            f"{size}px frame has no transparent pixels")


class TestItMatchesTheSource:
    def test_the_committed_icon_is_not_stale(self):
        """The guard that makes a committed derived asset safe.

        ``--check`` regenerates into a temporary file and compares digests, so re-exporting the
        brand artwork without re-running the generator fails here rather than shipping.
        """
        import tools.make_icons as make_icons

        assert make_icons.main(["--check"]) == 0, (
            "assets/nimbus_tray.ico is stale -- run: python -m tools.make_icons")

    def test_it_is_generated_from_the_same_artwork_the_app_uses(self):
        """So the desktop icon and the in-app logo cannot drift apart."""
        import brand
        import tools.make_icons as make_icons

        assert make_icons.SOURCE == brand.asset_path(brand.MARK)
        assert make_icons.CURSOR == brand.asset_path(brand.CURSOR)


class TestInstallerArtwork:
    def test_the_wizard_banner_is_the_right_shape_and_dark(self):
        """Inno's default is a white-background stock image, which is the first thing a user sees
        of the product."""
        from PIL import Image

        import theme
        import tools.make_icons as make_icons

        assert make_icons.INSTALLER_PNG.is_file()
        banner = Image.open(make_icons.INSTALLER_PNG)
        assert banner.size == (164, 314)
        assert banner.getpixel((2, 2)) == theme.parse_hex(theme.BG_BASE)

    def test_the_small_header_image_uses_the_pointer(self):
        """At 55x55 the full mark's lockup becomes a blob; the pointer is one legible silhouette.
        Same reasoning as the chat panel's header."""
        from PIL import Image

        import tools.make_icons as make_icons

        assert make_icons.INSTALLER_SMALL_PNG.is_file()
        assert Image.open(make_icons.INSTALLER_SMALL_PNG).size == (138, 140)

    def test_the_installer_script_points_at_all_three(self):
        """A generated asset nobody references is dead weight; a referenced one that does not
        exist fails the build."""
        from pathlib import Path

        import tools.make_icons as make_icons

        script = (make_icons.ROOT / "installer" / "nimbus.iss").read_text(encoding="utf-8")

        for directive, asset in (
            ("SetupIconFile", make_icons.TRAY_ICO),
            ("WizardImageFile", make_icons.INSTALLER_PNG),
            ("WizardSmallImageFile", make_icons.INSTALLER_SMALL_PNG),
        ):
            line = next(
                (row for row in script.splitlines() if row.strip().startswith(directive)), None)
            assert line, f"{directive} is not set"
            referenced = line.split("=", 1)[1].strip()
            resolved = (make_icons.ROOT / "installer" / Path(referenced)).resolve()
            assert resolved == asset.resolve(), f"{directive} points at {resolved}"


class TestPackaging:
    def test_the_generated_assets_are_bundled(self):
        """PyInstaller only ships what the spec names. A missing entry means the tray icon and
        the logo silently vanish in the frozen build."""
        import tools.make_icons as make_icons

        spec = (make_icons.ROOT / "nimbus.spec").read_text(encoding="utf-8")

        assert "assets/nimbus_tray.ico" in spec
        assert "assets/Nimbus tranparent .png" in spec
        assert "assets/cursor.png" in spec

    def test_the_executable_embeds_the_icon(self):
        """The PE resource Windows reads for the taskbar, Alt-Tab and Explorer."""
        import tools.make_icons as make_icons

        spec = (make_icons.ROOT / "nimbus.spec").read_text(encoding="utf-8")
        assert "nimbus_tray.ico" in spec
        assert "icon=" in spec


class TestBuildIsNotStale:
    """The guard that would have saved a whole round of manual testing.

    ``--clean --verify`` used to run *only* the verify step, because ``--verify`` was tested first
    in ``main``. PyInstaller never ran, verify passed against a ``dist/`` from an earlier session,
    and it printed a clean bill of health -- so an entire smoke test went against a stale binary and
    every "you did not fix this" was correct: the fixes were not in the executable.

    Verify used to prove the *bundle* was internally consistent -- assets present, every module
    importable -- which a months-old build also passes. It never asked whether the build was of the
    current code. Now it compares the executable's timestamp against its sources.
    """

    def test_verify_only_refuses_to_be_combined_with_a_build_flag(self, capsys):
        """Refused rather than resolved. Letting one silently win is what caused this."""
        from tools import build_release

        assert build_release.main(["--clean", "--verify-only"]) == 2
        assert build_release.main(["--verify", "--installer"]) == 2
        out = capsys.readouterr().out
        assert "cannot be combined" in out

    def test_a_build_older_than_its_sources_fails(self, mocker, tmp_path):
        from tools import build_release

        exe = tmp_path / "Nimbus.exe"
        exe.write_bytes(b"stub")
        source = tmp_path / "app.py"
        source.write_text("x = 1", encoding="utf-8")

        mocker.patch.object(build_release, "EXE", exe)
        mocker.patch.object(
            build_release, "newest_source",
            return_value=(build_release.ROOT / "app.py", exe.stat().st_mtime + 60))

        assert build_release.check_not_stale() is False

    def test_a_build_newer_than_its_sources_passes(self, mocker, tmp_path):
        from tools import build_release

        exe = tmp_path / "Nimbus.exe"
        exe.write_bytes(b"stub")

        mocker.patch.object(build_release, "EXE", exe)
        mocker.patch.object(
            build_release, "newest_source",
            return_value=(build_release.ROOT / "app.py", exe.stat().st_mtime - 60))

        assert build_release.check_not_stale() is True

    def test_the_source_list_covers_the_shell_and_ignores_tests(self):
        """``tests/`` and ``tools/`` are excluded on purpose: editing a test does not make a build
        stale. ``shell/`` is included, and it is the newest-moving part of the codebase."""
        from tools import build_release

        assert "shell/**/*.py" in build_release.SOURCE_GLOBS
        assert "nimbus.spec" in build_release.SOURCE_GLOBS
        assert not any(glob.startswith("tests") for glob in build_release.SOURCE_GLOBS)

    def test_throwaway_probes_do_not_report_a_build_as_stale(self, tmp_path, mocker):
        """``_something.py`` scratch scripts are never bundled, so one lying around must not
        fail the check -- which it did, on the first run of this guard."""
        from tools import build_release

        mocker.patch.object(build_release, "ROOT", tmp_path)
        mocker.patch.object(build_release, "SOURCE_GLOBS", ("*.py",))
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        probe = tmp_path / "_probe_thing.py"
        probe.write_text("y = 2", encoding="utf-8")
        import os
        import time

        future = time.time() + 3600
        os.utime(probe, (future, future))

        newest, _ = build_release.newest_source()
        assert newest is not None
        assert newest.name == "app.py"
