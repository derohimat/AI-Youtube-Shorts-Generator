"""End-to-end render check on a synthetic video (skipped when ffmpeg is missing)."""
import shutil

import pytest

from Components import captions, framing, media, render

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("src") / "src.mp4"
    media.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6",
                      "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                      "-c:v", "libx264", "-c:a", "aac", "-shortest", path])
    return str(path)


@pytest.mark.parametrize("mode", ["center", "fit-blur"])
def test_render_vertical_with_captions(source, tmp_path, mode, monkeypatch):
    monkeypatch.setattr("Components.config.OUTPUT_WIDTH", 360)
    monkeypatch.setattr("Components.config.OUTPUT_HEIGHT", 640)
    plan = framing.plan_framing(source, 1, 4, mode, (640, 360))
    words = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("one two three four".split())]
    out = render.render_short(source, 1, 4, plan, str(tmp_path / "out.mp4"), captions.build_lines(words))
    info = media.probe(out)
    assert (info["width"], info["height"]) == (360, 640)
    assert info["has_audio"]
    assert abs(info["duration"] - 3) < 0.3
    assert not list(tmp_path.glob("*.ass"))  # temp subtitle file cleaned up
