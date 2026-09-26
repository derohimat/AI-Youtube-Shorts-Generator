"""Step-by-step web UI (wizard) for turning a long video into shorts.

    0 Proyek -> 1 Sumber -> 2 Analisis -> 3 Pilih klip -> 4 Edit -> 5 Gaya -> 6 Render

Run:  python app.py            (then open http://127.0.0.1:7860)
      python app.py --share    (temporary public link, e.g. to use from your phone)
"""
import argparse
import os
import zipfile
from functools import partial

import gradio as gr

from Components import config, pipeline
from Components.captions import DEFAULT_PRESET, PRESETS, apply_text_edits
from Components.framing import MODES
from Components.highlights import DEFAULT_MODELS, MODEL_CHOICES, PROVIDERS, normalize_hashtags

STYLE_CHOICES = [(p["label"], name) for name, p in PRESETS.items()]
FRAMING_CHOICES = [
    ("Otomatis (ikuti pembicara, video layar pakai latar blur)", "auto"),
    ("Ikuti wajah", "track"),
    ("Potong tengah", "center"),
    ("Video utuh di atas latar blur", "fit-blur"),
    ("Layar terbagi (2 orang)", "split"),
]
assert {v for _, v in FRAMING_CHOICES} == set(MODES)

# Target platform -> (min seconds, max seconds, number of clips)
TARGETS = {
    "YouTube Shorts": (15, 60, 5),
    "TikTok": (15, 90, 5),
    "Instagram Reels": (15, 90, 5),
    "Klip panjang (sampai 3 menit)": (45, 180, 3),
}
CSS = """
.step-hint {opacity: .85}
.card {min-width: 260px}
/* no "select all" checkbox on the sentence table: one click would turn the clip into the whole window */
.sentence-table thead input[type=checkbox] {display: none}
"""


# ---------------------------------------------------------------- helpers

def _progress(progress):
    return lambda fraction, message: progress(fraction, desc=message)


def _go(step):
    return gr.Tabs(selected=step)


def _kept(clips):
    return [c for c in clips or [] if c.get("keep")]


def _dur(clip):
    return clip["end"] - clip["start"]


def _span(clip):
    return f"{pipeline.format_time(clip['start'])} – {pipeline.format_time(clip['end'])}"


def _rows_of(table):
    return table.values.tolist() if hasattr(table, "values") else list(table or [])


def _save(project, clips=None, caption_edits=None, **settings):
    if project:
        pipeline.save_session(project, clips=clips, caption_edits=caption_edits, settings=settings or None)


def _error(e):
    return gr.Error(f"{type(e).__name__}: {e}")


def _lines(project, clip, style, caption_edits):
    lines = pipeline.caption_lines(project, clip, style)
    edited = (caption_edits or {}).get(str(clip["id"]))
    return apply_text_edits(lines, edited) if edited else lines


def _zip(results):
    files = [p for r in results for p in (r["video"], r["metadata"], r["thumbnail"]) if os.path.exists(p)]
    if not files:
        return None
    path = os.path.join(os.path.dirname(results[0]["video"]), "shorts.zip")
    with zipfile.ZipFile(path, "w") as z:
        for f in files:
            z.write(f, os.path.basename(f))
    return path


def _resume_step(session):
    if session["renders"]:
        return "render"
    if _kept(session["clips"]):
        return "edit"
    if session["clips"]:
        return "pick"
    return "source"


# ---------------------------------------------------------------- 0. projects

def refresh_projects():
    rows = pipeline.list_history()
    choices = [(f"{r['title']} ({r['updated_at']})", r["id"]) for r in rows]
    return rows, gr.update(choices=choices, value=None)


