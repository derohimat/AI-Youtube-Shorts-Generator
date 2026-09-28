"""Render a short in a single ffmpeg pass:

    trim (+ cut silences) -> frame (crop / blur / split) -> captions + hook -> logo -> encode
    speech (+ loudness) -> optional background music that ducks under the voice
"""
import os

from Components import config
from Components.captions import write_ass
from Components.media import filter_path, run_ffmpeg

LOGO_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")


def _crop_x_expr(keyframes):
    """Piecewise-constant crop x as an ffmpeg expression (t is output time)."""
    expr = str(int(keyframes[-1][1]))
    for (t, _), (_, x) in zip(reversed(keyframes[1:]), reversed(keyframes[:-1])):
        expr = f"if(lt(t,{t:.2f}),{int(x)},{expr})"
    return expr


def video_filter(plan, out_w=None, out_h=None, src="[0:v]"):
    """Filter chain that turns `src` into the framed output stream [v0]."""
    out_w, out_h = out_w or config.OUTPUT_WIDTH, out_h or config.OUTPUT_HEIGHT
    mode = plan["mode"]
    if mode in ("track", "center"):
        x = _crop_x_expr(plan["keyframes"])
        y = plan.get("crop_y", 0)
        return (f"{src}crop=w={plan['crop_w']}:h={plan['crop_h']}:x='{x}':y={y},"
                f"scale={out_w}:{out_h}:flags=lanczos,setsar=1[v0]")
    if mode == "split":
        (x1, y1), (x2, y2) = plan["boxes"]
        hw, hh, half = plan["half_w"], plan["half_h"], out_h // 2
        return (f"{src}split=2[sa][sb];"
                f"[sa]crop={hw}:{hh}:{x1}:{y1},scale={out_w}:{half}:flags=lanczos[top];"
                f"[sb]crop={hw}:{hh}:{x2}:{y2},scale={out_w}:{out_h - half}:flags=lanczos[bottom];"
                f"[top][bottom]vstack,setsar=1[v0]")
    # fit-blur: the whole frame over a blurred, zoomed copy of itself.
    return (f"{src}split=2[bg][fg];"
            f"[bg]scale={out_w // 4}:{out_h // 4}:force_original_aspect_ratio=increase,"
            f"crop={out_w // 4}:{out_h // 4},boxblur=12:2,eq=brightness=-0.06,scale={out_w}:{out_h}[bgb];"
            f"[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]")


def _cut_filter(segments, has_audio):
    """Keep only `segments` (clip-relative) and join them. Returns (graph, video_label, audio_label)."""
    n = len(segments)
    parts = []
    if n > 1:
        parts.append(f"[0:v]split={n}" + "".join(f"[cv{i}]" for i in range(n)))
        if has_audio:
            parts.append(f"[0:a]asplit={n}" + "".join(f"[ca{i}]" for i in range(n)))
    joined = ""
    for i, (a, b) in enumerate(segments):
        vin = f"[cv{i}]" if n > 1 else "[0:v]"
        parts.append(f"{vin}trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[tv{i}]")
        joined += f"[tv{i}]"
        if has_audio:
            ain = f"[ca{i}]" if n > 1 else "[0:a]"
            parts.append(f"{ain}atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[ta{i}]")
            joined += f"[ta{i}]"
    parts.append(f"{joined}concat=n={n}:v=1:a={1 if has_audio else 0}[vcut]" + ("[acut]" if has_audio else ""))
    return ";".join(parts), "[vcut]", "[acut]" if has_audio else None


def _is_full(segments, duration):
    return not segments or (len(segments) == 1 and segments[0][0] <= 0.01 and segments[0][1] >= duration - 0.01)


def _logo_xy(position, margin):
    x = margin if "left" in position else f"W-w-{margin}"
    y = margin if "top" in position else f"H-h-{margin}"
    return x, y


def build_graph(plan, size, ass_path, duration, segments=None, has_audio=True, logo=None, music=None,
                loudnorm=True, fonts_dir=None, logo_input=1, music_input=2):
    """Assemble the full filter_complex. Returns (graph, audio_label or None)."""
    out_w, out_h = size
    graph, vsrc, asrc = [], "[0:v]", "[0:a]" if has_audio else None
    if not _is_full(segments, duration):
        cut, vsrc, asrc = _cut_filter(segments, has_audio)
        graph.append(cut)
    graph.append(video_filter(plan, out_w, out_h, src=vsrc))
    label = "[v0]"
    if ass_path:
        graph.append(f"{label}ass=filename={filter_path(os.path.basename(ass_path))}"
                     f":fontsdir={filter_path(fonts_dir or config.FONTS_DIR)}[v1]")
        label = "[v1]"
    if logo:
        width = max(16, int(out_w * float(logo.get("size", 0.18))))
        x, y = _logo_xy(logo.get("position", "top-right"), int(min(out_w, out_h) * 0.04))
        graph.append(f"[{logo_input}:v]format=rgba,scale={width}:-1,"
                     f"colorchannelmixer=aa={float(logo.get('opacity', 0.85)):.2f}[logo];"
                     f"{label}[logo]overlay={x}:{y}:format=auto[v2]")
        label = "[v2]"
    graph.append(f"{label}null[v]")

    audio = None
    if asrc:
        graph.append(f"{asrc}{'loudnorm=I=-14:TP=-1.5:LRA=11' if loudnorm else 'anull'}[speech]")
        audio = "[speech]"
    if music:
        fade = max(0.0, duration - 1.5)
        graph.append(f"[{music_input}:a]volume={float(music.get('volume', 0.15)):.3f},"
                     f"atrim=0:{duration:.3f},afade=t=out:st={fade:.3f}:d=1.5[mus]")
        if audio:
            # the music gets quieter whenever someone speaks
            graph.append("[speech]asplit=2[sp1][sp2];"
                         "[mus][sp2]sidechaincompress=threshold=0.03:ratio=10:attack=15:release=350[duck];"
                         "[sp1][duck]amix=inputs=2:duration=first:normalize=0[amix]")
            audio = "[amix]"
        else:
            audio = "[mus]"
    return ";".join(graph), audio


