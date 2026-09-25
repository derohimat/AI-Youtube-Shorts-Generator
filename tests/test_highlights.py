from Components import highlights

SEGMENTS = [
    {"text": "So, um, welcome back.", "start": 0.0, "end": 2.0, "words": [
        {"w": "So,", "s": 0.0, "e": 0.3}, {"w": "um,", "s": 0.4, "e": 0.7},
        {"w": "welcome", "s": 0.8, "e": 1.4}, {"w": "back.", "s": 1.5, "e": 2.0}]},
    {"text": "Nobody tells you this secret.", "start": 2.5, "end": 5.0, "words": [
        {"w": "Nobody", "s": 2.5, "e": 3.0}, {"w": "tells", "s": 3.1, "e": 3.4}, {"w": "you", "s": 3.5, "e": 3.7},
        {"w": "this", "s": 3.8, "e": 4.1}, {"w": "secret.", "s": 4.2, "e": 5.0}]},
    {"text": "It changed everything for me", "start": 5.2, "end": 8.0, "words": [
        {"w": "It", "s": 5.2, "e": 5.4}, {"w": "changed", "s": 5.5, "e": 6.0}, {"w": "everything", "s": 6.1, "e": 6.9},
        {"w": "for", "s": 7.0, "e": 7.3}, {"w": "me", "s": 7.4, "e": 8.0}]},
]


def test_snap_prefers_sentence_boundaries():
    words = highlights.all_words(SEGMENTS)
    start, end = highlights.snap_clip(3.2, 7.1, words, duration=8.0, pad_start=0, pad_end=0)
    assert start == 2.5          # snapped back to the sentence start "Nobody"
    assert end == 8.0            # end of the spoken segment, not mid-thought on "for"


def test_snap_never_cuts_mid_word_and_clamps():
    words = highlights.all_words(SEGMENTS)
    start, end = highlights.snap_clip(0.0, 20.0, words, duration=8.0)
    assert start == 0.0          # clips starting at 0 are valid
    assert end == 8.0


def test_snap_prefers_nearby_sentence_end():
    words = highlights.all_words(SEGMENTS)
    _, end = highlights.snap_clip(2.5, 5.6, words, duration=8.0, pad_start=0, pad_end=0)
    assert end == 5.0            # "secret." beats the closer word "It"


def test_snap_respects_max_len():
    words = highlights.all_words(SEGMENTS)
    start, end = highlights.snap_clip(2.5, 8.0, words, duration=8.0, max_len=3, pad_start=0, pad_end=0)
    assert end - start <= 3.0


def test_remove_overlaps_keeps_best():
    clips = [{"start": 0, "end": 10, "score": 5}, {"start": 2, "end": 12, "score": 9},
             {"start": 20, "end": 30, "score": 3}]
    kept = highlights.remove_overlaps(clips)
    assert [c["score"] for c in kept] == [9, 3]


def test_normalize_hashtags():
    assert highlights.normalize_hashtags(["#AI", "ai", "life hacks", ""]) == ["AI", "lifehacks"]


def test_chunking_splits_long_transcripts():
    segments = [{"text": "x" * 100, "start": i, "end": i + 1} for i in range(100)]
    chunks = highlights.chunk_segments(segments, max_chars=2000)
    assert len(chunks) > 1
    assert chunks[0][0]["start"] == 0 and chunks[-1][-1]["start"] == 99


class FakeStructured:
    def __init__(self, response):
        self.response = response

    def invoke(self, messages):
        return self.response


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def with_structured_output(self, schema, method=None):
        return FakeStructured(self.response)


def test_find_highlights_with_stub_llm():
    response = highlights.HighlightList(clips=[
        highlights.Highlight(start=2.4, end=7.9, title="The secret", hook="Nobody tells you this",
                             reason="curiosity", score=9, description="d", hashtags=["#Secret", "tips"]),
        highlights.Highlight(start=3.0, end=7.5, title="Duplicate", hook="", reason="", score=4,
                             description="", hashtags=[]),
        highlights.Highlight(start=9.0, end=5.0, title="Invalid", hook="", reason="", score=10,
                             description="", hashtags=[]),
    ])
    clips = highlights.find_highlights({"segments": SEGMENTS}, 8.0, num_clips=3, min_len=3, max_len=10,
                                       provider="openai", llm=FakeLLM(response))
    assert len(clips) == 1
    assert clips[0]["title"] == "The secret"
    assert clips[0]["hashtags"] == ["Secret", "tips"]
    assert clips[0]["id"] == 1


def test_heuristic_provider_works_offline():
    clips = highlights.find_highlights({"segments": SEGMENTS}, 8.0, num_clips=2, min_len=3, max_len=6,
                                       provider="heuristic")
    assert clips and all(c["end"] - c["start"] <= 6 + 5 for c in clips)
