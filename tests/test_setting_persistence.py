"""A setting has to survive a restart, including when the credential vault lies about storing it.

This exists because a user reported that nothing in Settings survived a restart and no test caught it.
The vault on that machine returned normally from ``set_password`` and stored nothing, so every guard
of the form ``except Exception`` was dead code: there was no exception. Measured cause, for the record:
the backend writes the newest value to the bare service target with enterprise persistence, an
enterprise-persisted credential is roamed, roaming has a total size budget, and past that budget the
write is dropped and reported as success.

So the vault here is not merely stubbed, it is stubbed **dishonestly**: ``set_password`` accepts
everything and changes nothing. Every test below would pass against the old code if the fake merely
raised, which is exactly why the fake does not raise.
"""
from __future__ import annotations

import importlib
import json
import os

import pytest


class SilentlyDroppingVault:
    """Accepts every write, stores nothing, reads back whatever it was seeded with.

    Deletes work, because they did on the machine where this was measured. That detail matters: it is
    why the vault looks alive and why the failure is invisible without a read-back.
    """

    def __init__(self, seeded: dict[str, str] | None = None) -> None:
        self.seeded = dict(seeded or {})
        self.attempted: list[tuple[str, str]] = []

    def set_password(self, service, name, value):
        self.attempted.append((name, value))

    def get_password(self, service, name):
        return self.seeded.get(name)

    def delete_password(self, service, name):
        self.seeded.pop(name, None)


