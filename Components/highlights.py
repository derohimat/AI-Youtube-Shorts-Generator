"""Pick the best short-form clips from a transcript.

The LLM proposes several ranked clips (with title, hook, hashtags...), then
every clip is snapped to word/sentence boundaries so cuts never land in the
middle of a word, and overlapping clips are removed.
"""
import json
import re
from typing import List

from pydantic import BaseModel, Field

from Components import config

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "gemini": "gemini-2.5-flash",
    "ollama": "llama3.1",
}
PROVIDERS = list(DEFAULT_MODELS) + ["heuristic"]

# Suggested models per provider for the UI dropdown: (label, model id). Any other name can be typed in.
# The "openai" provider also covers OpenAI-compatible gateways (OPENAI_BASE_URL, e.g. paas.id), which
# usually route Gemini and Claude models too; check your gateway's model list for its exact names.
MODEL_CHOICES = {
    "openai": [
        ("GPT-4o mini (fast, cheap)", "gpt-4o-mini"),
        ("GPT-4o", "gpt-4o"),
        ("GPT-4.1 mini", "gpt-4.1-mini"),
        ("Gemini 2.5 Flash (via gateway)", "gemini-2.5-flash"),
        ("Gemini 2.5 Pro (via gateway)", "gemini-2.5-pro"),
        ("Claude Sonnet 5 (via gateway)", "claude-sonnet-5"),
        ("Claude Haiku 4.5 (via gateway)", "claude-haiku-4-5-20251001"),
    ],
    "anthropic": [
        ("Claude Haiku 4.5 (fast, cheap)", "claude-haiku-4-5-20251001"),
        ("Claude Sonnet 5", "claude-sonnet-5"),
        ("Claude Opus 5.5 (best quality)", "claude-opus-5-5"),
    ],
    "gemini": [
        ("Gemini 2.5 Flash (fast, cheap)", "gemini-2.5-flash"),
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
    ],
    "ollama": [("Llama 3.1", "llama3.1"), ("Qwen 2.5", "qwen2.5"), ("Mistral", "mistral")],
    "heuristic": [("No AI model (offline heuristic)", "")],
}

# Rough budget per LLM call (characters of transcript); longer videos are chunked.
MAX_CHARS_PER_CALL = 60000
SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]]?$")


class Highlight(BaseModel):
    start: float = Field(description="Start time of the clip in seconds")
    end: float = Field(description="End time of the clip in seconds")
    title: str = Field(description="Catchy short title for the clip (max 60 characters)")
    hook: str = Field(description="The opening line that grabs attention")
    reason: str = Field(description="One sentence: why this clip will perform well")
    score: int = Field(description="Virality score from 1 (weak) to 10 (excellent)")
    description: str = Field(description="1-2 sentence description for YouTube/TikTok")
    hashtags: List[str] = Field(description="3-6 relevant hashtags without the # sign")


class HighlightList(BaseModel):
    """A list of the best short-form clips found in the transcript."""
    clips: List[Highlight]


SYSTEM_PROMPT = """You are an expert short-form video editor (YouTube Shorts, TikTok, Reels).
The user gives you a timestamped transcript of a long video. Find the {num_clips} best clips.

Rules for every clip:
- Length between {min_len} and {max_len} seconds.
- Starts with a strong hook in the first 3 seconds (a bold claim, question, surprising fact, or emotional moment). Never start with filler like "so", "um", "and".
- Is a complete thought that makes sense without the rest of the video; ends at the end of a sentence, ideally on a punchline or conclusion.
- Use the exact timestamps from the transcript. Clips must not overlap.
- Rank by how likely the clip is to go viral; give an honest score 1-10.
- Write title, description and hashtags in the same language as the transcript.
{instructions}"""


def format_transcript(segments):
    return "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)


def chunk_segments(segments, max_chars=MAX_CHARS_PER_CALL, overlap=3):
    """Split segments into chunks that fit in one LLM call (with a little overlap)."""
    chunks, current, size = [], [], 0
    for segment in segments:
        line = len(segment["text"]) + 20
        if current and size + line > max_chars:
            chunks.append(current)
            current = current[-overlap:]
            size = sum(len(s["text"]) + 20 for s in current)
        current.append(segment)
        size += line
    if current:
        chunks.append(current)
    return chunks


def _import(module, name, package):
    """Import an optional provider package with an install hint instead of a bare ImportError."""
    import importlib
    try:
        return getattr(importlib.import_module(module), name)
    except ImportError:
        raise ImportError(f"This provider needs an extra package: pip install {package} "
                          f"(or: pip install -r requirements-extra.txt)") from None