def _video_codec_args():
    if config.USE_NVENC:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", config.VIDEO_CRF, "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", config.VIDEO_PRESET, "-crf", config.VIDEO_CRF]


def _prepare_ass(lines, preset, out_path, size, hook=None, keywords=None, font=None, colors=None):
    has_captions = bool(lines) and preset != "none"
    if not has_captions and not (hook and hook.get("text")):
        return None
    ass_path = os.path.splitext(out_path)[0] + ".ass"
    write_ass(ass_path, lines if has_captions else [], preset, size[0], size[1], hook=hook, keywords=keywords,
              font=font, colors=colors)
    return ass_path


def _inputs(src, start, end, logo, music):
    args = ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", os.path.abspath(src)]
    idx = 1
    logo_input = music_input = None
    if logo and logo.get("path") and os.path.exists(logo["path"]):
        args += ["-i", os.path.abspath(logo["path"])]
        logo_input, idx = idx, idx + 1
    else:
        logo = None
    if music and music.get("path") and os.path.exists(music["path"]):
        args += ["-stream_loop", "-1", "-i", os.path.abspath(music["path"])]
        music_input = idx
    else:
        music = None
    return args, logo, music, logo_input, music_input


def render_short(src, start, end, plan, out_path, caption_lines=None, preset="bold-yellow",
                 loudnorm=True, has_audio=True, size=None, fast=False, segments=None, hook=None,
                 keywords=None, logo=None, music=None, font=None, colors=None, fonts_dir=None):
    """Render [start, end] of `src` to an MP4 at `out_path`.

    size=(w, h) sets the output resolution; fast=True makes a quick low-quality preview.
    segments: clip-relative (start, end) parts to keep (silence/filler cuts); caption_lines, hook and
    plan keyframes must already use the shortened timeline.
    logo: {"path", "position", "size", "opacity"}; music: {"path", "volume"}; font: caption font family.
    """
    size = size or (config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    duration = sum(b - a for a, b in segments) if segments else end - start
    ass_path = _prepare_ass(caption_lines, preset, out_path, size, hook, keywords, font, colors)
    args, logo, music, logo_input, music_input = _inputs(src, start, end, logo, music)
    graph, audio = build_graph(plan, size, ass_path, end - start if _is_full(segments, end - start) else duration,
                               segments=segments, has_audio=has_audio, logo=logo, music=music,
                               loudnorm=loudnorm and not fast, fonts_dir=fonts_dir,
                               logo_input=logo_input, music_input=music_input)
    args += ["-filter_complex", graph, "-map", "[v]"]
    codec = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", 30] if fast else _video_codec_args()
    args += codec + ["-pix_fmt", "yuv420p"]
    if audio:
        args += ["-map", audio, "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    args += ["-t", f"{duration:.3f}", "-movflags", "+faststart", out_path]
    try:
        _run_in(os.path.dirname(out_path), args)
    finally:
        if ass_path and os.path.exists(ass_path):
            os.remove(ass_path)
    return out_path


def render_still(src, start, end, plan, out_path, caption_lines=None, preset="bold-yellow", at=None,
                 size=None, segments=None, hook=None, keywords=None, logo=None, font=None, colors=None,
                 fonts_dir=None):
    """Render one frame of the final look (framing + captions + hook + logo) as an image."""
    size = size or (config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT)
    out_path = os.path.abspath(out_path)
    duration = sum(b - a for a, b in segments) if segments else end - start
    ass_path = _prepare_ass(caption_lines, preset, out_path, size, hook, keywords, font, colors)
    if at is None:
        at = caption_lines[0]["start"] + 0.05 if caption_lines else min(1.0, duration / 2)
    args, logo, _, logo_input, _ = _inputs(src, start, end, logo, None)
    graph, _ = build_graph(plan, size, ass_path, duration, segments=segments, has_audio=False, logo=logo,
                           fonts_dir=fonts_dir, logo_input=logo_input)
    args += ["-filter_complex", graph, "-map", "[v]", "-ss", f"{at:.3f}", "-frames:v", "1", "-q:v", "3", out_path]
    try:
        _run_in(os.path.dirname(out_path), args)
    finally:
        if ass_path and os.path.exists(ass_path):
            os.remove(ass_path)
    return out_path


def render_preview(src, start, end, out_path, height=360):
    """Fast low-resolution preview of the original (unframed) clip."""
    run_ffmpeg(["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", src,
                "-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", 30,
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out_path])
    return out_path


def thumbnail(video_path, out_path, at=0.8):
    run_ffmpeg(["-ss", at, "-i", video_path, "-frames:v", 1, "-q:v", 3, out_path])
    return out_path


def _run_in(directory, args):
    """Run ffmpeg from `directory` so the .ass file can be referenced by a simple relative name."""
    run_ffmpeg(args, cwd=directory)