class WorkingVault:
    def __init__(self, seeded: dict[str, str] | None = None) -> None:
        self.store = dict(seeded or {})

    def set_password(self, service, name, value):
        self.store[name] = value

    def get_password(self, service, name):
        return self.store.get(name)

    def delete_password(self, service, name):
        self.store.pop(name, None)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """``config`` with an isolated fallback file and no environment values in the way."""
    import config

    # The fallback file is already redirected for every test by the autouse fixture in conftest, via
    # NIMBUS_SETTINGS_FALLBACK. Named here so the assertions below can read the file directly.
    module = importlib.reload(config)
    for name in ("ANNOTATION_MODE", "PRIVACY_GUARD", "CHAT_HUD", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return module


class TestAVaultThatDropsWrites:
    def test_a_setting_still_persists(self, settings, monkeypatch):
        """The reported bug: teaching mode was ticked, saved, and gone on the next launch."""
        vault = SilentlyDroppingVault({"ANNOTATION_MODE": "off"})
        monkeypatch.setattr(settings, "keyring", vault)

        assert settings.store_setting("ANNOTATION_MODE", "on") is True
        assert settings.resolve_setting("ANNOTATION_MODE", "off") == "on"

    def test_the_write_was_attempted_before_falling_back(self, settings, monkeypatch):
        """The vault is preferred, not abandoned. It encrypts at rest and holds everything already."""
        vault = SilentlyDroppingVault()
        monkeypatch.setattr(settings, "keyring", vault)

        settings.store_setting("ANNOTATION_MODE", "on")

        assert ("ANNOTATION_MODE", "on") in vault.attempted

    def test_the_fallback_beats_a_stale_vault_value(self, settings, monkeypatch):
        """Order is the fix, not an implementation detail.

        A name is only in the fallback because the vault would not update it, so the vault is still
        holding the old value. Reading the vault first returns that old value and the save looks as
        though it did nothing, which is the same bug one layer down.
        """
        vault = SilentlyDroppingVault({"PRIVACY_GUARD": "on"})
        monkeypatch.setattr(settings, "keyring", vault)

        settings.store_setting("PRIVACY_GUARD", "off")

        assert vault.get_password(None, "PRIVACY_GUARD") == "on", "the vault kept the stale value"
        assert settings.resolve_setting("PRIVACY_GUARD", "on") == "off"

    def test_an_api_key_persists_too(self, settings, monkeypatch):
        """A key that appears to save and then vanishes sends someone back to a provider dashboard."""
        monkeypatch.setattr(settings, "keyring", SilentlyDroppingVault())

        settings.store_setting("GEMINI_API_KEY", "AIza-not-a-real-key")

        assert settings.resolve_api_key("GEMINI_API_KEY") == "AIza-not-a-real-key"

    def test_many_settings_accumulate_rather_than_overwrite(self, settings, monkeypatch):
        """One file holds all of them, so writing the second must not lose the first."""
        monkeypatch.setattr(settings, "keyring", SilentlyDroppingVault())

        for name, value in (("ANNOTATION_MODE", "on"), ("CHAT_HUD", "off"), ("NAV_SIDE", "right")):
            settings.store_setting(name, value)

        assert settings.resolve_setting("ANNOTATION_MODE", "off") == "on"
        assert settings.resolve_setting("CHAT_HUD", "on") == "off"
        assert settings.resolve_setting("NAV_SIDE", "left") == "right"


class TestAVaultThatWorks:
    def test_nothing_is_written_to_the_fallback(self, settings, monkeypatch, tmp_path):
        """The fallback is for a broken vault. A working one must not leave a second copy behind."""
        monkeypatch.setattr(settings, "keyring", WorkingVault())

        assert settings.store_setting("ANNOTATION_MODE", "on") is True

        assert not (tmp_path / "settings.dat").exists()

    def test_a_recovered_vault_takes_over_again(self, settings, monkeypatch):
        """Otherwise the fallback becomes the stale one and the bug returns inverted.

        Someone whose vault was full deletes credentials, or moves to another machine, and their
        settings must not be frozen at whatever the file happened to hold.
        """
        monkeypatch.setattr(settings, "keyring", SilentlyDroppingVault())
        settings.store_setting("ANNOTATION_MODE", "on")

        working = WorkingVault()
        monkeypatch.setattr(settings, "keyring", working)
        settings.store_setting("ANNOTATION_MODE", "off")

        assert working.get_password(None, "ANNOTATION_MODE") == "off"
        assert settings.resolve_setting("ANNOTATION_MODE", "on") == "off"
        # And the fallback copy is gone, so it cannot resurface later.
        assert "ANNOTATION_MODE" not in settings._read_fallback()


class TestTheFallbackFile:
    def test_it_is_not_readable_as_plain_text(self, settings, monkeypatch, tmp_path):
        """It holds API keys, and it replaces a store that encrypts at rest.

        Skipped rather than failed where the platform has no user-bound encryption: the fallback is
        still correct there, and a plaintext file in the user's own profile beats losing the setting.
        """
        monkeypatch.setattr(settings, "keyring", SilentlyDroppingVault())
        settings.store_setting("GEMINI_API_KEY", "AIza-not-a-real-key")

        raw = (tmp_path / "settings.dat").read_bytes()
        if settings._dpapi(b"probe", protect=True) is None:
            pytest.skip("no user-bound encryption available on this platform")

        assert b"AIza-not-a-real-key" not in raw
        with pytest.raises(Exception):
            json.loads(raw.decode("utf-8"))

    def test_a_corrupt_file_costs_a_setting_and_not_a_startup(self, settings, monkeypatch, tmp_path):
        """Every resolver on this path is read at import, so raising here would refuse to launch."""
        monkeypatch.setattr(settings, "keyring", SilentlyDroppingVault())
        (tmp_path / "settings.dat").write_bytes(b"\x00\x01 not a sealed blob nor json")

        assert settings._read_fallback() == {}
        assert settings.resolve_setting("ANNOTATION_MODE", "off") == "off"

    def test_the_environment_still_wins(self, settings, monkeypatch):
        """Documented precedence, and the escape hatch for a machine where both stores are unhappy."""
        monkeypatch.setattr(settings, "keyring", SilentlyDroppingVault())
        settings.store_setting("ANNOTATION_MODE", "on")
        monkeypatch.setenv("ANNOTATION_MODE", "off")

        assert settings.resolve_setting("ANNOTATION_MODE", "on") == "off"


@pytest.fixture(scope="module")
def qt_app():
    """One QApplication for the module. Qt requires it before any QWidget exists."""
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


# Controls that are not settings. `_reveal` shows a value rather than storing one.
NOT_PERSISTED = frozenset({"_reveal"})


def _controls(form) -> dict:
    """Every control on the form that is meant to persist, by the attribute it hangs off.

    Discovered rather than listed, deliberately. A hand-written list is a list somebody forgets to
    add to, and the reported bug was precisely that a control did not persist -- so the guard has to
    find controls it was never told about.
    """
    from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox

    found = {}
    for name in dir(form):
        if not name.startswith("_") or name == "__dict__":
            continue
        try:
            value = getattr(form, name)
        except Exception:
            continue
        if isinstance(value, (QCheckBox, QSpinBox, QComboBox)):
            found[name] = value
        elif isinstance(value, dict):
            for key, inner in value.items():
                if isinstance(inner, (QCheckBox, QSpinBox, QComboBox)):
                    found[f"{name}[{key}]"] = inner
    return found


def _change(widget):
    """A different value, or ``None`` when the control offers no alternative."""
    from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox

    if isinstance(widget, QCheckBox):
        widget.setChecked(not widget.isChecked())
        return widget.isChecked()
    if isinstance(widget, QSpinBox):
        target = widget.value() + 1
        widget.setValue(widget.minimum() if target > widget.maximum() else target)
        return widget.value()
    if isinstance(widget, QComboBox) and widget.count() > 1:
        widget.setCurrentIndex((widget.currentIndex() + 1) % widget.count())
        return widget.currentIndex()
    return None


def _read(widget):
    from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox

    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QSpinBox):
        return widget.value()
    if isinstance(widget, QComboBox):
        return widget.currentIndex()
    return None


