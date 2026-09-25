"""Small ffmpeg/ffprobe helpers shared by the pipeline."""
import json
import subprocess

from Components import config


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(args, quiet=True, cwd=None):
    """Run ffmpeg with the given arguments (without the binary name)."""
    cmd = [config.FFMPEG, "-hide_banner", "-y"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += [str(a) for a in args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({result.returncode}): {result.stderr.strip()[-2000:]}")
    return result


def probe(path):
    """Return basic information about a media file."""
    cmd = [config.FFPROBE, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    audio = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    info = {
        "duration": float(data["format"].get("duration", 0.0)),
        "has_audio": audio is not None,
        "width": 0, "height": 0, "fps": 30.0,
    }
    if video:
        width, height = int(video["width"]), int(video["height"])
        rotation = _rotation(video)
        if rotation in (90, 270):
            width, height = height, width
        info.update(width=width, height=height, fps=_parse_rate(video.get("avg_frame_rate")) or 30.0)
        if not info["duration"] and video.get("duration"):
            info["duration"] = float(video["duration"])
    return info


def _rotation(stream):
    try:
        if "rotate" in stream.get("tags", {}):
            return abs(int(stream["tags"]["rotate"])) % 360
        for side in stream.get("side_data_list", []):
            if "rotation" in side:
                return abs(int(side["rotation"])) % 360
    except (TypeError, ValueError):
        pass
    return 0


def _parse_rate(rate):
    try:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else None
    except (AttributeError, ValueError, ZeroDivisionError):
        return None


def extract_audio(video_path, audio_path, sample_rate=16000):
    """Extract a mono WAV track suitable for Whisper."""
    run_ffmpeg(["-i", video_path, "-vn", "-ac", 1, "-ar", sample_rate, "-c:a", "pcm_s16le", audio_path])
    return audio_path


def filter_path(path):
    """Escape a file path for use as an ffmpeg filter option value."""
    return (str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            .replace(",", "\\,").replace("[", "\\[").replace("]", "\\]"))
