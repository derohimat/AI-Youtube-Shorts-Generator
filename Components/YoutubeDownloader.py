"""Download YouTube videos without any interactive prompts.

yt-dlp is used when installed (most reliable), pytubefix is the fallback.
"""
import glob
import os

from Components import config


def download_youtube_video(url, output_dir=None, max_height=None):
    """Download `url` into `output_dir` and return (video_path, title)."""
    output_dir = output_dir or os.path.join(config.WORK_DIR, "downloads")
    max_height = max_height or config.MAX_DOWNLOAD_HEIGHT
    os.makedirs(output_dir, exist_ok=True)
    try:
        return _download_ytdlp(url, output_dir, max_height)
    except ImportError:
        print("yt-dlp not installed, falling back to pytubefix")
    return _download_pytubefix(url, output_dir, max_height)


def _download_ytdlp(url, output_dir, max_height):
    import yt_dlp

    options = {
        "format": (f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
                   f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"),
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    if not os.path.exists(path):
        matches = glob.glob(os.path.join(output_dir, f"{info['id']}.*"))
        path = next((m for m in matches if m.endswith(".mp4")), matches[0] if matches else path)
    return path, info.get("title") or info["id"]


def _download_pytubefix(url, output_dir, max_height):
    from pytubefix import YouTube
    from Components.media import run_ffmpeg

    yt = YouTube(url)
    streams = yt.streams.filter(type="video").order_by("resolution").desc()
    fitting = [s for s in streams if s.resolution and int(s.resolution.rstrip("p")) <= max_height]
    stream = (fitting or list(streams))[0]
    print(f"Downloading {yt.title} ({stream.resolution})")

    video_file = stream.download(output_path=output_dir, filename=f"{yt.video_id}_video")
    if stream.is_progressive:
        return video_file, yt.title

    audio_file = yt.streams.filter(only_audio=True).order_by("abr").desc().first().download(
        output_path=output_dir, filename=f"{yt.video_id}_audio")
    output_file = os.path.join(output_dir, f"{yt.video_id}.mp4")
    run_ffmpeg(["-i", video_file, "-i", audio_file, "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy" if video_file.endswith(".mp4") else "libx264", "-c:a", "aac", output_file])
    os.remove(video_file)
    os.remove(audio_file)
    return output_file, yt.title


if __name__ == "__main__":
    print(download_youtube_video(input("Enter YouTube video URL: ")))