def get_llm(provider=None, model=None, temperature=None):
    """Return a LangChain chat model for the configured provider."""
    provider = (provider or config.LLM_PROVIDER).lower()
    model = model or config.LLM_MODEL or DEFAULT_MODELS.get(provider)
    temperature = temperature if temperature is not None else config.LLM_TEMPERATURE
    kwargs = {"model": model}
    if temperature is not None:
        kwargs["temperature"] = float(temperature)

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        if not config.OPENAI_API_KEY:
            raise ValueError("OpenAI API key missing: set OPENAI_API (or OPENAI_API_KEY) in .env")
        # OPENAI_BASE_URL points the OpenAI SDK at any OpenAI-compatible gateway (e.g. https://ai.paas.id).
        return ChatOpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL, **kwargs)
    if provider == "anthropic":
        ChatAnthropic = _import("langchain_anthropic", "ChatAnthropic", "langchain-anthropic")
        if not config.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key missing: set ANTHROPIC_API_KEY in .env")
        return ChatAnthropic(api_key=config.ANTHROPIC_API_KEY, max_tokens=4096, **kwargs)
    if provider == "gemini":
        ChatGoogleGenerativeAI = _import("langchain_google_genai", "ChatGoogleGenerativeAI", "langchain-google-genai")
        if not config.GOOGLE_API_KEY:
            raise ValueError("Google API key missing: set GOOGLE_API_KEY in .env")
        return ChatGoogleGenerativeAI(google_api_key=config.GOOGLE_API_KEY, **kwargs)
    if provider == "ollama":
        ChatOllama = _import("langchain_ollama", "ChatOllama", "langchain-ollama")
        return ChatOllama(base_url=config.OLLAMA_BASE_URL, **kwargs)
    raise ValueError(f"Unknown LLM provider '{provider}'. Choose one of: {', '.join(PROVIDERS)}")


# Errors that a different request format cannot fix; surface them instead of retrying.
_FATAL_ERRORS = {"AuthenticationError", "PermissionDeniedError", "NotFoundError", "RateLimitError",
                 "APIConnectionError", "APITimeoutError"}

JSON_INSTRUCTIONS = """
Reply with ONLY a JSON object, no other text, in this exact shape:
{"clips": [{"start": 12.3, "end": 55.0, "title": "...", "hook": "...", "reason": "...", "score": 8,
            "description": "...", "hashtags": ["tag1", "tag2"]}]}"""


def parse_json_clips(text):
    """Extract clip dicts from a plain-text LLM reply containing JSON."""
    text = re.sub(r"```(?:json)?", "", str(text))
    pairs = sorted((("{", "}"), ("[", "]")), key=lambda p: text.find(p[0]) if p[0] in text else len(text))
    for opener, closer in pairs:
        start, end = text.find(opener), text.rfind(closer)
        if start == -1 or end <= start:
            continue
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            continue
        clips = data.get("clips", []) if isinstance(data, dict) else data
        return [c for c in clips if isinstance(c, dict) and "start" in c and "end" in c]
    raise ValueError(f"The AI reply did not contain valid clip JSON: {str(text)[:300]}")


def ask_llm(segments, num_clips, min_len, max_len, instructions="", provider=None, model=None, llm=None):
    """Ask the LLM for highlight candidates. Returns a list of dicts (unsnapped).

    Uses tool/function calling first; models behind OpenAI-compatible gateways that don't
    support it fall back to a plain JSON reply.
    """
    provider = (provider or config.LLM_PROVIDER).lower()
    llm = llm or get_llm(provider, model)
    method = "function_calling" if provider == "openai" else None
    structured = (llm.with_structured_output(HighlightList, method=method) if method
                  else llm.with_structured_output(HighlightList))
    extra = f"\nExtra instructions from the user: {instructions.strip()}" if instructions and instructions.strip() else ""
    system = SYSTEM_PROMPT.format(num_clips=num_clips, min_len=min_len, max_len=max_len, instructions=extra)

    candidates, use_json = [], False
    for chunk in chunk_segments(segments):
        messages = [("system", system), ("user", format_transcript(chunk))]
        if not use_json:
            try:
                response = structured.invoke(messages)
                if response is not None and response.clips:
                    candidates.extend(c.model_dump() for c in response.clips)
                    continue
            except Exception as e:
                if type(e).__name__ in _FATAL_ERRORS:
                    raise
                print(f"Structured output failed ({type(e).__name__}: {e}); falling back to plain JSON")
            use_json = True
        reply = llm.invoke([("system", system + JSON_INSTRUCTIONS), ("user", format_transcript(chunk))])
        candidates.extend(parse_json_clips(getattr(reply, "content", reply)))
    return candidates


