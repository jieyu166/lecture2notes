"""`l2n convert-model`: say the cost first, and never overwrite by surprise.

No test here converts anything. The converter is injected as a mock, so the
arguments are asserted without ctranslate2 installed and without downloading six
gigabytes. The one real-machine behaviour that matters — refusing to overwrite a
model directory that already exists — is exercised against a temporary directory
shaped like one, never against the user's actual weights.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture2notes import _deps, _out, exit_codes
from lecture2notes.cli.main import main as run_main
from lecture2notes.engines import convert
from lecture2notes.engines.breeze_ct2 import SOURCE_MODEL


class MockConverter:
    """Records the conversion call instead of performing it."""

    def __init__(self, source, copy_files):
        self.source = source
        self.copy_files = copy_files
        self.calls = []

    def convert(self, output_dir, quantization, force=False):
        self.calls.append(
            {"output_dir": output_dir, "quantization": quantization, "force": force}
        )
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "model.bin").write_bytes(b"converted")
        return target


@pytest.fixture()
def mock_factory():
    made = []

    def factory(source, copy_files):
        converter = MockConverter(source, copy_files)
        made.append(converter)
        return converter

    factory.made = made
    return factory


def existing_model(tmp_path: Path) -> Path:
    """A directory shaped like an already-converted model."""
    target = tmp_path / "breeze-asr-25-ct2"
    target.mkdir()
    (target / "model.bin").write_bytes(b"pretend weights")
    return target


# -- the disk estimate -----------------------------------------------------
def test_disk_report_states_source_output_and_peak():
    lines = convert.disk_report("float16")
    assert "6.0 GB" in lines[0]
    assert "3.0 GB" in lines[1]
    assert "9.0 GB" in lines[2]


def test_disk_report_is_ascii_only():
    for line in convert.disk_report():
        line.encode("ascii")


def test_disk_report_follows_the_quantization():
    assert convert.output_estimate_gb("int8") < convert.output_estimate_gb("float16")
    assert convert.output_estimate_gb("float32") > convert.output_estimate_gb("float16")


def test_the_estimate_is_printed_before_anything_is_written(tmp_path, capsys,
                                                            mock_factory):
    target = tmp_path / "out"
    convert.convert(out_dir=target, converter_factory=mock_factory)
    printed = capsys.readouterr().out
    assert "peak disk needed" in printed
    assert printed.index("peak disk needed") < printed.index("converted to")


# -- defaults --------------------------------------------------------------
def test_float16_is_the_default_quantization(tmp_path, mock_factory):
    convert.convert(out_dir=tmp_path / "out", converter_factory=mock_factory)
    assert mock_factory.made[0].calls[0]["quantization"] == "float16"


def test_the_default_source_is_the_breeze_checkpoint(tmp_path, mock_factory):
    convert.convert(out_dir=tmp_path / "out", converter_factory=mock_factory)
    assert mock_factory.made[0].source == SOURCE_MODEL
    assert "Breeze-ASR-25" in SOURCE_MODEL


def test_the_tokeniser_files_are_copied_next_to_the_model(tmp_path, mock_factory):
    convert.convert(out_dir=tmp_path / "out", converter_factory=mock_factory)
    assert "tokenizer.json" in mock_factory.made[0].copy_files


def test_an_explicit_quantization_reaches_the_converter(tmp_path, mock_factory):
    convert.convert(out_dir=tmp_path / "out", quantization="int8",
                    converter_factory=mock_factory)
    assert mock_factory.made[0].calls[0]["quantization"] == "int8"


def test_an_unknown_quantization_is_refused_before_anything_runs(tmp_path, mock_factory):
    with pytest.raises(convert.ConversionRefused):
        convert.convert(out_dir=tmp_path / "out", quantization="fp8",
                        converter_factory=mock_factory)
    assert mock_factory.made == []


def test_the_default_target_is_the_directory_the_engine_looks_in(monkeypatch, tmp_path):
    """convert-model and the default engine must agree on one path."""
    from lecture2notes.engines import breeze_ct2

    monkeypatch.setenv(breeze_ct2.MODEL_DIR_ENV, str(tmp_path / "chosen"))
    assert breeze_ct2.default_model_dir() == tmp_path / "chosen"
    engine = breeze_ct2.BreezeCT2Engine()
    assert engine.model_dir == tmp_path / "chosen"


# -- the overwrite refusal -------------------------------------------------
def test_an_existing_target_is_refused_without_force(tmp_path, mock_factory):
    target = existing_model(tmp_path)
    with pytest.raises(convert.ConversionRefused) as excinfo:
        convert.convert(out_dir=target, converter_factory=mock_factory)
    assert "--force" in str(excinfo.value)
    assert str(target) in str(excinfo.value)


def test_the_refusal_leaves_the_existing_weights_untouched(tmp_path, mock_factory):
    target = existing_model(tmp_path)
    before = (target / "model.bin").read_bytes()
    with pytest.raises(convert.ConversionRefused):
        convert.convert(out_dir=target, converter_factory=mock_factory)
    assert (target / "model.bin").read_bytes() == before
    assert mock_factory.made == [], "the converter was built despite the refusal"


def test_force_allows_the_overwrite_and_is_passed_through(tmp_path, mock_factory):
    target = existing_model(tmp_path)
    convert.convert(out_dir=target, force=True, converter_factory=mock_factory)
    assert mock_factory.made[0].calls[0]["force"] is True
    assert (target / "model.bin").read_bytes() == b"converted"


def test_an_empty_directory_is_not_treated_as_an_existing_model(tmp_path, mock_factory):
    target = tmp_path / "empty"
    target.mkdir()
    convert.convert(out_dir=target, converter_factory=mock_factory)
    assert mock_factory.made[0].calls


def test_check_target_is_usable_on_its_own(tmp_path):
    assert convert.check_target(tmp_path / "missing") == tmp_path / "missing"
    with pytest.raises(convert.ConversionRefused):
        convert.check_target(existing_model(tmp_path))


# -- missing dependencies --------------------------------------------------
def test_a_missing_converter_package_exits_3(tmp_path, monkeypatch, capsys):
    def missing(name, **kwargs):
        raise _deps.MissingDependency(name, _deps.INSTALL_HINTS.get(name, "see README"))

    monkeypatch.setattr(_deps, "require_module", missing)
    code = run_main(["convert-model", "--out", str(tmp_path / "out")])
    captured = capsys.readouterr().out
    assert code == exit_codes.MISSING_DEPENDENCY
    assert "missing dependency" in captured
    assert not (tmp_path / "out").exists()


def test_the_overwrite_refusal_beats_the_dependency_check(tmp_path, monkeypatch, capsys):
    """An existing model is told so, not handed an install command."""
    def missing(name, **kwargs):
        raise _deps.MissingDependency(name, "pip install " + name)

    monkeypatch.setattr(_deps, "require_module", missing)
    target = existing_model(tmp_path)
    code = run_main(["convert-model", "--out", str(target)])
    captured = capsys.readouterr().out
    assert code == exit_codes.ERROR
    assert "--force" in captured
    assert "pip install" not in captured


# -- the CLI ---------------------------------------------------------------
def test_cli_refuses_an_existing_target_with_exit_2(tmp_path, capsys):
    target = existing_model(tmp_path)
    code = run_main(["convert-model", "--out", str(target)])
    assert code == exit_codes.ERROR
    assert "already exists" in capsys.readouterr().out


def test_cli_rejects_an_unknown_quantization_at_parse_time(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        run_main(["convert-model", "--out", str(tmp_path / "o"), "--quantization", "fp8"])
    assert excinfo.value.code == 2


def test_cli_succeeds_with_a_mocked_converter(tmp_path, monkeypatch, mock_factory):
    monkeypatch.setattr(convert, "_default_converter", mock_factory)
    code = run_main(["convert-model", "--out", str(tmp_path / "out")])
    assert code == exit_codes.OK
    assert (tmp_path / "out" / "model.bin").is_file()
