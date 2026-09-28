import json
import os
import shutil

import pytest

from Components import captions, framing, media, pipeline

FFMPEG = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_format_sizes():
    assert pipeline.format_size("1:1") == (1080, 1080)
    assert pipeline.format_size("4:5") == (1080, 1350)
    assert pipeline.format_size("4:5", width=360) == (360, 450)
    assert pipeline._formats({"formats": ["1:1", "bogus", "1:1"]}) == ["1:1"]
    assert pipeline._formats({}) == ["9:16"]


def test_srt_output():
    lines = [{"start": 0.0, "end": 1.25, "text": "Hello there"}, {"start": 61.5, "end": 62.0, "text": "Bye"}]
    assert captions.build_srt(lines) == ("1\n00:00:00,000 --> 00:00:01,250\nHello there\n\n"
                                         "2\n00:01:01,500 --> 00:01:02,000\nBye\n")


def test_caption_size_scales_with_width_not_height():
    words = [{"w": "hi", "s": 0, "e": 1}]
    tall = captions.build_ass(captions.build_lines(words), width=1080, height=1920)
    square = captions.build_ass(captions.build_lines(words), width=1080, height=1080)
    size = lambda ass: ass.split("Style: Default,")[1].split(",")[1]  # noqa: E731
    assert size(tall) == size(square)


def test_crop_width_follows_aspect(monkeypatch):
    monkeypatch.setattr(framing, "_cached_samples", lambda *a: ([], (1920, 1080)))
    assert framing.plan_framing("x.mp4", 0, 5, "center", (1920, 1080))["crop_w"] == 606
    assert framing.plan_framing("x.mp4", 0, 5, "center", (1920, 1080), aspect=1.0)["crop_w"] == 1080
    assert framing.plan_framing("x.mp4", 0, 5, "center", (1920, 1080), aspect=0.8)["crop_w"] == 864
    # a 9:16 source is already narrower than 1:1 -> fit it on a blurred background
    assert framing.plan_framing("x.mp4", 0, 5, "center", (1080, 1920), aspect=1.0)["mode"] == "fit-blur"


@FFMPEG
def test_render_clip_in_three_formats_with_srt(tmp_path, monkeypatch):
    monkeypatch.setattr("Components.config.WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr("Components.config.OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr("Components.config.OUTPUT_WIDTH", 360)
    monkeypatch.setattr("Components.config.OUTPUT_HEIGHT", 640)
    src = tmp_path / "talk.mp4"
    media.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6",
                      "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-c:a", "aac",
                      "-shortest", src])
    words = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("one two three four five".split())]
    work_dir = tmp_path / "work" / "aaaaaaaa"
    os.makedirs(work_dir)
    project = {"id": "aaaaaaaa", "title": "Talk", "slug": "talk", "video_path": str(src), "work_dir": str(work_dir),
               "duration": 6.0, "width": 640, "height": 360, "fps": 25.0, "has_audio": True,
               "transcript": {"language": "en", "segments": [{"text": "x", "start": 0, "end": 2.4, "words": words}]}}
    clip = {"id": 1, "start": 0.0, "end": 3.0, "title": "Numbers", "score": 8, "keep": True}
    result = pipeline.render_clip(project, clip, {"formats": ["9:16", "1:1", "4:5"], "framing": "center"})
    sizes = [(media.probe(v)["width"], media.probe(v)["height"]) for v in result["videos"]]
    assert sizes == [(360, 640), (360, 360), (360, 450)]
    assert [os.path.basename(v) for v in result["videos"]] == ["01-numbers.mp4", "01-numbers_1x1.mp4",
                                                              "01-numbers_4x5.mp4"]
    srt = open(result["srt"], encoding="utf-8").read()
    assert srt.startswith("1\n00:00:00,") and "one" in srt
    session = pipeline.save_session(project, render_results=[result])
    assert session["renders"][0]["videos"] == result["videos"]
    json.dumps(session)  # stays serialisable


def test_analyze_project_auto_render(tmp_path, monkeypatch):
    calls = []
    project = {"work_dir": str(tmp_path)}
    monkeypatch.setattr(pipeline, "prepare", lambda *a, **k: project)
    monkeypatch.setattr(pipeline, "suggest_clips", lambda *a, **k: [{"id": 1, "start": 0, "end": 5, "score": 9}])
    monkeypatch.setattr(pipeline, "preview_clip", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "save_session", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "render_project", lambda p, progress=None, **o: calls.append(o))
    progress = []
    pipeline.analyze_project("x.mp4", auto_render=True, render_opts={"formats": ["1:1"]},
                             progress=lambda f, m: progress.append(f))
    assert calls == [{"formats": ["1:1"]}]
    assert max(progress) <= 0.5 + 1e-9  # analysis reports the first half; the render the second