def heuristic_candidates(segments, num_clips, min_len, max_len):
    """Offline fallback (no API key): pick dense, sentence-aligned windows."""
    target = (min_len + max_len) / 2
    candidates = []
    for i, first in enumerate(segments):
        text, end = [], first["start"]
        for segment in segments[i:]:
            if segment["end"] - first["start"] > max_len:
                break
            text.append(segment["text"])
            end = segment["end"]
            if end - first["start"] >= target and SENTENCE_END.search(segment["text"]):
                break
        length = end - first["start"]
        if length < min_len:
            continue
        joined = " ".join(text)
        words = len(joined.split())
        score = words / max(length, 1) + joined.count("?") * 0.3 + joined.count("!") * 0.3
        title = " ".join(first["text"].split()[:8]).rstrip(",.")
        candidates.append({
            "start": first["start"], "end": end, "title": title, "hook": first["text"],
            "reason": "Dense, uninterrupted speech segment", "score": score,
            "description": joined[:200], "hashtags": ["shorts"],
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = max((c["score"] for c in candidates), default=1) or 1
    for c in candidates:
        c["score"] = max(1, min(10, round(10 * c["score"] / best)))
    return candidates[: num_clips * 5]


def all_words(segments):
    """Flatten words, flagging sentence and (Whisper) segment boundaries."""
    words = []
    for segment in segments:
        seg_words = segment.get("words") or [{"w": segment["text"], "s": segment["start"], "e": segment["end"]}]
        for i, word in enumerate(seg_words):
            words.append({**word, "sentence_end": bool(SENTENCE_END.search(word["w"])),
                          "segment_start": i == 0, "segment_end": i == len(seg_words) - 1})
    for i, word in enumerate(words):
        word["sentence_start"] = i == 0 or words[i - 1]["sentence_end"]
    return words


def snap_clip(start, end, words, duration, max_len=None, window=1.5, pad_start=0.15, pad_end=0.3):
    """Move start/end to clean boundaries: sentence > Whisper segment > nearest word (never mid-word)."""
    if not words:
        return max(0.0, start), min(duration, end)

    def pick(candidates, key, target, flags):
        near = [w for w in candidates if abs(w[key] - target) <= window]
        for flag in flags:
            flagged = [w for w in near if w[flag]]
            if flagged:
                return min(flagged, key=lambda w: abs(w[key] - target))
        return min(candidates, key=lambda w: abs(w[key] - target))

    new_start = pick(words, "s", start, ("sentence_start", "segment_start"))["s"]

    limit = new_start + max_len if max_len else float("inf")
    after = [w for w in words if new_start < w["e"] <= limit + 0.01]
    if not after:
        after = [w for w in words if w["e"] > new_start] or [words[-1]]
    new_end = pick(after, "e", min(end, limit), ("sentence_end", "segment_end"))["e"]

    new_start = max(0.0, new_start - pad_start)
    new_end = min(duration, new_end + pad_end) if duration else new_end + pad_end
    return round(new_start, 2), round(new_end, 2)


def remove_overlaps(clips, max_overlap=0.2):
    """Keep the best-scored clips whose overlap with already kept clips is small."""
    kept = []
    for clip in sorted(clips, key=lambda c: c["score"], reverse=True):
        length = clip["end"] - clip["start"]
        if length <= 0:
            continue
        ok = True
        for other in kept:
            overlap = min(clip["end"], other["end"]) - max(clip["start"], other["start"])
            if overlap > max_overlap * min(length, other["end"] - other["start"]):
                ok = False
                break
        if ok:
            kept.append(clip)
    return kept


def normalize_hashtags(tags):
    cleaned = []
    for tag in tags or []:
        tag = re.sub(r"[^\w]", "", str(tag).lstrip("#"))
        if tag and tag.lower() not in (t.lower() for t in cleaned):
            cleaned.append(tag)
    return cleaned


def find_highlights(transcript, duration, num_clips=5, min_len=20, max_len=60, instructions="",
                    provider=None, model=None, exclude=None, llm=None):
    """Return up to `num_clips` snapped, non-overlapping, ranked clips."""
    segments = transcript["segments"]
    if not segments:
        return []
    provider = (provider or config.LLM_PROVIDER).lower()
    if exclude:
        instructions = (instructions or "") + "\nDo NOT pick these time ranges again: " + ", ".join(
            f"{a:.0f}-{b:.0f}s" for a, b in exclude)

    if provider == "heuristic":
        candidates = heuristic_candidates(segments, num_clips, min_len, max_len)
    else:
        candidates = ask_llm(segments, num_clips, min_len, max_len, instructions, provider, model, llm=llm)

    words = all_words(segments)
    clips = []
    for c in candidates:
        try:
            start, end = float(c["start"]), float(c["end"])
        except (TypeError, ValueError, KeyError):
            continue
        if end <= start or start >= (duration or float("inf")):
            continue
        start, end = snap_clip(start, end, words, duration, max_len=max_len + 5)
        if end - start < min(5, min_len):
            continue
        clips.append({
            "start": start, "end": end,
            "title": (c.get("title") or "Clip").strip()[:80],
            "hook": (c.get("hook") or "").strip(),
            "reason": (c.get("reason") or "").strip(),
            "score": int(max(1, min(10, round(float(c.get("score") or 5))))),
            "description": (c.get("description") or "").strip(),
            "hashtags": normalize_hashtags(c.get("hashtags")),
        })
    if exclude:
        clips = [c for c in clips if not any(min(c["end"], b) - max(c["start"], a) > 0.5 * (c["end"] - c["start"])
                                             for a, b in exclude)]
    clips = remove_overlaps(clips)[:num_clips]
    for i, clip in enumerate(clips, 1):
        clip["id"] = i
    return clips
