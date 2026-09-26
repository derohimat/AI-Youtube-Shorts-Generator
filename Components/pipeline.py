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
import shutil
import threading
import time

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


def _write_json(path, data):
    """Write JSON atomically so a crash never leaves a half-written file."""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"  # unique per writer
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def import_upload(path):
    """Copy an uploaded file (from Gradio's temp folder) into work/uploads once.

    The name is derived from the file's content, so uploading the same video again
    reuses the same project (download + transcript cache and history).
    """
    digest = hashlib.sha1(str(os.path.getsize(path)).encode())
    with open(path, "rb") as f:
        digest.update(f.read(8 * 1024 * 1024))
    upload_dir = os.path.join(config.WORK_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    target = os.path.join(upload_dir, f"{digest.hexdigest()[:10]}_{os.path.basename(path)}")
    if not os.path.exists(target):
        shutil.copy2(path, target)
    return target


# ---- Session history: clips, edits, settings and renders per project ----

SESSION_FILE = "session.json"
_PROJECT_ID = re.compile(r"^[0-9a-f]{8}$")


def _project_path(project_id):
    if not _PROJECT_ID.match(str(project_id)):
        raise ValueError(f"Invalid project id: {project_id!r}")
    return os.path.join(config.WORK_DIR, project_id)


def load_session(project):
    path = os.path.join(project["work_dir"], SESSION_FILE)
    if not os.path.exists(path):
        return {"clips": [], "caption_edits": {}, "settings": {}, "renders": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key, default in (("clips", []), ("caption_edits", {}), ("settings", {}), ("renders", [])):
        data.setdefault(key, default)
    return data


def save_session(project, clips=None, caption_edits=None, settings=None, render_results=None):
    """Merge the given fields into the project's session.json and return the session.

    Returns None if the project was deleted from history in the meantime.
    """
    if not os.path.isdir(project["work_dir"]):
        return None
    with _SESSION_LOCK:
        return _save_session(project, clips, caption_edits, settings, render_results)


_SESSION_LOCK = threading.Lock()


def _save_session(project, clips, caption_edits, settings, render_results):
    session = load_session(project)
    if clips is not None:
        session["clips"] = clips
    if caption_edits is not None:
        session["caption_edits"] = caption_edits
    if settings:
        session["settings"] = {**session["settings"], **settings}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for result in render_results or []:
        clip = result["clip"]
        session["renders"] = [r for r in session["renders"] if r["clip_id"] != clip["id"]] + [{
            "clip_id": clip["id"], "title": clip.get("title", ""), "video": result["video"],
            "metadata": result["metadata"], "thumbnail": result["thumbnail"], "text": result["text"],
            "style": result.get("style"), "framing": result.get("framing"), "rendered_at": now,
        }]
    session["updated_at"] = now
    _write_json(os.path.join(project["work_dir"], SESSION_FILE), session)
    return session


def list_history():
    """Projects that can be reopened without downloading, newest first."""
    rows = []
    if not os.path.isdir(config.WORK_DIR):
        return rows
    for name in os.listdir(config.WORK_DIR):
        if not _PROJECT_ID.match(name):
            continue
        work_dir = os.path.join(config.WORK_DIR, name)
        try:
            project = load_project(work_dir)
        except (OSError, ValueError):
            continue
        if not os.path.exists(project.get("video_path", "")):
            continue
        project["work_dir"] = work_dir
        session = load_session(project)
        renders = [r for r in session["renders"] if os.path.exists(r["video"])]
        rows.append({
            "id": name, "title": project.get("title", name), "source": project.get("source", ""),
            "duration": project.get("duration", 0), "n_clips": len(session["clips"]),
            "n_renders": len(renders),
            "updated_at": session.get("updated_at") or time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(os.path.join(work_dir, "project.json")))),
            "n_kept": sum(1 for c in session["clips"] if c.get("keep")),
            "thumbnail": renders[-1]["thumbnail"] if renders else project_thumbnail(project),
        })
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return rows


