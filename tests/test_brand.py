import os
import shutil

import pytest

from Components import brand, captions, framing, media, pipeline, render

FFMPEG = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr("Components.config.WORK_DIR", str(tmp_path / "work"))
    os.makedirs(tmp_path / "work")
    return tmp_path


def test_brand_defaults_and_updates(work):
    assert brand.load()["logo"]["position"] == "top-right"
    assert brand.render_kwargs() == {}
    brand.update("colors", enabled=True, color="#112233", highlight="rgba(255, 0, 0, 1)", keyword="bad")
    assert brand.render_kwargs()["colors"] == {"color": "112233", "highlight": "FF0000"}
    assert brand.render_kwargs(use=False) == {}


def test_brand_font_upload_reads_family_and_bundles_fonts(work):
    data = brand.set_font(os.path.join(os.path.dirname(__file__), "..", "fonts", "Anton-Regular.ttf"))
    assert data["font"]["family"] == "Anton"
    kwargs = brand.render_kwargs()
    assert kwargs["font"] == "Anton" and os.path.isdir(kwargs["fonts_dir"])
    assert "Anton-Regular.ttf" in os.listdir(kwargs["fonts_dir"])
    brand.set_font(None)
    assert "font" not in brand.render_kwargs()


def test_ass_font_and_colour_overrides():
    words = [{"w": "hello", "s": 0, "e": 0.5}, {"w": "world", "s": 0.6, "e": 1.0}]
    ass = captions.build_ass(captions.build_lines(words), "bold-yellow", font="MyBrand",
                             colors={"color": "112233", "highlight": "FF0000"})
    assert "Style: Default,MyBrand," in ass and "&H00332211" in ass and "\\c&H000000FF" in ass


def test_framing_offset_shifts_and_clamps():
    plan = {"src_w": 1920, "crop_w": 608, "keyframes": [(0.0, 100), (2.0, 1200)]}
    assert pipeline._shift(plan, 0) == [(0.0, 100), (2.0, 1200)]
    assert pipeline._shift(plan, 50) == [(0.0, 404), (2.0, 1312)]      # 1504+ clamps to max_x=1312
    assert pipeline._shift(plan, -50) == [(0.0, 0), (2.0, 896)]


@FFMPEG
def test_render_with_logo_and_ducked_music(work, tmp_path):
    src, logo, music = tmp_path / "src.mp4", tmp_path / "logo.png", tmp_path / "music.mp3"
    media.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4",
                      "-f", "lavfi", "-i", "sine=frequency=300:duration=4", "-c:v", "libx264", "-c:a", "aac",
                      "-shortest", src])
    media.run_ffmpeg(["-f", "lavfi", "-i", "color=c=red@0.5:s=200x100,format=rgba", "-frames:v", 1, logo])
    media.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=880:duration=1.5", music])  # shorter: must loop
    brand.set_logo(str(logo))
    brand.set_music(str(music))
    brand.update("logo", position="bottom-left", size=0.25)
    kwargs = brand.render_kwargs()
    assert {"logo", "music"} <= set(kwargs)
    plan = framing.plan_framing(str(src), 0, 4, "fit-blur", (640, 360))
    out = render.render_short(str(src), 0, 4, plan, str(tmp_path / "out.mp4"), size=(360, 640),
                              caption_lines=[], **kwargs)
    info = media.probe(out)
    assert info["has_audio"] and info["duration"] == pytest.approx(4, abs=0.2)
    still = render.render_still(str(src), 0, 4, plan, str(tmp_path / "still.jpg"), size=(360, 640),
                                logo=kwargs["logo"])
    assert os.path.getsize(still) > 0
