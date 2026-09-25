"""Central configuration, read from environment variables / .env."""
import os
import shutil

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(name, default=None):
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_bool(name, default=False):
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# LLM used for picking highlights and writing titles/hashtags.
# openai | anthropic | gemini | ollama | heuristic (no API key needed)
LLM_PROVIDER = _env("LLM_PROVIDER", "openai").lower()
LLM_MODEL = _env("LLM_MODEL")  # None -> provider default (see highlights.DEFAULT_MODELS)
LLM_TEMPERATURE = _env("LLM_TEMPERATURE")  # None -> provider default

# Keep backward compatibility with the original OPENAI_API variable name.
OPENAI_API_KEY = _env("OPENAI_API_KEY", _env("OPENAI_API"))
# Any OpenAI-compatible gateway, e.g. https://ai.paas.id (None -> api.openai.com)
OPENAI_BASE_URL = _env("OPENAI_BASE_URL")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
GOOGLE_API_KEY = _env("GOOGLE_API_KEY")
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434")

# Speech-to-text
WHISPER_MODEL = _env("WHISPER_MODEL", "small")
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE")  # None -> auto-detect

# Folders
WORK_DIR = os.path.abspath(_env("WORK_DIR", os.path.join(ROOT_DIR, "work")))
OUTPUT_DIR = os.path.abspath(_env("OUTPUT_DIR", os.path.join(ROOT_DIR, "output")))
FONTS_DIR = os.path.join(ROOT_DIR, "fonts")
MODELS_DIR = os.path.join(ROOT_DIR, "models")

# Rendering
OUTPUT_WIDTH = int(_env("OUTPUT_WIDTH", 1080))
OUTPUT_HEIGHT = int(_env("OUTPUT_HEIGHT", 1920))
VIDEO_CRF = int(_env("VIDEO_CRF", 20))
VIDEO_PRESET = _env("VIDEO_PRESET", "veryfast")
USE_NVENC = _env_bool("USE_NVENC", False)
MAX_DOWNLOAD_HEIGHT = int(_env("MAX_DOWNLOAD_HEIGHT", 1080))

FFMPEG = _env("FFMPEG_BINARY", shutil.which("ffmpeg") or "ffmpeg")
FFPROBE = _env("FFPROBE_BINARY", shutil.which("ffprobe") or "ffprobe")
