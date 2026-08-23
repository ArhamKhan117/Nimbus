"""A default recording device that will not open must not stop Nimbus starting.

Reported from a real first launch: the dialog said "speech-to-text failed to load" with PortAudio
-9985, "device unavailable", and advised switching to the AssemblyAI cloud provider. That advice could
not work, because every provider records from the same microphone. Meanwhile the machine had a working
`External Mic` on the same sound card, and the default happened to be a `Line In` jack with nothing
plugged into it.

Two things are pinned here. The fallback tries other input devices rather than giving up, and it only
accepts one that survives being **started**, because a device can construct happily and fail the moment
audio is asked for. And when nothing works, the error names the default, the underlying failure and
every device tried, rather than pointing at a setting that cannot help.

``sounddevice`` is faked throughout. The real fault is a transient property of one machine's audio
stack, and a test that waits for it is a test that fails for unrelated reasons.
"""
from __future__ import annotations

import sys

import pytest


class FakeStream:
    def __init__(self, device, fail_on_start: bool) -> None:
        self.device = device
        self._fail_on_start = fail_on_start
        self.started = False
        self.closed = False

    def start(self):
        if self._fail_on_start:
            raise RuntimeError(f"device {self.device} accepted the open and then failed")
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


class FakeSoundDevice:
    """Just enough of the module: a device list, a default, and an opener with scripted failures."""

    def __init__(self, devices, default_index, openable, fail_on_start=()) -> None:
        self._devices = devices
        self._openable = set(openable)
        self._fail_on_start = set(fail_on_start)
        self.default = type("Default", (), {"device": [default_index, 6]})()
        self.opened: list[object] = []

    def query_devices(self):
        return self._devices

    def query_hostapis(self, index):
        return {"name": "MME"}

    def RawInputStream(self, device=None, **kwargs):
        target = self._openable if device is None else {device}
        chosen = None if device is None else device
        if device is None:
            # Mirrors sounddevice: no device means the Windows default.
            chosen = self.default.device[0]
        self.opened.append(chosen)
        if chosen not in self._openable:
            raise RuntimeError(
                f"Error opening RawInputStream: Device unavailable [PaErrorCode -9985] "
                f"(device {chosen})")
        assert target  # the branch above decided what we are opening
        return FakeStream(chosen, fail_on_start=chosen in self._fail_on_start)


def devices(*names_with_channels):
    return [{"name": name, "max_input_channels": channels, "hostapi": 0}
            for name, channels in names_with_channels]


THREE_DEVICES = devices(
    ("Microsoft Sound Mapper - Input", 2),
    ("Line In (Sound BlasterX G6)", 2),
    ("External Mic (Sound BlasterX G6)", 2),
    ("Speakers", 0),
)


@pytest.fixture
def fake_sd(monkeypatch):
    """Install a fake ``sounddevice`` for the duration, since ``open_input_stream`` imports it."""
    def install(sound_device):
        monkeypatch.setitem(sys.modules, "sounddevice", sound_device)
        return sound_device
    return install


class TestOpeningTheMicrophone:
    def test_the_default_is_used_when_it_works(self, fake_sd):
        """No probing, no surprises. This is nearly every machine and it must stay the fast path."""
        from stt import open_input_stream

        sound = fake_sd(FakeSoundDevice(THREE_DEVICES, default_index=2, openable={2}))
        stream = open_input_stream(samplerate=16000, channels=1, dtype="int16")

        assert stream.device == 2
        assert sound.opened == [2], "it should not have gone looking for anything else"

    def test_a_dead_default_falls_back_to_a_device_that_works(self, fake_sd):
        """The reported failure. The working microphone was there all along."""
        from stt import open_input_stream

        # Default is Line In, which is unopenable. External Mic works.
        sound = fake_sd(FakeSoundDevice(THREE_DEVICES, default_index=1, openable={2}))
        stream = open_input_stream(samplerate=16000, channels=1, dtype="int16")

        assert stream.device == 2
        assert 1 in sound.opened, "the default must still be tried first"

    def test_a_device_that_opens_and_then_fails_is_rejected(self, fake_sd):
        """Construction succeeding proves nothing; the probe has to start the stream.

        This is the difference between choosing a device and choosing a device that delivers
        audio, and getting it wrong would swap a startup error for a microphone that is silent.
        """
        from stt import open_input_stream

        sound = fake_sd(FakeSoundDevice(
            THREE_DEVICES, default_index=1, openable={0, 2}, fail_on_start={0}))
        stream = open_input_stream(samplerate=16000, channels=1, dtype="int16")

        assert stream.device == 2

    def test_output_only_devices_are_skipped(self, fake_sd):
        from stt import open_input_stream

        sound = fake_sd(FakeSoundDevice(THREE_DEVICES, default_index=1, openable={2, 3}))
        stream = open_input_stream(samplerate=16000, channels=1, dtype="int16")

        assert stream.device == 2, "device 3 has no input channels and is not a microphone"

    def test_the_callback_is_not_passed_to_the_probe(self, fake_sd):
        """A probe that invoked the real callback would feed the recogniser throwaway audio."""
        from stt import open_input_stream

        seen = []

        class Recording(FakeSoundDevice):
            def RawInputStream(self, device=None, **kwargs):
                seen.append((device, "callback" in kwargs))
                return super().RawInputStream(device=device, **kwargs)

        fake_sd(Recording(THREE_DEVICES, default_index=1, openable={2}))
        open_input_stream(samplerate=16000, channels=1, dtype="int16",
                          callback=lambda *args: None)

        probes = [(device, has_callback) for device, has_callback in seen
                  if device == 2 and not has_callback]
        assert probes, "device 2 should have been probed without the callback"
        assert (2, True) in seen, "and then opened for real with it"


class TestWhenNothingWorks:
    def test_it_raises_a_distinct_type(self, fake_sd):
        """So the caller can stop advising a provider switch that cannot help."""
        from stt import MicrophoneUnavailable, open_input_stream

        fake_sd(FakeSoundDevice(THREE_DEVICES, default_index=1, openable=set()))

        with pytest.raises(MicrophoneUnavailable):
            open_input_stream(samplerate=16000, channels=1, dtype="int16")

    def test_the_message_is_actionable(self, fake_sd):
        """It names the default, the real error, and what else was tried."""
        from stt import MicrophoneUnavailable, open_input_stream

        fake_sd(FakeSoundDevice(THREE_DEVICES, default_index=1, openable=set()))

        with pytest.raises(MicrophoneUnavailable) as caught:
            open_input_stream(samplerate=16000, channels=1, dtype="int16")

        message = str(caught.value)
        assert "Line In (Sound BlasterX G6)" in message, "the default must be named"
        assert "-9985" in message, "the underlying error must survive"
        assert "External Mic (Sound BlasterX G6)" in message, "and what else was tried"
        assert "Sound settings" in message, "and where to fix it"
        assert "will not help" in message, "and that changing provider is not the fix"
