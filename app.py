"""Point-and-click web UI: paste a link -> pick clips -> choose a style -> download shorts.

Run:  python app.py            (then open http://127.0.0.1:7860)
      python app.py --share    (temporary public link, e.g. to use from your phone)
"""
import argparse
import os
import zipfile

import gradio as gr

from Components import config, pipeline
from Components.captions import DEFAULT_PRESET, PRESETS, apply_text_edits
from Components.framing import MODES
from Components.highlights import DEFAULT_MODELS, MODEL_CHOICES, PROVIDERS

CLIP_HEADERS = ["#", "Score", "Start", "End", "Seconds", "Title", "Why it works"]
CAPTION_HEADERS = ["Start", "End", "Caption text"]
STYLE_CHOICES = [(p["label"], name) for name, p in PRESETS.items()]
FRAMING_CHOICES = [
    ("Auto (follow speaker, fit screen recordings)", "auto"),
    ("Follow faces", "track"),
    ("Center crop", "center"),
    ("Whole video on blurred background", "fit-blur"),
    ("Split screen (2 people)", "split"),
]
assert {v for _, v in FRAMING_CHOICES} == set(MODES)


def _progress(progress):
    return lambda fraction, message: progress(fraction, desc=message)


def _label(clip):
    return f"{clip['id']}. {clip['title']} ({clip['end'] - clip['start']:.0f}s, {clip['score']}/10)"


def _rows(clips):
    return [[c["id"], c["score"], pipeline.format_time(c["start"]), pipeline.format_time(c["end"]),
             round(c["end"] - c["start"]), c["title"], c.get("reason", "")] for c in clips]


def _table_to_rows(table):
    """Gradio may hand back a list of lists or a pandas DataFrame."""
    if table is None:
        return []
    if hasattr(table, "values"):
        return table.values.tolist()
    return list(table)


def _sync(project, clips, table, caption_edits):
    """Apply start/end/title edits from the clip table (snapping to word boundaries)."""
    by_id = {c["id"]: c for c in clips}
    updated = []
    for row in _table_to_rows(table):
        if not row or str(row[0]).strip() == "" or int(float(row[0])) not in by_id:
            continue
        clip = by_id[int(float(row[0]))]
        start, end = pipeline.parse_time(row[2]), pipeline.parse_time(row[3])
        if abs(start - clip["start"]) > 0.05 or abs(end - clip["end"]) > 0.05:
            clip = pipeline.adjust_clip(project, clip, start, end)
            caption_edits.pop(str(clip["id"]), None)  # captions no longer match the new range
        updated.append({**clip, "title": str(row[5]).strip() or clip["title"]})
    return updated or clips


def _find(choice, clips):
    if not clips:
        raise gr.Error("Find clips first.")
    if choice:
        wanted = int(str(choice).split(".")[0])
        for clip in clips:
            if clip["id"] == wanted:
                return clip
    return clips[0]


def _models_for(provider):
    """Refresh the model list when the AI provider changes."""
    return gr.update(choices=MODEL_CHOICES.get(provider, []), value=DEFAULT_MODELS.get(provider, ""))


