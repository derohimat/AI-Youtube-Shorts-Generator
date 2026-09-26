"""Render a vertical short in a single ffmpeg pass: trim -> frame -> captions -> encode."""
import os

from Components import config
from Components.captions import write_ass
from Components.media import filter_path, run_ffmpeg


def _crop_x_expr(keyframes):
    """Piecewise-constant crop x as an ffmpeg expression (t is clip time)."""
    expr = str(int(keyframes[-1][1]))
    for (t, _), (_, x) in zip(reversed(keyframes[1:]), reversed(keyframes[:-1])):
        expr = f"if(lt(t,{t:.2f}),{int(x)},{expr})"
    return expr


def video_filter(plan, out_w=None, out_h=None):
    """Build the filter_complex that turns [0:v] into a vertical [v0] stream."""
    out_w, out_h = out_w or config.OUTPUT_WIDTH, out_h or config.OUTPUT_HEIGHT
    mode = plan["mode"]
    if mode in ("track", "center"):
        x = _crop_x_expr(plan["keyframes"])
        return (f"[0:v]crop=w={plan['crop_w']}:h={plan['crop_h']}:x='{x}':y=0,"
                f"scale={out_w}:{out_h}:flags=lanczos,setsar=1[v0]")
    if mode == "split":
        (x1, y1), (x2, y2) = plan["boxes"]
        hw, hh, half = plan["half_w"], plan["half_h"], out_h // 2
        return (f"[0:v]split=2[a][b];"
                f"[a]crop={hw}:{hh}:{x1}:{y1},scale={out_w}:{half}:flags=lanczos[top];"
                f"[b]crop={hw}:{hh}:{x2}:{y2},scale={out_w}:{out_h - half}:flags=lanczos[bottom];"
                f"[top][bottom]vstack,setsar=1[v0]")
    # fit-blur: the whole frame over a blurred, zoomed copy of itself.
    return (f"[0:v]split=2[bg][fg];"
            f"[bg]scale={out_w // 4}:{out_h // 4}:force_original_aspect_ratio=increase,"
            f"crop={out_w // 4}:{out_h // 4},boxblur=12:2,eq=brightness=-0.06,scale={out_w}:{out_h}[bgb];"
            f"[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]")


def _full_filter(plan, ass_path, size=None):
    graph = video_filter(plan, *(size or (None, None)))
    if ass_path:
        graph += (f";[v0]ass=filename={filter_path(os.path.basename(ass_path))}"
                  f":fontsdir={filter_path(config.FONTS_DIR)}[v]")
    else:
        graph += ";[v0]null[v]"
    return graph


def _video_codec_args():
    if config.USE_NVENC:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", config.VIDEO_CRF, "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", config.VIDEO_PRESET, "-crf", config.VIDEO_CRF]


def _prepare_ass(lines, preset, out_path, size=None):
    if not lines or preset == "none":
        return None
    ass_path = os.path.splitext(out_path)[0] + ".ass"
    width, height = size or (config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT)
    write_ass(ass_path, lines, preset, width, height)
    return ass_path


def render_short(src, start, end, plan, out_path, caption_lines=None, preset="bold-yellow",
                 loudnorm=True, has_audio=True, size=None, fast=False):
    """Render [start, end] of `src` to a vertical MP4 at `out_path`.

    `size=(w, h)` overrides the output resolution; `fast=True` makes a quick low-quality preview.
    """
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    ass_path = _prepare_ass(caption_lines, preset, out_path, size)
    args = ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", os.path.abspath(src),
            "-filter_complex", _full_filter(plan, ass_path, size), "-map", "[v]"]
    codec = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", 30] if fast else _video_codec_args()
    args += codec + ["-pix_fmt", "yuv420p"]
    if has_audio:
        args += ["-map", "0:a:0"]
        if loudnorm and not fast:
            args += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
        args += ["-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    args += ["-movflags", "+faststart", out_path]
    try:
        _run_in(os.path.dirname(out_path), args)
    finally:
        if ass_path and os.path.exists(ass_path):
            os.remove(ass_path)
    return out_path


def render_still(src, start, end, plan, out_path, caption_lines=None, preset="bold-yellow", at=None):
    """Render one frame of the final look (framing + captions) as an image."""
    out_path = os.path.abspath(out_path)
    ass_path = _prepare_ass(caption_lines, preset, out_path)
    if at is None:
        at = caption_lines[0]["start"] + 0.05 if caption_lines else min(1.0, (end - start) / 2)
    args = ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", os.path.abspath(src),
            "-filter_complex", _full_filter(plan, ass_path), "-map", "[v]",
            "-ss", f"{at:.3f}", "-frames:v", "1", "-q:v", "3", out_path]
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