def open_project(project_id):
    try:
        project, session = pipeline.open_project(project_id)
    except Exception as e:
        raise _error(e)
    clips = session["clips"]
    if clips and not any("keep" in c for c in clips):  # sessions saved before the wizard existed
        for c in sorted(clips, key=lambda c: c["score"], reverse=True)[:3]:
            c["keep"] = True
    st = session["settings"]
    provider = st.get("provider") or config.LLM_PROVIDER
    status = (f"✅ **{project['title']}** dibuka dari daftar proyek ({len(clips)} klip, "
              f"{len(session['renders'])} di-render). Tidak perlu download ulang.")
    return (
        project, clips, session["caption_edits"], 0, session["renders"], status,
        project["source"], st.get("instructions", ""), st.get("num_clips", 5), st.get("min_len", 15),
        st.get("max_len", 60), provider,
        gr.update(choices=MODEL_CHOICES.get(provider, []), value=st.get("model") or DEFAULT_MODELS.get(provider)),
        st.get("language", ""), st.get("style", DEFAULT_PRESET), st.get("framing", "auto"),
        st.get("loudnorm", True), _zip(session["renders"]) if session["renders"] else None,
        _go(_resume_step(session)),
    )


def delete_project(project_id, confirmed):
    if not project_id:
        raise gr.Error("Pilih proyek yang mau dihapus.")
    if not confirmed:
        raise gr.Error("Centang konfirmasi dulu.")
    try:
        pipeline.delete_project(project_id)
    except Exception as e:
        raise _error(e)
    return refresh_projects() + (False,)


def new_project():
    return None, [], {}, 0, [], "Belum ada analisis.", "", None, "", None, _go("source")


# ---------------------------------------------------------------- 1-2. source & analysis

def apply_target(target):
    return TARGETS[target]


def models_for(provider):
    return gr.update(choices=MODEL_CHOICES.get(provider, []), value=DEFAULT_MODELS.get(provider, ""))


def check_source(url, upload, min_len, max_len):
    if not (url or "").strip() and not upload:
        raise gr.Error("Tempel link YouTube atau upload video dulu.")
    if min_len >= max_len:
        raise gr.Error("Durasi minimum harus lebih kecil dari maksimum.")
    return _go("analyze"), "⏳ Memproses..."


def analyze(url, upload, num_clips, min_len, max_len, instructions, provider, model, language,
            progress=gr.Progress()):
    source = (url or "").strip()
    try:
        if upload:
            source = pipeline.import_upload(upload)
        project = pipeline.prepare(source, progress=_progress(progress), language=language or None)
        clips = pipeline.suggest_clips(project, int(num_clips), int(min_len), int(max_len), instructions,
                                       provider=provider, model=(model or "").strip() or None,
                                       progress=_progress(progress))
    except Exception as e:
        raise _error(e)
    if not clips:
        raise gr.Error("Tidak ada klip yang cocok. Coba durasi minimum lebih pendek atau instruksi lain.")
    for i, clip in enumerate(sorted(clips, key=lambda c: c["score"], reverse=True)):
        clip["keep"] = i < 3
    for i, clip in enumerate(clips):  # warm up the preview videos shown on the cards
        progress((i + 1) / len(clips), desc=f"Menyiapkan preview klip {i + 1}/{len(clips)}")
        pipeline.preview_clip(project, clip)
    _save(project, clips, {}, num_clips=num_clips, min_len=min_len, max_len=max_len,
          instructions=instructions, provider=provider, model=model, language=language)
    status = (f"✅ **{project['title']}** ({pipeline.format_time(project['duration'])}, bahasa "
              f"`{project['transcript']['language']}`): ditemukan **{len(clips)} klip**.")
    return project, clips, {}, [], status, _go("pick")


# ---------------------------------------------------------------- 3. pick

def toggle_clip(clip_id, project, clips):
    clips = [{**c, "keep": not c.get("keep")} if c["id"] == clip_id else c for c in clips]
    _save(project, clips)
    return clips


def more_clips(project, clips, num_clips, min_len, max_len, instructions, provider, model,
               progress=gr.Progress()):
    if not project:
        raise gr.Error("Analisis video dulu.")
    try:
        new = pipeline.suggest_clips(project, int(num_clips), int(min_len), int(max_len), instructions,
                                     provider=provider, model=(model or "").strip() or None,
                                     exclude=[(c["start"], c["end"]) for c in clips],
                                     progress=_progress(progress))
    except Exception as e:
        raise _error(e)
    next_id = max((c["id"] for c in clips), default=0)
    for i, clip in enumerate(new, 1):
        clip.update(id=next_id + i, keep=False)
        pipeline.preview_clip(project, clip)
    clips = clips + new
    _save(project, clips)
    return clips, f"Ditambahkan {len(new)} klip baru." if new else "Tidak ada klip baru yang ditemukan."