def find_clips(url, upload, num_clips, min_len, max_len, instructions, provider, model, language,
               progress=gr.Progress()):
    source = (url or "").strip()
    if not source and not upload:
        raise gr.Error("Paste a YouTube link or upload a video first.")
    if min_len >= max_len:
        raise gr.Error("Minimum length must be smaller than maximum length.")
    try:
        if upload:
            source = pipeline.import_upload(upload)  # keep a copy so History works after a restart
        project = pipeline.prepare(source, progress=_progress(progress), language=language or None)
        clips = pipeline.suggest_clips(project, int(num_clips), int(min_len), int(max_len), instructions,
                                       provider=provider, model=(model or "").strip() or None,
                                       progress=_progress(progress))
    except Exception as e:
        raise gr.Error(f"{type(e).__name__}: {e}")
    if not clips:
        raise gr.Error("No suitable clips found. Try a shorter minimum length or different instructions.")
    # New clip suggestions replace the old list (and its caption edits) for this video.
    pipeline.save_session(project, clips=clips, caption_edits={}, settings=dict(
        num_clips=num_clips, min_len=min_len, max_len=max_len, instructions=instructions,
        provider=provider, model=model, language=language))
    labels = [_label(c) for c in clips]
    status = (f"**{project['title']}**: {pipeline.format_time(project['duration'])} long, language "
              f"`{project['transcript']['language']}`. Found **{len(clips)} clips**. Edit Start/End/Title "
              f"in the table if you like, then preview or render below.")
    return (project, clips, {}, _rows(clips), status,
            gr.update(choices=labels, value=labels[0]),
            gr.update(choices=labels, value=labels[: min(3, len(labels))]))


def more_clips(project, clips, table, caption_edits, num_clips, min_len, max_len, instructions, provider, model,
               progress=gr.Progress()):
    """Ask for different clips than the ones already found."""
    if not project:
        raise gr.Error("Find clips first.")
    clips = _sync(project, clips, table, caption_edits)
    try:
        new = pipeline.suggest_clips(project, int(num_clips), int(min_len), int(max_len), instructions,
                                     provider=provider, model=(model or "").strip() or None, exclude=[(c["start"], c["end"]) for c in clips],
                                     progress=_progress(progress))
    except Exception as e:
        raise gr.Error(f"{type(e).__name__}: {e}")
    next_id = max((c["id"] for c in clips), default=0)
    for i, clip in enumerate(new, 1):
        clip["id"] = next_id + i
    clips = clips + new
    pipeline.save_session(project, clips=clips, caption_edits=caption_edits)
    labels = [_label(c) for c in clips]
    return (clips, _rows(clips), f"Added {len(new)} more clips.",
            gr.update(choices=labels), gr.update(choices=labels))


def preview(project, clips, table, caption_edits, choice, style, framing, progress=gr.Progress()):
    if not project:
        raise gr.Error("Find clips first.")
    clips = _sync(project, clips, table, caption_edits)
    clip = _find(choice, clips)
    try:
        progress(0.2, desc="Cutting preview...")
        video = pipeline.preview_clip(project, clip)
        progress(0.5, desc="Rendering style preview...")
        lines = _lines(project, clip, style, caption_edits)
        still = pipeline.style_still(project, clip, style, framing, lines=lines)
    except Exception as e:
        raise gr.Error(f"{type(e).__name__}: {e}")
    pipeline.save_session(project, clips=clips, caption_edits=caption_edits,
                          settings=dict(style=style, framing=framing))
    rows = [[round(l["start"], 2), round(l["end"], 2), l["text"]] for l in lines]
    return clips, _rows(clips), video, still, rows


def _lines(project, clip, style, caption_edits):
    lines = pipeline.caption_lines(project, clip, style)
    edited = caption_edits.get(str(clip["id"]))
    return apply_text_edits(lines, edited) if edited else lines


def save_captions(project, clips, caption_edits, choice, captions_table):
    """Remember caption text fixes for the selected clip."""
    if not project or not clips:
        return caption_edits, ""
    clip = _find(choice, clips)
    rows = _table_to_rows(captions_table)
    if not rows:
        return caption_edits, ""
    caption_edits = dict(caption_edits or {})
    caption_edits[str(clip["id"])] = [[r[0], r[1], r[2]] for r in rows]
    pipeline.save_session(project, caption_edits=caption_edits)
    return caption_edits, f"Caption edits saved for clip {clip['id']}."


