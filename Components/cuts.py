"""Tighten a clip by cutting silences and filler words ("um", "eh", ...).

All times here are relative to the clip start. `cut_segments` returns the parts to keep;
the other helpers move caption words and camera keyframes onto the shortened timeline.
"""
import re

# Only sounds that never carry meaning; real words ("apa", "like", "so") are never cut.
FILLERS = {
    "en": {"um", "uh", "uhm", "umm", "erm", "er", "ah", "hmm", "mm", "mhm"},
    "id": {"eh", "em", "emm", "ehm", "hmm", "ee", "eee", "anu"},
}


def _norm(word):
    return re.sub(r"[^\w]", "", word.lower())


def _is_filler(words, i, language):
    fillers = FILLERS.get((language or "en")[:2], set()) | FILLERS["en"]
    return _norm(words[i]["w"]) in fillers


def cut_segments(words, duration, remove_silence=True, remove_fillers=True, language="en",
                 max_gap=0.6, pad=0.15, min_len=0.12):
    """Return the (start, end) ranges to keep, relative to the clip start."""
    if not words or not (remove_silence or remove_fillers):
        return [(0.0, round(duration, 3))]
    fillers = [(w["s"], w["e"]) for i, w in enumerate(words) if remove_fillers and _is_filler(words, i, language)]
    kept = [w for i, w in enumerate(words) if not (remove_fillers and _is_filler(words, i, language))]
    if not kept:
        return [(0.0, round(duration, 3))]
    if remove_silence:
        segments = []
        for w in kept:
            start, end = max(0.0, w["s"] - pad), min(duration, w["e"] + pad)
            if segments and start - segments[-1][1] <= max(0.0, max_gap - 2 * pad):
                segments[-1][1] = max(segments[-1][1], end)
            else:
                segments.append([start, end])
    else:
        segments = [[0.0, duration]]
    # Always cut the filler words themselves, even when they sit between words with no pause.
    for fs, fe in fillers:
        result = []
        for a, b in segments:
            if fe <= a or fs >= b:
                result.append([a, b])
                continue
            if fs > a:
                result.append([a, fs])
            if fe < b:
                result.append([fe, b])
        segments = result
    return [(round(a, 3), round(b, 3)) for a, b in segments if b - a >= min_len]


def output_duration(segments):
    return sum(b - a for a, b in segments)


def remap_time(t, segments):
    """Clip time -> time in the shortened output (times inside a cut snap to the next kept part)."""
    offset = 0.0
    for a, b in segments:
        if t < a:
            return round(offset, 3)
        if t <= b:
            return round(offset + t - a, 3)
        offset += b - a
    return round(offset, 3)


def remap_lines(lines, segments, drop_fillers=False, language="en"):
    """Move caption lines onto the shortened timeline; words that were cut out disappear."""
    result = []
    for line in lines:
        words = line.get("words") or [{"w": line["text"], "s": line["start"], "e": line["end"]}]
        new_words = []
        for i, w in enumerate(words):
            if drop_fillers and _is_filler(words, i, language):
                continue
            s, e = remap_time(w["s"], segments), remap_time(w["e"], segments)
            if e - s < 0.02:  # entirely inside a cut
                continue
            new_words.append({"w": w["w"], "s": s, "e": e})
        if new_words:
            result.append({"start": new_words[0]["s"], "end": new_words[-1]["e"],
                           "text": " ".join(w["w"] for w in new_words), "words": new_words})
    return result


def remap_keyframes(keyframes, segments):
    out = []
    for t, x in keyframes:
        t = remap_time(t, segments)
        if out and t <= out[-1][0]:
            out[-1] = (out[-1][0], x)
        else:
            out.append((t, x))
    return out
