"""High-level pipeline shared by the web UI (app.py) and the CLI (main.py).

    project = prepare("https://youtu.be/...")          # download + transcribe (cached)
    clips = suggest_clips(project, num_clips=5)         # AI picks ranked highlights
    result = render_clip(project, clips[0], style="bold-yellow", framing="auto")

No function here prompts the user; progress is reported via an optional
`progress(fraction, message)` callback.
"""
import hashlib
import json
import os
import re

from Components import config, media
from Components.captions import DEFAULT_PRESET, lines_for_clip
from Components.framing import plan_framing
from Components.highlights import all_words, find_highlights, snap_clip
from Components.render import render_preview, render_short, render_still, thumbnail


def _noop(fraction, message):
    print(f"[{fraction:>4.0%}] {message}")


def clean_filename(title, max_len=80):
    """Slugify a title for use in file names."""
    cleaned = title.lower()
    cleaned = re.sub(r'[<>:"/\\|?*\[\]\'#%&{}$!@+=`]', "", cleaned)
    cleaned = re.sub(r"[\s_.,;]+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:max_len] or "video"


def parse_time(value):
    """Accept seconds (12.5) or timestamps ("1:02.5", "01:02:03")."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part or 0)
    return seconds


def format_time(seconds):
    minutes, secs = divmod(max(0.0, float(seconds)), 60)
    return f"{int(minutes)}:{secs:04.1f}"


def _is_url(source):
    return bool(re.match(r"^https?://", source.strip()))


def _project_dir(source):
    key = source.strip() if _is_url(source) else os.path.abspath(source)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return os.path.join(config.WORK_DIR, digest), digest


def load_project(project_dir):
    with open(os.path.join(project_dir, "project.json"), encoding="utf-8") as f:
        return json.load(f)


def _save_project(project):
    with open(os.path.join(project["work_dir"], "project.json"), "w", encoding="utf-8") as f:
        json.dump(project, f, ensure_ascii=False, indent=1)


def prepare(source, progress=None, language=None, whisper_model=None, force=False):
    """Download (if URL) and transcribe `source`. Results are cached per source."""
    progress = progress or _noop
    source = source.strip().strip('"').strip("'")
    work_dir, project_id = _project_dir(source)
    cache = os.path.join(work_dir, "project.json")
    if os.path.exists(cache) and not force:
        project = load_project(work_dir)
        if os.path.exists(project["video_path"]):
            progress(1.0, "Loaded cached transcript")
            return project
    os.makedirs(work_dir, exist_ok=True)

    if _is_url(source):
        progress(0.05, "Downloading video...")
        from Components.YoutubeDownloader import download_youtube_video
        video_path, title = download_youtube_video(source, output_dir=work_dir)
    elif os.path.isfile(source):
        video_path, title = os.path.abspath(source), os.path.splitext(os.path.basename(source))[0]
    else:
        raise FileNotFoundError(f"Not a URL or existing file: {source}")

    info = media.probe(video_path)
    if not info["has_audio"]:
        raise ValueError("The video has no audio track, so it cannot be transcribed.")

    progress(0.35, "Extracting audio...")
    audio_path = media.extract_audio(video_path, os.path.join(work_dir, "audio.wav"))
    progress(0.45, "Transcribing speech (this is the slow part)...")
    from Components.Transcription import transcribeAudio
    transcript = transcribeAudio(audio_path, model_size=whisper_model, language=language)
    os.remove(audio_path)

    project = {
        "id": project_id, "source": source, "title": title, "slug": clean_filename(title),
        "video_path": video_path, "work_dir": work_dir, **info, "transcript": transcript,
    }
    _save_project(project)
    progress(1.0, f"Transcribed {len(transcript['segments'])} segments ({transcript['language']})")
    return project


def suggest_clips(project, num_clips=5, min_len=20, max_len=60, instructions="", provider=None,
                  model=None, exclude=None, progress=None):
    """Ask the AI for the best clips. Returns a list of clip dicts sorted by score."""
    progress = progress or _noop
    progress(0.1, f"Finding the {num_clips} best moments...")
    clips = find_highlights(project["transcript"], project["duration"], num_clips=num_clips,
                            min_len=min_len, max_len=max_len, instructions=instructions,
                            provider=provider, model=model, exclude=exclude)
    progress(1.0, f"Found {len(clips)} clips")
    return clips


def adjust_clip(project, clip, start, end, snap=True):
    """Return a copy of `clip` with new start/end, optionally snapped to word boundaries."""
    start, end = parse_time(start), parse_time(end)
    start, end = max(0.0, start), min(project["duration"], end)
    if end <= start:
        raise ValueError(f"Clip '{clip.get('title')}': end ({end}s) must be after start ({start}s)")
    if snap:
        start, end = snap_clip(start, end, all_words(project["transcript"]["segments"]),
                               project["duration"], window=1.0)
    return {**clip, "start": start, "end": end}


def caption_lines(project, clip, style=DEFAULT_PRESET):
    return lines_for_clip(project["transcript"], clip["start"], clip["end"], style)


def preview_clip(project, clip):
    """Quick low-res preview of the clip's time range (no framing/captions)."""
    path = os.path.join(project["work_dir"], "previews",
                        f"preview_{clip['start']:.2f}_{clip['end']:.2f}.mp4")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        render_preview(project["video_path"], clip["start"], clip["end"], path)
    return path


def style_still(project, clip, style=DEFAULT_PRESET, framing="auto", lines=None):
    """One frame showing the final framing + caption style."""
    plan = plan_framing(project["video_path"], clip["start"], clip["end"], framing,
                        (project["width"], project["height"]))
    lines = lines if lines is not None else caption_lines(project, clip, style)
    path = os.path.join(project["work_dir"], "previews", f"style_{clip['start']:.2f}_{style}_{framing}.jpg")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return render_still(project["video_path"], clip["start"], clip["end"], plan, path, lines, style)


def render_clip(project, clip, style=DEFAULT_PRESET, framing="auto", lines=None, loudnorm=True,
                index=None, progress=None):
    """Render one clip. Returns {"video", "thumbnail", "metadata", "text", "clip"}."""
    progress = progress or _noop
    index = index or clip.get("id", 1)
    out_dir = os.path.join(config.OUTPUT_DIR, project["slug"][:60])
    base = os.path.join(out_dir, f"{index:02d}-{clean_filename(clip.get('title') or 'clip', 50)}")

    progress(0.1, f"Clip {index}: analysing faces / framing ({framing})...")
    plan = plan_framing(project["video_path"], clip["start"], clip["end"], framing,
                        (project["width"], project["height"]))
    if lines is None:
        lines = caption_lines(project, clip, style)

    progress(0.4, f"Clip {index}: rendering ({plan['mode']}, captions: {style})...")
    video = render_short(project["video_path"], clip["start"], clip["end"], plan, base + ".mp4",
                         caption_lines=lines, preset=style, loudnorm=loudnorm,
                         has_audio=project.get("has_audio", True))
    thumb = thumbnail(video, base + ".jpg", at=min(0.8, (clip["end"] - clip["start"]) / 2))

    text = metadata_text(clip)
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(text)
    progress(1.0, f"Clip {index}: done -> {video}")
    return {"video": video, "thumbnail": thumb, "metadata": base + ".txt", "text": text,
            "clip": clip, "framing": plan["mode"]}


def metadata_text(clip):
    tags = " ".join(f"#{t}" for t in clip.get("hashtags") or [])
    return (f"{clip.get('title', '')}\n\n{clip.get('description', '')}\n\n{tags}\n\n"
            f"(source {format_time(clip['start'])} - {format_time(clip['end'])}, score {clip.get('score', '-')}/10)\n")