def render(project, clips, table, caption_edits, selected, style, framing, loudnorm, progress=gr.Progress()):
    if not project:
        raise gr.Error("Find clips first.")
    clips = _sync(project, clips, table, caption_edits)
    chosen_ids = {int(str(s).split(".")[0]) for s in (selected or [])}
    chosen = [c for c in clips if c["id"] in chosen_ids]
    if not chosen:
        raise gr.Error("Tick at least one clip to render.")
    results = []
    for n, clip in enumerate(chosen):
        def report(fraction, message, n=n):
            progress((n + fraction) / len(chosen), desc=message)
        try:
            results.append(pipeline.render_clip(project, clip, style=style, framing=framing,
                                                lines=_lines(project, clip, style, caption_edits),
                                                loudnorm=loudnorm, progress=report))
        except Exception as e:
            raise gr.Error(f"Clip {clip['id']} failed: {type(e).__name__}: {e}")

    pipeline.save_session(project, clips=clips, caption_edits=caption_edits, render_results=results,
                          settings=dict(style=style, framing=framing, loudnorm=loudnorm))
    return (clips, _rows(clips)) + _render_outputs(results)


def _render_outputs(results):
    """(first video, download files incl. zip, metadata text) for a list of render results."""
    if not results:
        return None, None, ""
    files = [p for r in results for p in (r["video"], r["metadata"], r["thumbnail"]) if os.path.exists(p)]
    zip_path = os.path.join(os.path.dirname(results[0]["video"]), "shorts.zip")
    with zipfile.ZipFile(zip_path, "w") as z:
        for path in files:
            z.write(path, os.path.basename(path))
    text = "\n\n".join(f"===== {os.path.basename(r['video'])} (framing: {r.get('framing')}) =====\n{r['text']}"
                       for r in results)
    return results[0]["video"], [zip_path] + files, text


HISTORY_HEADERS = ["Title", "Source", "Length", "Clips", "Rendered", "Last updated"]


def _short_source(source):
    return source if source.startswith("http") else os.path.basename(source)


def refresh_history():
    rows = pipeline.list_history()
    table = [[r["title"], _short_source(r["source"]), pipeline.format_time(r["duration"]), r["n_clips"],
              r["n_renders"], r["updated_at"]] for r in rows]
    choices = [(f"{r['title']} ({r['updated_at']})", r["id"]) for r in rows]
    note = "" if rows else "No saved projects yet. Videos you process in the Create tab appear here."
    return table, gr.update(choices=choices, value=choices[0][1] if choices else None), note


def show_history_item(project_id):
    """Previous renders of the selected project."""
    if not project_id:
        return None, None, ""
    try:
        _, session = pipeline.open_project(project_id)
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"
    if not session["renders"]:
        return None, None, "Nothing rendered yet for this project. Open it to pick clips and render."
    return _render_outputs(session["renders"])


def open_from_history(project_id):
    """Load a saved project into the Create tab (no download, no transcription)."""
    if not project_id:
        raise gr.Error("Pick a project first.")
    try:
        project, session = pipeline.open_project(project_id)
    except Exception as e:
        raise gr.Error(f"{type(e).__name__}: {e}")
    clips, st = session["clips"], session["settings"]
    labels = [_label(c) for c in clips]
    provider = st.get("provider") or config.LLM_PROVIDER
    status = (f"**{project['title']}** reopened from history ({len(clips)} clips, "
              f"{len(session['renders'])} rendered). No download needed: edit, preview or render again.")
    if not clips:
        status += " No clips saved yet: click **Find the best clips**."
    video, files, text = _render_outputs(session["renders"])
    return (
        project, clips, session["caption_edits"], _rows(clips), status,
        gr.update(choices=labels, value=labels[0] if labels else None),
        gr.update(choices=labels, value=labels[: min(3, len(labels))]),
        st.get("style", DEFAULT_PRESET), st.get("framing", "auto"), st.get("loudnorm", True),
        st.get("num_clips", 5), st.get("min_len", 20), st.get("max_len", 60), st.get("instructions", ""),
        provider,
        gr.update(choices=MODEL_CHOICES.get(provider, []),
                  value=st.get("model") or DEFAULT_MODELS.get(provider, "")),
        st.get("language", ""), project["source"], video, files, text,
        gr.Tabs(selected="create"),
    )