def open_project(project_id):
    """Load a cached project and its session. Never downloads or transcribes."""
    work_dir = _project_path(project_id)
    project = load_project(work_dir)
    project["work_dir"] = work_dir
    if not os.path.exists(project["video_path"]):
        raise FileNotFoundError("The cached video for this project is gone; add it again from the Create tab.")
    session = load_session(project)
    session["renders"] = [r for r in session["renders"] if os.path.exists(r["video"])]
    return project, session


def delete_project(project_id):
    """Delete a project's cached download, transcript and previews. Rendered shorts in output/ are kept."""
    work_dir = _project_path(project_id)
    project = load_project(work_dir)
    video = project.get("video_path", "")
    uploads = os.path.join(config.WORK_DIR, "uploads")
    if video and os.path.dirname(os.path.abspath(video)) == os.path.abspath(uploads) and os.path.exists(video):
        os.remove(video)  # our own copy of an uploaded file
    shutil.rmtree(work_dir)


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


def transcript_sentences(project, max_len=25.0):
    """Split the transcript into sentences (also broken at Whisper segment ends / long runs)."""
    sentences, current = [], []
    for word in all_words(project["transcript"]["segments"]):
        current.append(word)
        if word["sentence_end"] or word["segment_end"] or current[-1]["e"] - current[0]["s"] > max_len:
            sentences.append({"start": current[0]["s"], "end": current[-1]["e"],
                              "text": " ".join(w["w"] for w in current)})
            current = []
    if current:
        sentences.append({"start": current[0]["s"], "end": current[-1]["e"],
                          "text": " ".join(w["w"] for w in current)})
    return sentences


def clip_sentences(project, clip, context=45.0):
    """Sentences around a clip, each flagged `in_clip`, for editing a clip by ticking sentences."""
    rows = []
    for sentence in transcript_sentences(project):
        if sentence["end"] < clip["start"] - context or sentence["start"] > clip["end"] + context:
            continue
        mid = (sentence["start"] + sentence["end"]) / 2
        rows.append({**sentence, "in_clip": clip["start"] <= mid <= clip["end"]})
    return rows


def clip_from_sentences(project, clip, sentences, checked):
    """New clip covering the first..last checked sentence (clips are always one continuous piece)."""
    idx = [i for i, on in enumerate(checked) if on]
    if not idx:
        raise ValueError("Tick at least one sentence.")
    start = max(0.0, sentences[idx[0]]["start"] - 0.15)
    end = min(project["duration"], sentences[idx[-1]]["end"] + 0.3)
    return {**clip, "start": round(start, 2), "end": round(end, 2)}


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


def quick_preview(project, clip, style=DEFAULT_PRESET, framing="auto", lines=None):
    """Fast low-resolution 9:16 preview with the final framing and captions."""
    lines = lines if lines is not None else caption_lines(project, clip, style)
    key = hashlib.sha1(json.dumps([clip["start"], clip["end"], style, framing,
                                   [l["text"] for l in lines]]).encode()).hexdigest()[:10]
    path = os.path.join(project["work_dir"], "previews", f"short_{key}.mp4")
    if not os.path.exists(path):
        plan = plan_framing(project["video_path"], clip["start"], clip["end"], framing,
                            (project["width"], project["height"]))
        render_short(project["video_path"], clip["start"], clip["end"], plan, path, caption_lines=lines,
                     preset=style, has_audio=project.get("has_audio", True), size=(360, 640), fast=True)
    return path


def project_thumbnail(project):
    """A cover image for the project list (frame at 10% of the video), cached."""
    path = os.path.join(project["work_dir"], "cover.jpg")
    if not os.path.exists(path):
        try:
            thumbnail(project["video_path"], path, at=round(project.get("duration", 10) * 0.1, 2))
        except media.FFmpegError:
            return None
    return path


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
            "clip": clip, "framing": plan["mode"], "style": style}


def metadata_text(clip):
    tags = " ".join(f"#{t}" for t in clip.get("hashtags") or [])
    return (f"{clip.get('title', '')}\n\n{clip.get('description', '')}\n\n{tags}\n\n"
            f"(source {format_time(clip['start'])} - {format_time(clip['end'])}, score {clip.get('score', '-')}/10)\n")
