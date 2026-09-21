"""faster_whisper picks a device that works, and never lies about which.

`FasterWhisperEngine.meta` says `needs_gpu=False` and `extra="runs on CPU,
faster with CUDA"`, so the engine has to actually run on a machine whose CUDA
runtime is unusable -- an NVIDIA card with no cuBLAS on PATH, which is the
ordinary state of a fresh Windows box and of most CI runners. CTranslate2 loads
the weights without touching cuBLAS and only fails on the first encode, so the
retry has to wrap the whole transcription, not the constructor: that is exactly
the bug these tests pin.

The other half is restraint. `auto` is a request to find something that works;
`cuda` is not. An explicit device that fails is reported, because a run that
quietly moved to the CPU would look like a working GPU that is thirty times too
slow, and nobody would ever notice.

Nothing here imports faster-whisper or loads a model: the runtime is a fake
whose only job is to fail on the device the test says it fails on.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pytest

from lecture2notes import _deps, _out
from lecture2notes.engines.breeze_ct2 import BreezeCT2Engine
from lecture2notes.engines.faster_whisper import (
    AUTO_DEVICE,
    CPU_COMPUTE_TYPE,
    CPU_DEVICE,
    DEFAULT_COMPUTE_TYPE,
    FasterWhisperEngine,
)

#: What CTranslate2 raises on a machine with a GPU and no cuBLAS, verbatim.
CUBLAS_ERROR = "Library cublas64_12.dll is not found or cannot be loaded"


class _Segment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _Info:
    language = "en"
    language_probability = 1.0


class _FakeModel:
    """One loaded model. Fails where the fake runtime was told to fail."""

    def __init__(self, runtime, model: str, device: str, compute_type: str) -> None:
        self.runtime = runtime
        runtime.loads.append((model, device, compute_type))
        if device in runtime.fails_on_load:
            raise RuntimeError(CUBLAS_ERROR)
        self.device = device

    def transcribe(self, audio, **options):
        self.runtime.transcribes.append((audio, self.device, options))
        device = self.device

        def segments():
            if device in self.runtime.fails_on_encode:
                # CTranslate2 fails here, on the first encode, not on the load.
                raise RuntimeError(CUBLAS_ERROR)
            yield _Segment(0.0, 1.0, " hello ")
            yield _Segment(1.0, 2.0, " world ")

        return segments(), _Info()


class _FakeRuntime:
    """Stands in for the `faster_whisper` module."""

    def __init__(
        self,
        fails_on_encode: Optional[List[str]] = None,
        fails_on_load: Optional[List[str]] = None,
    ) -> None:
        self.fails_on_encode = list(fails_on_encode or [])
        self.fails_on_load = list(fails_on_load or [])
        self.loads: List[tuple] = []
        self.transcribes: List[tuple] = []

    def WhisperModel(self, model, device, compute_type):  # noqa: N802 - upstream name
        return _FakeModel(self, model, device, compute_type)


@pytest.fixture()
def runtime(monkeypatch: pytest.MonkeyPatch):
    """Install a fake `faster_whisper`, and hand the test its call log."""
    fake = _FakeRuntime()
    monkeypatch.setattr(_deps, "require_module", lambda *a, **k: fake)
    return fake


def _devices_tried(fake: _FakeRuntime) -> List[str]:
    return [device for _model, device, _compute in fake.loads]


# -- the fallback ----------------------------------------------------------
def test_auto_falls_back_to_cpu_when_the_gpu_fails_on_the_first_encode(runtime):
    runtime.fails_on_encode.append(AUTO_DEVICE)
    engine = FasterWhisperEngine(model="tiny")

    cues = engine.transcribe(Path("clip.mp4"), "en")

    assert _devices_tried(runtime) == [AUTO_DEVICE, CPU_DEVICE]
    assert [cue.text for cue in cues] == ["hello", "world"]


def test_auto_falls_back_to_cpu_when_the_model_will_not_even_load(runtime):
    runtime.fails_on_load.append(AUTO_DEVICE)
    engine = FasterWhisperEngine(model="tiny")

    cues = engine.transcribe(Path("clip.mp4"), "en")

    assert _devices_tried(runtime) == [AUTO_DEVICE, CPU_DEVICE]
    assert len(cues) == 2


def test_the_fallback_says_so_out_loud(runtime, capsys):
    runtime.fails_on_encode.append(AUTO_DEVICE)

    FasterWhisperEngine(model="tiny").transcribe(Path("clip.mp4"), "en")

    printed = capsys.readouterr().out
    assert "[warn]" in printed
    assert CPU_DEVICE in printed.lower()
    # The reason is quoted, not summarised: "retrying on the CPU" with no cause
    # is the kind of message that sends people looking for a bug in the audio.
    assert CUBLAS_ERROR in printed


def test_a_working_gpu_is_never_second_guessed(runtime):
    cues = FasterWhisperEngine(model="tiny").transcribe(Path("clip.mp4"), "en")

    assert _devices_tried(runtime) == [AUTO_DEVICE]
    assert len(cues) == 2


# -- restraint -------------------------------------------------------------
def test_an_explicitly_named_device_is_reported_not_replaced(runtime):
    runtime.fails_on_encode.append("cuda")
    engine = FasterWhisperEngine(model="tiny", device="cuda")

    with pytest.raises(RuntimeError) as excinfo:
        engine.transcribe(Path("clip.mp4"), "en")

    assert CUBLAS_ERROR in str(excinfo.value)
    assert _devices_tried(runtime) == ["cuda"], "it must not retry elsewhere"


def test_breeze_asks_for_cuda_and_so_gets_no_fallback(runtime, tmp_path):
    """breeze_ct2 is a GPU engine (`needs_gpu=True`) and names its device."""
    runtime.fails_on_encode.append("cuda")
    engine = BreezeCT2Engine(model_dir=tmp_path)
    engine.check = lambda: None  # the model directory is not what is under test

    with pytest.raises(RuntimeError):
        engine.transcribe(Path("clip.mp4"), "zh")

    assert _devices_tried(runtime) == ["cuda"]


# -- compute type ----------------------------------------------------------
def test_the_default_float16_becomes_int8_on_the_cpu():
    engine = FasterWhisperEngine(model="tiny")

    assert engine.compute_type_for(CPU_DEVICE) == CPU_COMPUTE_TYPE
    assert engine.compute_type_for(AUTO_DEVICE) == DEFAULT_COMPUTE_TYPE


def test_a_compute_type_the_caller_named_is_kept_everywhere():
    engine = FasterWhisperEngine(model="tiny", compute_type="float32")

    assert engine.compute_type_for(CPU_DEVICE) == "float32"
    assert engine.compute_type_for("cuda") == "float32"


def test_the_fallback_load_uses_the_cpu_compute_type(runtime):
    runtime.fails_on_encode.append(AUTO_DEVICE)

    FasterWhisperEngine(model="tiny").transcribe(Path("clip.mp4"), "en")

    assert runtime.loads[-1] == ("tiny", CPU_DEVICE, CPU_COMPUTE_TYPE)


# -- the options the engine passes on --------------------------------------
def test_auto_language_is_sent_as_none(runtime):
    FasterWhisperEngine(model="tiny").transcribe(Path("clip.mp4"), "auto")

    _audio, _device, options = runtime.transcribes[-1]
    assert options["language"] is None


def test_vad_and_conditioning_stay_off_by_default(runtime):
    FasterWhisperEngine(model="tiny").transcribe(Path("clip.mp4"), "en")

    _audio, _device, options = runtime.transcribes[-1]
    assert options["vad_filter"] is False
    assert options["condition_on_previous_text"] is False


def test_output_flags_do_not_leak(runtime):
    # _out is module state; the autouse conftest fixture resets it, and this
    # asserts the engine did not turn anything on behind that reset.
    assert _out.is_quiet() is False