def delete_from_history(project_id, confirmed):
    if not project_id:
        raise gr.Error("Pick a project first.")
    if not confirmed:
        raise gr.Error("Tick the confirmation box to delete this project.")
    try:
        pipeline.delete_project(project_id)
    except Exception as e:
        raise gr.Error(f"{type(e).__name__}: {e}")
    return refresh_history() + (False,)


def build_ui():
    with gr.Blocks(title="AI Shorts Generator") as demo:
        project = gr.State(None)
        clips = gr.State([])
        caption_edits = gr.State({})

        gr.Markdown("# 🎬 AI Shorts Generator\nPaste a video link → pick the moments you like → "
                    "choose a caption style → download ready-to-post vertical shorts.")

        with gr.Tabs() as tabs:
            with gr.Tab("✂️ Create", id="create"):
                gr.Markdown("## 1. Your video")
                with gr.Row():
                    url = gr.Textbox(label="YouTube link (or path to a video on this computer)",
                                     placeholder="https://www.youtube.com/watch?v=...", scale=3)
                    upload = gr.File(label="...or upload a video", file_types=["video"], type="filepath", scale=2)
                with gr.Accordion("Options", open=False):
                    with gr.Row():
                        num_clips = gr.Slider(1, 10, value=5, step=1, label="How many clips")
                        min_len = gr.Slider(5, 120, value=20, step=5, label="Min length (seconds)")
                        max_len = gr.Slider(15, 180, value=60, step=5, label="Max length (seconds)")
                    with gr.Row():
                        instructions = gr.Textbox(label="What should the AI look for? (optional)",
                                                  placeholder="e.g. funny moments, practical tips, strong opinions", scale=3)
                        provider = gr.Dropdown(PROVIDERS, value=config.LLM_PROVIDER, label="AI provider", scale=1)
                        model = gr.Dropdown(MODEL_CHOICES.get(config.LLM_PROVIDER, []),
                                            value=config.LLM_MODEL or DEFAULT_MODELS.get(config.LLM_PROVIDER, ""),
                                            allow_custom_value=True, label="Model (pick or type a name)", scale=1)
                        language = gr.Textbox(label="Language code (blank = auto)", placeholder="en, id, es...", scale=1)
                find_btn = gr.Button("🔍 Find the best clips", variant="primary")
                status = gr.Markdown()

                gr.Markdown("## 2. Pick & fine-tune clips\nYou can edit **Start**, **End** (m:ss) and **Title** "
                            "directly in the table. Cuts are snapped to word boundaries automatically.")
                table = gr.Dataframe(headers=CLIP_HEADERS, type="array", interactive=True, wrap=True)
                more_btn = gr.Button("➕ Find more (different) clips")

                gr.Markdown("## 3. Preview & style")
                with gr.Row():
                    style = gr.Dropdown(STYLE_CHOICES, value=DEFAULT_PRESET, label="Caption style")
                    framing = gr.Dropdown(FRAMING_CHOICES, value="auto", label="Framing")
                    loudnorm = gr.Checkbox(value=True, label="Normalize loudness")
                with gr.Row():
                    clip_choice = gr.Dropdown([], label="Clip to preview", scale=3)
                    preview_btn = gr.Button("👀 Preview clip & style", scale=1)
                with gr.Row():
                    preview_video = gr.Video(label="Original moment", height=360)
                    preview_image = gr.Image(label="How the short will look", type="filepath", height=360)
                captions_table = gr.Dataframe(headers=CAPTION_HEADERS, type="array", interactive=True, wrap=True,
                                              label="Captions (fix any typos, then click Save)")
                with gr.Row():
                    save_caps_btn = gr.Button("💾 Save caption edits")
                    caps_status = gr.Markdown()

                gr.Markdown("## 4. Render & download")
                selected = gr.CheckboxGroup([], label="Clips to render")
                render_btn = gr.Button("🎞️ Render selected shorts", variant="primary")
                with gr.Row():
                    result_video = gr.Video(label="First result", height=480)
                    with gr.Column():
                        result_files = gr.Files(label="Downloads (zip, videos, titles/hashtags, thumbnails)")
                        result_text = gr.Textbox(label="Titles, descriptions & hashtags (copy-paste ready)", lines=12)

            with gr.Tab("🕘 History", id="history") as history_tab:
                gr.Markdown("Videos you worked on before. **Open & continue** loads the clips, your edits and "
                            "style again, without downloading or transcribing the video again.")
                history_table = gr.Dataframe(headers=HISTORY_HEADERS, interactive=False, wrap=True)
                history_note = gr.Markdown()
                with gr.Row():
                    history_choice = gr.Dropdown([], label="Project", scale=3)
                    refresh_btn = gr.Button("🔄 Refresh", scale=1)
                    open_btn = gr.Button("📂 Open & continue", variant="primary", scale=1)
                gr.Markdown("### Previous renders")
                with gr.Row():
                    history_video = gr.Video(label="Latest short", height=420)
                    with gr.Column():
                        history_files = gr.Files(label="Downloads")
                        history_text = gr.Textbox(label="Titles, descriptions & hashtags", lines=10)
                with gr.Accordion("Delete from history", open=False):
                    gr.Markdown("Removes the cached download and transcript to free disk space. "
                                "Rendered shorts in the `output` folder are kept.")
                    with gr.Row():
                        delete_confirm = gr.Checkbox(label="Yes, delete this project's cached download/transcript")
                        delete_btn = gr.Button("🗑️ Delete", variant="stop")

        provider.input(_models_for, provider, model)
        find_btn.click(find_clips, [url, upload, num_clips, min_len, max_len, instructions, provider, model, language],
                       [project, clips, caption_edits, table, status, clip_choice, selected])
        more_btn.click(more_clips, [project, clips, table, caption_edits, num_clips, min_len, max_len,
                                    instructions, provider, model],
                       [clips, table, status, clip_choice, selected])
        preview_btn.click(preview, [project, clips, table, caption_edits, clip_choice, style, framing],
                          [clips, table, preview_video, preview_image, captions_table])
        save_caps_btn.click(save_captions, [project, clips, caption_edits, clip_choice, captions_table],
                            [caption_edits, caps_status])
        render_btn.click(render, [project, clips, table, caption_edits, selected, style, framing, loudnorm],
                         [clips, table, result_video, result_files, result_text])

        history_outputs = [history_table, history_choice, history_note]
        history_tab.select(refresh_history, None, history_outputs)
        refresh_btn.click(refresh_history, None, history_outputs)
        demo.load(refresh_history, None, history_outputs)
        history_choice.change(show_history_item, history_choice, [history_video, history_files, history_text])
        open_btn.click(open_from_history, history_choice,
                       [project, clips, caption_edits, table, status, clip_choice, selected,
                        style, framing, loudnorm, num_clips, min_len, max_len, instructions, provider, model,
                        language, url, result_video, result_files, result_text, tabs])
        delete_btn.click(delete_from_history, [history_choice, delete_confirm], history_outputs + [delete_confirm])
    return demo


def main():
    parser = argparse.ArgumentParser(description="AI Shorts Generator web UI")
    parser.add_argument("--host", default=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("GRADIO_SERVER_PORT", 7860)))
    parser.add_argument("--share", action="store_true", help="create a temporary public link")
    args = parser.parse_args()
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    build_ui().queue().launch(server_name=args.host, server_port=args.port, share=args.share,
                              allowed_paths=[config.OUTPUT_DIR, config.WORK_DIR])


if __name__ == "__main__":
    main()