def to_edit(clips):
    if not _kept(clips):
        raise gr.Error("Pilih minimal satu klip (klik 'Pakai klip ini').")
    return 0, _go("edit")


# ---------------------------------------------------------------- 4. edit

def _current(clips, idx):
    kept = _kept(clips)
    if not kept:
        raise gr.Error("Belum ada klip yang dipilih.")
    idx = max(0, min(int(idx or 0), len(kept) - 1))
    return kept, idx, kept[idx]


def _sentence_rows(sentences):
    return [[s["in_clip"], pipeline.format_time(s["start"]), s["text"]] for s in sentences]


def _caption_rows(lines):
    return [[round(l["start"], 2), round(l["end"], 2), l["text"]] for l in lines]


def _clip_info(clip, max_len):
    warn = f" ⚠️ lebih panjang dari target ({max_len:.0f} dtk)" if max_len and _dur(clip) > max_len + 5 else ""
    return f"**Durasi: {_dur(clip):.0f} detik** ({_span(clip)}){warn}"


def load_edit(project, clips, idx, caption_edits, style, max_len):
    if not project:
        raise gr.Error("Buka atau buat proyek dulu.")
    kept, idx, clip = _current(clips, idx)
    sentences = pipeline.clip_sentences(project, clip)
    return (idx, f"### Klip {idx + 1} dari {len(kept)}", clip["title"], clip.get("description", ""),
            " ".join(f"#{t}" for t in clip.get("hashtags") or []),
            sentences, _sentence_rows(sentences), _clip_info(clip, max_len),
            _caption_rows(_lines(project, clip, style, caption_edits)), None)


def load_edit_if_any(project, clips, idx, caption_edits, style, max_len):
    if not project or not _kept(clips):
        return (gr.update(),) * 10
    return load_edit(project, clips, idx, caption_edits, style, max_len)


def _replace(clips, clip):
    return [clip if c["id"] == clip["id"] else c for c in clips]


def commit_fields(project, clips, idx, title, description, hashtags):
    """Store title/description/hashtags of the clip being edited."""
    if not project or not _kept(clips):
        return clips
    _, _, clip = _current(clips, idx)
    clip = {**clip, "title": (title or "").strip() or clip["title"], "description": (description or "").strip(),
            "hashtags": normalize_hashtags((hashtags or "").replace(",", " ").split())}
    clips = _replace(clips, clip)
    _save(project, clips)
    return clips


def edit_sentences(project, clips, idx, sentences, table, caption_edits, style, max_len):
    """Ticking sentences changes the clip range (always one continuous piece)."""
    _, _, clip = _current(clips, idx)
    checked = [bool(r[0]) for r in _rows_of(table)][: len(sentences)]
    try:
        new_clip = pipeline.clip_from_sentences(project, clip, sentences, checked)
    except ValueError:
        raise gr.Error("Centang minimal satu kalimat.")
    clips = _replace(clips, new_clip)
    caption_edits = {k: v for k, v in (caption_edits or {}).items() if k != str(clip["id"])}
    _save(project, clips, caption_edits)
    sentences = pipeline.clip_sentences(project, new_clip)
    return (clips, caption_edits, sentences, _sentence_rows(sentences), _clip_info(new_clip, max_len),
            _caption_rows(_lines(project, new_clip, style, caption_edits)))


def edit_captions(project, clips, idx, table, caption_edits, style):
    """Save caption text fixes (only when they differ from the transcript)."""
    _, _, clip = _current(clips, idx)
    rows = _rows_of(table)
    generated = pipeline.caption_lines(project, clip, style)
    caption_edits = dict(caption_edits or {})
    if [str(r[2]).strip() for r in rows] == [l["text"] for l in generated]:
        caption_edits.pop(str(clip["id"]), None)
    else:
        caption_edits[str(clip["id"])] = [[r[0], r[1], r[2]] for r in rows]
    _save(project, clips, caption_edits)
    return caption_edits


def edit_preview(project, clips, idx, caption_edits, style, framing, progress=gr.Progress()):
    _, _, clip = _current(clips, idx)
    progress(0.2, desc="Membuat preview 9:16...")
    try:
        return pipeline.quick_preview(project, clip, style, framing, _lines(project, clip, style, caption_edits))
    except Exception as e:
        raise _error(e)


