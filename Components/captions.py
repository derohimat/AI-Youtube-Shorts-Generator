"""Viral-style word-by-word captions rendered as an ASS subtitle file.

Captions are handled as editable "lines" (a few words each):
    [{"start": 0.0, "end": 1.2, "text": "THIS IS HOW", "words": [{"w", "s", "e"}, ...]}]
Times are relative to the clip start. The ASS file is burned in by ffmpeg/libass,
so no ImageMagick is needed.
"""
import re

# Colours are RGB hex strings; converted to ASS &HAABBGGRR format below.
PRESETS = {
    "bold-yellow": {
        "label": "Bold yellow (word highlight)", "font": "Anton", "size": 0.072,
        "color": "FFFFFF", "highlight": "FFE81F", "outline": "000000", "outline_w": 0.0045,
        "shadow": 0.002, "box": False, "uppercase": True, "max_words": 3, "max_chars": 18,
        "position": 0.30, "pop": True,
    },
    "clean-white": {
        "label": "Clean white (green highlight)", "font": "Anton", "size": 0.062,
        "color": "FFFFFF", "highlight": "3DFF6E", "outline": "000000", "outline_w": 0.0035,
        "shadow": 0.0, "box": False, "uppercase": False, "max_words": 4, "max_chars": 24,
        "position": 0.30, "pop": False,
    },
    "boxed": {
        "label": "Boxed (white on black)", "font": "Anton", "size": 0.052,
        "color": "FFFFFF", "highlight": "FFD400", "outline": "000000", "outline_w": 0.006,
        "shadow": 0.0, "box": True, "uppercase": False, "max_words": 5, "max_chars": 28,
        "position": 0.28, "pop": False,
    },
    "minimal": {
        "label": "Minimal (sentence, no highlight)", "font": "DejaVu Sans", "size": 0.036,
        "color": "FFFFFF", "highlight": None, "outline": "000000", "outline_w": 0.0025,
        "shadow": 0.0015, "box": False, "uppercase": False, "max_words": 8, "max_chars": 40,
        "position": 0.18, "pop": False,
    },
    "none": {"label": "No captions"},
}
DEFAULT_PRESET = "bold-yellow"


def build_lines(words, max_words=3, max_chars=18, max_gap=0.6):
    """Group timed words into short caption lines."""
    lines, current = [], []

    def flush():
        if current:
            lines.append({"start": current[0]["s"], "end": current[-1]["e"],
                          "text": " ".join(w["w"] for w in current), "words": list(current)})
            current.clear()

    for word in words:
        if current:
            text_len = len(" ".join(w["w"] for w in current)) + 1 + len(word["w"])
            if (len(current) >= max_words or text_len > max_chars
                    or word["s"] - current[-1]["e"] > max_gap
                    or re.search(r"[.!?,;:…]$", current[-1]["w"])):
                flush()
        current.append(word)
    flush()
    return lines


def clip_words(transcript, start, end):
    """Words inside [start, end], re-timed relative to `start`."""
    words = []
    for segment in transcript["segments"]:
        if segment["end"] < start or segment["start"] > end:
            continue
        seg_words = segment.get("words") or [{"w": segment["text"], "s": segment["start"], "e": segment["end"]}]
        for w in seg_words:
            mid = (w["s"] + w["e"]) / 2
            if start <= mid <= end:
                words.append({"w": w["w"], "s": round(max(0.0, w["s"] - start), 3),
                              "e": round(min(end, w["e"]) - start, 3)})
    return words


def lines_for_clip(transcript, start, end, preset=DEFAULT_PRESET):
    style = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    return build_lines(clip_words(transcript, start, end),
                       max_words=style.get("max_words", 3), max_chars=style.get("max_chars", 18))


def apply_text_edits(lines, edited_rows):
    """Apply edited [start, end, text] rows (from the UI table) back onto lines.

    If a line keeps the same number of words the original word timings are kept,
    otherwise the line's duration is spread evenly across the new words.
    """
    result = []
    for i, row in enumerate(edited_rows):
        start, end, text = float(row[0]), float(row[1]), str(row[2] or "").strip()
        if not text or end <= start:
            continue
        new_words = text.split()
        original = lines[i]["words"] if i < len(lines) else []
        if len(original) == len(new_words) and original and abs(original[0]["s"] - start) < 0.01:
            words = [{"w": nw, "s": ow["s"], "e": ow["e"]} for nw, ow in zip(new_words, original)]
        else:
            step = (end - start) / len(new_words)
            words = [{"w": nw, "s": round(start + k * step, 3), "e": round(start + (k + 1) * step, 3)}
                     for k, nw in enumerate(new_words)]
        result.append({"start": start, "end": end, "text": text, "words": words})
    return result


def _ass_color(rgb, alpha=0):
    rgb = rgb.lstrip("#")
    return f"&H{alpha:02X}{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}".upper()


def _ass_time(seconds):
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(lines, preset=DEFAULT_PRESET, width=1080, height=1920):
    """Return the ASS document text for the given caption lines."""
    style = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    size = round(height * style["size"])
    outline = max(1, round(height * style["outline_w"]))
    shadow = round(height * style["shadow"])
    margin_v = round(height * style["position"])
    border_style = 3 if style["box"] else 1
    back = _ass_color("000000", 0x30) if style["box"] else _ass_color("000000", 0x80)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style['font']},{size},{_ass_color(style['color'])},{_ass_color(style['color'])},{_ass_color(style['outline'])},{back},-1,0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,{round(width * 0.08)},{round(width * 0.08)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for line in lines:
        words = line.get("words") or [{"w": line["text"], "s": line["start"], "e": line["end"]}]
        texts = [_escape(w["w"].upper() if style["uppercase"] else w["w"]) for w in words]
        pop = r"{\fscx80\fscy80\t(0,90,\fscx100\fscy100)}" if style["pop"] else ""
        if not style["highlight"]:
            events.append((line["start"], line["end"], pop + " ".join(texts)))
            continue
        # One event per word so the currently spoken word is highlighted.
        for k, word in enumerate(words):
            start = line["start"] if k == 0 else word["s"]
            end = words[k + 1]["s"] if k + 1 < len(words) else line["end"]
            if end <= start:
                continue
            hl = _ass_color(style["highlight"])
            parts = [f"{{\\c{hl}}}{t}{{\\r}}" if j == k else t for j, t in enumerate(texts)]
            events.append((start, end, (pop if k == 0 else "") + " ".join(parts)))
    body = "\n".join(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Default,,0,0,0,,{t}" for s, e, t in events)
    return header + body + "\n"


def write_ass(path, lines, preset=DEFAULT_PRESET, width=1080, height=1920):
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_ass(lines, preset, width, height))
    return path