class TestEverySettingOnTheForm:
    """The end-to-end guard: change a control, save, rebuild the form, read it back.

    Rebuilding is what makes this meaningful. ``SettingsForm`` resolves every value in its
    constructor, so a fresh instance is the same read a relaunched Nimbus performs. The vault is the
    dishonest one, because that is the machine the bug was reported from -- on a healthy vault this
    would have passed before the fix and proved nothing.
    """

    @pytest.fixture
    def form_factory(self, settings, monkeypatch):
        import settings_dialog

        # Both modules: the form writes through `config.store_setting`, and its own reads go through
        # `config.resolve_setting`, but `required_keys_present` and the key fields read the vault too.
        vault = SilentlyDroppingVault()
        monkeypatch.setattr(settings, "keyring", vault)
        monkeypatch.setattr(settings_dialog, "keyring", vault)
        # The environment beats stored state by design, so anything set there could never round-trip.
        # A frozen build has no .env beside it, which is the case worth testing.
        for name in list(os.environ):
            if name.endswith(("_API_KEY", "_PROVIDER", "_MODEL", "_MODEL_VISION")):
                monkeypatch.delenv(name, raising=False)
        return settings_dialog.SettingsForm

    def test_every_control_survives_save_and_a_restart(self, qt_app, form_factory):
        factory = form_factory
        probe = factory()
        names = sorted(_controls(probe))
        probe.deleteLater()
        assert names, "no controls found, so this test would pass by doing nothing"

        failures = []
        for name in names:
            if name in NOT_PERSISTED:
                continue
            form = factory()
            widget = _controls(form).get(name)
            wanted = _change(widget) if widget is not None else None
            if wanted is None:
                form.deleteLater()
                continue
            saved = form.save()
            form.deleteLater()

            fresh = factory()
            got = _read(_controls(fresh).get(name))
            fresh.deleteLater()

            if not saved or got != wanted:
                failures.append(f"{name}: saved={saved} wanted={wanted} got={got}")

        assert not failures, "settings that do not survive a restart:\n  " + "\n  ".join(failures)