def step_clip(delta, clips, idx):
    return max(0, min(int(idx) + delta, len(_kept(clips)) - 1))


# ---------------------------------------------------------------- 5. style

def style_clip_choices(clips):
    choices = [(f"{i + 1}. {c['title']}", c["id"]) for i, c in enumerate(_kept(clips))]
    return gr.update(choices=choices, value=choices[0][1] if choices else None)


def style_preview(project, clips, clip_id, caption_edits, style, framing):
    if not project or not clips:
        return None
    clip = next((c for c in clips if c["id"] == clip_id), None) or (_kept(clips) or clips)[0]
    try:
        still = pipeline.style_still(project, clip, style, framing, lines=_lines(project, clip, style, caption_edits))
    except Exception as e:
        raise _error(e)
    _save(project, style=style, framing=framing)
    return still


# ---------------------------------------------------------------- 6. render

def render_summary(clips, style, framing):
    kept = _kept(clips)
    if not kept:
        return "Belum ada klip yang dipilih. Kembali ke langkah **3 · Pilih klip**."
    items = "\n".join(f"{i}. **{c['title']}** ({_dur(c):.0f} dtk)" for i, c in enumerate(kept, 1))
    style_label = dict((v, k) for k, v in STYLE_CHOICES).get(style, style)
    framing_label = dict((v, k) for k, v in FRAMING_CHOICES).get(framing, framing)
    return f"**{len(kept)} klip** akan di-render · caption: *{style_label}* · framing: *{framing_label}*\n\n{items}"


def render_all(project, clips, caption_edits, style, framing, loudnorm, progress=gr.Progress()):
    kept = _kept(clips)
    if not project or not kept:
        raise gr.Error("Pilih minimal satu klip dulu.")
    results = []
    for n, clip in enumerate(kept, 1):
        def report(fraction, message, n=n):
            progress((n - 1 + fraction) / len(kept), desc=f"Klip {n}/{len(kept)}: {message}")
        try:
            results.append(pipeline.render_clip(project, clip, style=style, framing=framing,
                                                lines=_lines(project, clip, style, caption_edits),
                                                loudnorm=loudnorm, index=n, progress=report))
        except Exception as e:
            raise gr.Error(f"Klip {n} gagal: {type(e).__name__}: {e}")
    session = pipeline.save_session(project, clips=clips, caption_edits=caption_edits, render_results=results,
                                    settings=dict(style=style, framing=framing, loudnorm=loudnorm))
    renders = session["renders"] if session else results
    return renders, _zip(renders)


# ---------------------------------------------------------------- layout

