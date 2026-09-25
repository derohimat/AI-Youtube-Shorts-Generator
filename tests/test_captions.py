from Components import captions

WORDS = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("This is huge. Really big news today".split())]


def test_build_lines_breaks_on_punctuation_and_length():
    lines = captions.build_lines(WORDS, max_words=3, max_chars=18)
    assert [l["text"] for l in lines] == ["This is huge.", "Really big news", "today"]
    assert lines[0]["start"] == 0.0 and lines[0]["end"] == 1.4


def test_clip_words_are_relative_to_clip():
    transcript = {"segments": [{"text": "a b c", "start": 10, "end": 13, "words": [
        {"w": "a", "s": 10.0, "e": 10.5}, {"w": "b", "s": 11.0, "e": 11.5}, {"w": "c", "s": 12.5, "e": 13.0}]}]}
    words = captions.clip_words(transcript, 10.8, 12.0)
    assert words == [{"w": "b", "s": 0.2, "e": 0.7}]


def test_ass_has_highlight_events_and_uppercase():
    lines = captions.build_lines(WORDS)
    ass = captions.build_ass(lines, "bold-yellow", 1080, 1920)
    assert "PlayResY: 1920" in ass
    assert "Style: Default,Anton," in ass
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == len(WORDS)          # one event per spoken word
    assert "HUGE." in ass and "\\c&H001FE8FF" in ass


def test_minimal_style_one_event_per_line():
    lines = captions.build_lines(WORDS, max_words=8, max_chars=40)
    ass = captions.build_ass(lines, "minimal")
    assert len([l for l in ass.splitlines() if l.startswith("Dialogue:")]) == len(lines)


def test_ass_time_format():
    assert captions._ass_time(3723.456) == "1:02:03.46"


def test_apply_text_edits_keeps_or_redistributes_timing():
    lines = captions.build_lines(WORDS, max_words=3, max_chars=18)
    rows = [[l["start"], l["end"], l["text"]] for l in lines]
    rows[0][2] = "This was huge."               # same word count -> original timings
    rows[1][2] = "Really big"                   # fewer words -> evenly spread
    rows[2][2] = ""                             # removed
    edited = captions.apply_text_edits(lines, rows)
    assert len(edited) == 2
    assert edited[0]["words"][1] == {"w": "was", "s": 0.5, "e": 0.9}
    assert edited[1]["words"][1]["e"] == lines[1]["end"]
