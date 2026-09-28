import shutil

import pytest

from Components import captions, cuts, framing, media, render

W = lambda w, s, e: {"w": w, "s": s, "e": e}
WORDS = [W("So", 0.5, 0.8), W("um,", 0.9, 1.2), W("this", 1.3, 1.6), W("works.", 1.7, 2.2),
         W("Really", 4.0, 4.4), W("well.", 4.5, 5.0)]


def test_cut_segments_removes_silence_and_fillers():
    segs = cuts.cut_segments(WORDS, duration=6.0, remove_silence=True, remove_fillers=True)
    # "um" (0.9-1.2) cut even without pauses around it, the 1.8s pause before "Really" gone,
    # leading/trailing silence trimmed
    assert segs == [(0.35, 0.9), (1.2, 2.35), (3.85, 5.15)]
    assert cuts.output_duration(segs) == pytest.approx(3.0)


def test_silence_only_keeps_fillers_and_short_gaps():
    segs = cuts.cut_segments(WORDS, duration=6.0, remove_silence=True, remove_fillers=False)
    assert segs == [(0.35, 2.35), (3.85, 5.15)]


def test_fillers_only_keeps_pauses():
    segs = cuts.cut_segments(WORDS, duration=6.0, remove_silence=False, remove_fillers=True)
    assert segs == [(0.0, 0.9), (1.2, 6.0)]


def test_nothing_to_cut_returns_whole_clip():
    assert cuts.cut_segments(WORDS, 6.0, False, False) == [(0.0, 6.0)]
    assert cuts.cut_segments([], 6.0) == [(0.0, 6.0)]


def test_indonesian_fillers_but_never_real_words():
    words = [W("Eh", 0.0, 0.3), W("apa", 0.4, 0.7), W("anu", 0.8, 1.0), W("kabar?", 1.1, 1.5)]
    kept = [w["w"] for i, w in enumerate(words) if not cuts._is_filler(words, i, "id")]
    assert kept == ["apa", "kabar?"]


def test_remap_time_and_lines():
    segs = [(0.35, 0.9), (1.2, 2.35), (3.85, 5.15)]
    assert cuts.remap_time(0.35, segs) == 0.0
    assert cuts.remap_time(1.0, segs) == pytest.approx(0.55)   # inside a cut -> start of next part
    assert cuts.remap_time(4.0, segs) == pytest.approx(1.85)
    lines = captions.build_lines(WORDS, max_words=3, max_chars=30)
    out = cuts.remap_lines(lines, segs, drop_fillers=True, language="en")
    texts = " ".join(l["text"] for l in out)
    assert "um" not in texts and "Really well." in texts
    assert all(l["end"] <= cuts.output_duration(segs) + 1e-6 for l in out)


def test_remap_keyframes_merges_collapsed_times():
    segs = [(0.0, 1.0), (3.0, 4.0)]
    assert cuts.remap_keyframes([(0.0, 10), (1.5, 20), (2.5, 30), (3.5, 40)], segs) == [(0.0, 10), (1.0, 30), (1.5, 40)]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_with_cuts_hook_and_keywords(tmp_path):
    src = tmp_path / "src.mp4"
    media.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6",
                      "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-c:a", "aac",
                      "-shortest", src])
    segs = cuts.cut_segments(WORDS, 6.0)
    plan = framing.plan_framing(str(src), 0, 6, "center", (640, 360))
    plan["keyframes"] = cuts.remap_keyframes(plan["keyframes"], segs)
    lines = cuts.remap_lines(captions.build_lines(WORDS), segs, drop_fillers=True)
    out = render.render_short(str(src), 0, 6, plan, str(tmp_path / "out.mp4"), caption_lines=lines,
                              size=(360, 640), segments=segs, hook={"text": "Hook!", "duration": 2},
                              keywords=["works"])
    info = media.probe(out)
    assert (info["width"], info["height"]) == (360, 640) and info["has_audio"]
    assert info["duration"] == pytest.approx(cuts.output_duration(segs), abs=0.15)