def build_ui():
    with gr.Blocks(title="AI Shorts Generator") as demo:
        project = gr.State(None)
        clips = gr.State([])
        caption_edits = gr.State({})
        edit_idx = gr.State(0)
        sentences = gr.State([])
        renders = gr.State([])
        projects = gr.State([])
        wiring = {}  # event targets used by dynamically rendered cards

        gr.Markdown("# 🎬 AI Shorts Generator\nIkuti langkahnya dari kiri ke kanan. Semua tersimpan otomatis.")

        with gr.Tabs() as tabs:
            # ---------------- 0
            with gr.Tab("0 · Proyek", id="projects") as tab_projects:
                gr.Markdown("Mulai proyek baru, atau lanjutkan video yang pernah dikerjakan "
                            "(tanpa download/transkripsi ulang).", elem_classes="step-hint")
                new_btn = gr.Button("➕ Proyek baru", variant="primary", size="lg")

                @gr.render(inputs=projects)
                def project_cards(rows):
                    if not rows:
                        gr.Markdown("*Belum ada proyek. Klik **Proyek baru** untuk mulai.*")
                        return
                    for start in range(0, len(rows), 3):
                        with gr.Row(equal_height=True):
                            for row in rows[start:start + 3]:
                                with gr.Column(variant="panel", elem_classes="card"):
                                    if row.get("thumbnail"):
                                        gr.Image(row["thumbnail"], show_label=False, height=150,
                                                 interactive=False)
                                    if row["n_renders"]:
                                        status = f"✅ {row['n_renders']} di-render"
                                    elif row["n_clips"]:
                                        status = f"✂️ {row.get('n_kept', 0)}/{row['n_clips']} klip dipilih"
                                    else:
                                        status = "🆕 belum dianalisis"
                                    gr.Markdown(f"**{row['title']}**\n\n{pipeline.format_time(row['duration'])}"
                                                f" · {status}\n\n<small>{row['updated_at']}</small>")
                                    btn = gr.Button("▶ Lanjutkan", variant="primary", size="sm")
                                    btn.click(partial(open_project, row["id"]), None, wiring["open"]).success(
                                        load_edit_if_any, wiring["edit_in"], wiring["edit_out"]).success(
                                        style_clip_choices, clips, wiring["style_clip"]).success(
                                        render_summary, [clips, wiring["style"], wiring["framing"]],
                                        wiring["render_info"])

                with gr.Accordion("🗑️ Hapus proyek", open=False):
                    gr.Markdown("Menghapus cache download & transkrip. Hasil short di folder `output` tetap ada.")
                    with gr.Row():
                        delete_choice = gr.Dropdown([], label="Proyek", scale=3)
                        delete_confirm = gr.Checkbox(label="Ya, hapus", scale=1)
                        delete_btn = gr.Button("Hapus", variant="stop", scale=1)

            # ---------------- 1
            with gr.Tab("1 · Sumber", id="source"):
                gr.Markdown("**Video mana yang mau dipotong, dan untuk platform apa?**", elem_classes="step-hint")
                with gr.Row():
                    url = gr.Textbox(label="Link YouTube (atau path video di komputer ini)",
                                     placeholder="https://www.youtube.com/watch?v=...", scale=3)
                    upload = gr.File(label="...atau upload video", file_types=["video"], type="filepath", scale=2)
                target = gr.Radio(list(TARGETS), value="YouTube Shorts", label="Untuk platform")
                instructions = gr.Textbox(label="Cari momen seperti apa? (opsional)",
                                          placeholder="mis. momen lucu, tips praktis, opini kuat")
                with gr.Accordion("Pengaturan lanjutan", open=False):
                    with gr.Row():
                        num_clips = gr.Slider(1, 10, value=5, step=1, label="Jumlah klip")
                        min_len = gr.Slider(5, 120, value=15, step=5, label="Durasi min (detik)")
                        max_len = gr.Slider(15, 180, value=60, step=5, label="Durasi maks (detik)")
                    with gr.Row():
                        provider = gr.Dropdown(PROVIDERS, value=config.LLM_PROVIDER, label="AI provider")
                        model = gr.Dropdown(MODEL_CHOICES.get(config.LLM_PROVIDER, []),
                                            value=config.LLM_MODEL or DEFAULT_MODELS.get(config.LLM_PROVIDER, ""),
                                            allow_custom_value=True, label="Model")
                        language = gr.Textbox(label="Kode bahasa (kosong = otomatis)", placeholder="id, en, ...")
                analyze_btn = gr.Button("Analisis video →", variant="primary", size="lg")

            # ---------------- 2
            with gr.Tab("2 · Analisis", id="analyze"):
                gr.Markdown("AI sedang: **download → transkripsi → mencari momen terbaik**. Video panjang bisa "
                            "butuh beberapa menit; hasilnya tersimpan di halaman Proyek.", elem_classes="step-hint")
                analyze_status = gr.Markdown("Belum ada analisis. Mulai dari langkah **1 · Sumber**.")

            # ---------------- 3
            with gr.Tab("3 · Pilih klip", id="pick"):
                gr.Markdown("**Tonton preview, lalu klik *Pakai klip ini* pada klip yang kamu suka.** "
                            "3 klip terbaik sudah dipilih otomatis.", elem_classes="step-hint")

                @gr.render(inputs=[project, clips])
                def clip_cards(proj, clip_list):
                    if not proj or not clip_list:
                        gr.Markdown("*Belum ada klip. Analisis video dulu di langkah 1.*")
                        return
                    for start in range(0, len(clip_list), 3):
                        with gr.Row(equal_height=True):
                            for clip in clip_list[start:start + 3]:
                                keep = clip.get("keep")
                                with gr.Column(variant="panel", elem_classes="card"):
                                    gr.Video(pipeline.preview_clip(proj, clip), show_label=False, height=220)
                                    gr.Markdown(f"**{'✅ ' if keep else ''}{clip['title']}**\n\n⭐ {clip['score']}/10"
                                                f" · {_dur(clip):.0f} dtk · {_span(clip)}\n\n"
                                                f"<small>{clip.get('reason', '')}</small>")
                                    btn = gr.Button("✓ Dipakai (klik untuk batal)" if keep else "Pakai klip ini",
                                                    variant="primary" if keep else "secondary", size="sm")
                                    btn.click(partial(toggle_clip, clip["id"]), [project, clips], clips)

                pick_status = gr.Markdown()
                with gr.Row():
                    more_btn = gr.Button("🔁 Cari klip lain")
                    to_edit_btn = gr.Button("Lanjut: edit klip →", variant="primary")

            # ---------------- 4
            with gr.Tab("4 · Edit", id="edit"):
                gr.Markdown("**Rapikan tiap klip:** centang kalimat yang mau dimasukkan (klip selalu satu potongan "
                            "utuh), perbaiki judul & caption, lalu lihat preview 9:16.", elem_classes="step-hint")
                with gr.Row():
                    prev_btn = gr.Button("◀ Klip sebelumnya", size="sm")
                    edit_header = gr.Markdown()
                    next_btn = gr.Button("Klip berikutnya ▶", size="sm")
                with gr.Row():
                    with gr.Column(scale=3):
                        clip_info = gr.Markdown()
                        sentence_table = gr.Dataframe(
                            headers=["Pakai", "Waktu", "Kalimat"], datatype=["bool", "str", "str"],
                            static_columns=[1, 2], interactive=True, wrap=True, max_height=420,
                            label="Transkrip di sekitar klip", elem_classes="sentence-table")
                        title = gr.Textbox(label="Judul")
                        description = gr.Textbox(label="Deskripsi", lines=2)
                        hashtags = gr.Textbox(label="Hashtag", placeholder="#tips #motivasi")
                    with gr.Column(scale=2):
                        edit_video = gr.Video(label="Preview 9:16", height=480)
                        edit_preview_btn = gr.Button("▶ Buat preview 9:16")
                caption_table = gr.Dataframe(headers=["Mulai", "Selesai", "Teks caption"],
                                             datatype=["number", "number", "str"], static_columns=[0, 1],
                                             interactive=True, wrap=True, max_height=300,
                                             label="Caption (ketik untuk memperbaiki typo, tersimpan otomatis)")
                with gr.Row():
                    back_pick_btn = gr.Button("◀ Kembali pilih klip")
                    to_style_btn = gr.Button("Lanjut: pilih gaya →", variant="primary")

            # ---------------- 5
            with gr.Tab("5 · Gaya", id="style"):
                gr.Markdown("**Pilih tampilan caption dan cara membingkai video.** Berlaku untuk semua klip.",
                            elem_classes="step-hint")
                with gr.Row():
                    with gr.Column():
                        style = gr.Dropdown(STYLE_CHOICES, value=DEFAULT_PRESET, label="Gaya caption")
                        framing = gr.Dropdown(FRAMING_CHOICES, value="auto", label="Framing")
                        loudnorm = gr.Checkbox(value=True, label="Samakan volume suara (disarankan)")
                        style_clip = gr.Dropdown([], label="Preview pada klip")
                    style_image = gr.Image(label="Tampilan short", type="filepath", height=520)
                with gr.Row():
                    back_edit_btn = gr.Button("◀ Kembali edit")
                    to_render_btn = gr.Button("Lanjut: render →", variant="primary")

            # ---------------- 6
            with gr.Tab("6 · Render", id="render"):
                render_info = gr.Markdown()
                render_btn = gr.Button("🎞️ Render semua klip yang dipilih", variant="primary", size="lg")
                zip_file = gr.File(label="Download semua (zip)")

                @gr.render(inputs=renders)
                def result_cards(items):
                    if not items:
                        return
                    gr.Markdown("### Hasil")
                    for start in range(0, len(items), 3):
                        with gr.Row(equal_height=True):
                            for item in items[start:start + 3]:
                                with gr.Column(variant="panel", elem_classes="card"):
                                    gr.Video(item["video"], show_label=False, height=420)
                                    gr.Textbox(item["text"], label="Judul, deskripsi & hashtag", lines=6,
                                               buttons=["copy"])

                with gr.Row():
                    back_edit2_btn = gr.Button("✏️ Edit klip lagi")
                    home_btn = gr.Button("🏠 Ke daftar proyek")

        # ---------------------------------------------------------- wiring
        edit_in = [project, clips, edit_idx, caption_edits, style, max_len]
        edit_out = [edit_idx, edit_header, title, description, hashtags, sentences, sentence_table,
                    clip_info, caption_table, edit_video]
        wiring.update(
            open=[project, clips, caption_edits, edit_idx, renders, analyze_status, url, instructions, num_clips,
                  min_len, max_len, provider, model, language, style, framing, loudnorm, zip_file, tabs],
            edit_in=edit_in, edit_out=edit_out, style_clip=style_clip, style=style, framing=framing,
            render_info=render_info)

        refresh = [projects, delete_choice]
        demo.load(refresh_projects, None, refresh)
        tab_projects.select(refresh_projects, None, refresh)
        delete_btn.click(delete_project, [delete_choice, delete_confirm], refresh + [delete_confirm])
        new_btn.click(new_project, None, [project, clips, caption_edits, edit_idx, renders, analyze_status,
                                          url, upload, instructions, zip_file, tabs])

        target.change(apply_target, target, [min_len, max_len, num_clips])
        provider.input(models_for, provider, model)
        analyze_btn.click(check_source, [url, upload, min_len, max_len], [tabs, analyze_status]).success(
            analyze, [url, upload, num_clips, min_len, max_len, instructions, provider, model, language],
            [project, clips, caption_edits, renders, analyze_status, tabs])

        more_btn.click(more_clips, [project, clips, num_clips, min_len, max_len, instructions, provider, model],
                       [clips, pick_status])
        to_edit_btn.click(to_edit, clips, [edit_idx, tabs]).success(load_edit, edit_in, edit_out)

        fields = [project, clips, edit_idx, title, description, hashtags]
        for btn, delta in ((prev_btn, -1), (next_btn, 1)):
            btn.click(commit_fields, fields, clips).success(
                partial(step_clip, delta), [clips, edit_idx], edit_idx).success(load_edit, edit_in, edit_out)
        sentence_table.input(edit_sentences, [project, clips, edit_idx, sentences, sentence_table, caption_edits,
                                              style, max_len],
                             [clips, caption_edits, sentences, sentence_table, clip_info, caption_table])
        caption_table.input(edit_captions, [project, clips, edit_idx, caption_table, caption_edits, style],
                            caption_edits)
        edit_preview_btn.click(commit_fields, fields, clips).success(
            edit_preview, [project, clips, edit_idx, caption_edits, style, framing], edit_video)
        back_pick_btn.click(commit_fields, fields, clips).success(lambda: _go("pick"), None, tabs)
        to_style_btn.click(commit_fields, fields, clips).success(lambda: _go("style"), None, tabs).success(
            style_clip_choices, clips, style_clip)

        preview_in = [project, clips, style_clip, caption_edits, style, framing]
        for comp in (style, framing, style_clip):
            comp.change(style_preview, preview_in, style_image)
        back_edit_btn.click(lambda: _go("edit"), None, tabs).success(load_edit, edit_in, edit_out)
        to_render_btn.click(lambda: _go("render"), None, tabs).success(
            render_summary, [clips, style, framing], render_info)

        render_btn.click(render_all, [project, clips, caption_edits, style, framing, loudnorm], [renders, zip_file])
        back_edit2_btn.click(lambda: _go("edit"), None, tabs).success(load_edit, edit_in, edit_out)
        home_btn.click(lambda: refresh_projects() + (_go("projects"),), None, refresh + [tabs])
    return demo


def main():
    parser = argparse.ArgumentParser(description="AI Shorts Generator web UI")
    parser.add_argument("--host", default=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("GRADIO_SERVER_PORT", 7860)))
    parser.add_argument("--share", action="store_true", help="create a temporary public link")
    args = parser.parse_args()
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    build_ui().queue().launch(server_name=args.host, server_port=args.port, share=args.share, css=CSS,
                              allowed_paths=[config.OUTPUT_DIR, config.WORK_DIR])


if __name__ == "__main__":
    main()
