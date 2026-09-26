# AI YouTube Shorts Generator

Turn long videos (podcasts, interviews, tutorials, streams) into ready-to-post vertical shorts for YouTube Shorts, TikTok and Instagram Reels, **without any editing skills**.

Paste a link → the AI finds the best moments → you tick the ones you like → download vertical 1080×1920 videos with animated captions, plus a title, description and hashtags for each one.

![longshorts](https://github.com/user-attachments/assets/3f5d1abf-bf3b-475f-8abf-5e253003453a)

## Features

- **🖱️ Web UI, no editor needed**: a local web page walks you through: 1. video, 2. pick clips, 3. preview style, 4. download.
- **🤖 Several clips per video**: the AI returns up to 10 ranked clips, each with a virality score, the reason it works, a title, a description and hashtags. Click "Find more" to get different ones.
- **✂️ Clean cuts**: clip start and end snap to sentence and word boundaries, so cuts never land mid-word. You can nudge the times in the table.
- **💬 Viral-style captions**: word-by-word captions that highlight the current word. Presets: *Bold yellow*, *Clean white*, *Boxed*, *Minimal*, *None*. Fix typos in the caption table before rendering.
- **🎯 Smart framing**:
  - **Auto** follows the active speaker with clean camera cuts. Screen recordings with no faces get *fit + blurred background*.
  - **Split screen** stacks two people top and bottom.
  - **Center crop** and **whole video on blurred background** are also available.
- **⚡ Fast single-pass render**: one ffmpeg encode per clip (the old pipeline encoded 4 times). Optional loudness normalization and NVIDIA NVENC.
- **🌍 Any language**: Whisper detects the language automatically. Titles and hashtags are written in the video's language.
- **🔌 Choose your AI**: OpenAI (default), Anthropic Claude, Google Gemini, local Ollama (free), or `heuristic` (no API key needed, basic quality).
- **📦 Organized output**: `output/<video>/01-<clip-title>.mp4` plus `.txt` (title, description, hashtags), a `.jpg` thumbnail, and `shorts.zip`.
- **💾 Caching**: downloads and transcripts are cached, so re-running the same video skips straight to clip selection.

## Try it in your browser (GitHub Codespaces, nothing to install)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/derohimat/AI-Youtube-Shorts-Generator?quickstart=1)

1. Click the badge above, or go to **Code → Codespaces → Create codespace on main**.
2. When asked, paste your OpenAI key into the `OPENAI_API` secret. You can skip it and pick the `heuristic` AI provider in the UI.
3. Wait for setup to finish (about 3–5 minutes the first time). The web UI opens automatically at `https://<your-codespace>-7860.app.github.dev`. If it doesn't, open the **Ports** tab and click the globe icon next to port 7860.

Notes:
- The link is private to your GitHub account by default. Codespaces are free for personal accounts up to 120 core-hours per month, and they stop automatically when idle.
- Codespaces have no GPU, so transcription runs on the CPU. With the default `small` model, a 10-minute video takes a few minutes. Set `WHISPER_MODEL=base` in `.env` for faster, less accurate transcripts.
- YouTube sometimes blocks downloads from cloud servers ("Sign in to confirm you're not a bot"). If that happens, download the video yourself and use the upload box instead.
- To restart the app, run `python app.py` in the terminal.

## Installation

### Prerequisites

- Python 3.10+
- FFmpeg (with libass, which the standard packages include)
- NVIDIA GPU with CUDA (optional; it speeds up transcription)
- An API key for your chosen AI provider (or use `heuristic` / `ollama` for free)

### Steps

```bash
git clone https://github.com/SamurAIGPT/AI-Youtube-Shorts-Generator.git
cd AI-Youtube-Shorts-Generator

sudo apt install -y ffmpeg          # macOS: brew install ffmpeg

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# optional, for Claude / Gemini / Ollama:
# pip install -r requirements-extra.txt

cp .env.example .env                # then put your API key in .env
```

## Usage

### Web UI (recommended)

```bash
./run.sh                 # or: python app.py
```

Open **http://127.0.0.1:7860** and:

1. **Your video**: paste a YouTube link or upload a file. Under *Options*, set how many clips you want, the min/max length, and what the AI should look for (e.g. "funny moments", "practical tips"). Click **Find the best clips**.
2. **Pick & fine-tune**: review the ranked clips. Edit **Start**, **End** (`m:ss`) or **Title** directly in the table.
3. **Preview & style**: choose a caption style and framing, then click **Preview** to watch the original moment and see a still of how the short will look. Fix caption typos and click **Save caption edits**.
4. **Render & download**: tick the clips you want and click **Render**. Download the zip or individual files, and copy the titles and hashtags.

Use `./run.sh --ui --share` to get a temporary public link, for example to use the tool from your phone.

### History: continue later

Every video you process is saved automatically: the download, the transcript, the suggested clips, your start/end/title and caption edits, your style settings, and the rendered shorts. Open the **🕘 History** tab to:

- see all earlier videos, with the number of clips and renders
- watch and download previous renders again
- click **Open & continue** to load everything back into the Create tab. You can edit, preview or **render again without downloading or transcribing the video again.**
- **Delete from history** to free disk space. This removes the cached download and transcript in `work/`; finished shorts in `output/` are kept.

Uploaded videos are copied into `work/uploads/` so they can be reopened after a restart. Uploading the same file again reuses the same project. Projects created from the command line appear in History too.

### Command line

```bash
./run.sh "https://youtu.be/VIDEO_ID"                         # lists clips, you pick numbers
./run.sh video.mp4 --clips 5 --min 15 --max 45 --style clean-white --framing auto
./run.sh "https://youtu.be/VIDEO_ID" --auto-approve          # render all found clips
./run.sh --help                                              # all options
```

Batch processing:

```bash
xargs -a urls.txt -I{} ./run.sh --auto-approve {}
```

### Docker

```bash
docker-compose up                    # web UI on http://localhost:7860
docker-compose run youtube-shorts-generator ./run.sh "https://youtu.be/VIDEO_ID" --auto-approve
```

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai`, `anthropic`, `gemini`, `ollama`, `heuristic` |
| `LLM_MODEL` | per provider | e.g. `gpt-4o-mini`, `claude-haiku-4-5-20251001`, `gemini-2.5-flash`, `llama3.1` |
| `OPENAI_API` / `OPENAI_API_KEY` | | OpenAI key |
| `OPENAI_BASE_URL` | api.openai.com | any OpenAI-compatible gateway, e.g. `https://ai.paas.id` (use your gateway key as `OPENAI_API`) |
| `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` | | keys for the other providers |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | local Ollama server |
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3` |
| `WHISPER_LANGUAGE` | auto | force a language, e.g. `en`, `id` |
| `OUTPUT_DIR` / `WORK_DIR` | `output` / `work` | where shorts and cache files go |
| `OUTPUT_WIDTH` / `OUTPUT_HEIGHT` | `1080` / `1920` | output resolution |
| `VIDEO_CRF` / `VIDEO_PRESET` | `20` / `veryfast` | x264 quality and speed |
| `USE_NVENC` | `false` | encode on an NVIDIA GPU |
| `MAX_DOWNLOAD_HEIGHT` | `1080` | max YouTube download resolution |

Caption styles are defined in `Components/captions.py` (`PRESETS`), where you can change fonts, colors, size and position. Fonts in `fonts/` are loaded automatically (Anton is bundled under the SIL Open Font License).

### Using an OpenAI-compatible gateway (e.g. paas.id)

Set these in `.env` (or as Codespaces secrets):

```bash
LLM_PROVIDER=openai
OPENAI_API=your_gateway_api_key
OPENAI_BASE_URL=https://ai.paas.id
LLM_MODEL=model-name-from-your-gateway
```

You can also pick or type the model name in the web UI under *Options → Model*. The list changes with the AI provider: `openai` (OpenAI or your gateway) lists GPT, Gemini and Claude names, `anthropic` lists Claude, and `gemini` lists Gemini. For `anthropic` and `gemini`, run `pip install -r requirements-extra.txt` (Codespaces installs these for you) and set `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`. If the chosen model doesn't support tool calling, the app automatically switches to plain JSON replies.

## How it works

```
app.py (web UI) / main.py (CLI)
        └── Components/pipeline.py
              ├── YoutubeDownloader.py  download (yt-dlp, pytubefix fallback), cached in work/
              ├── Transcription.py      faster-whisper with word timestamps + language detection
              ├── highlights.py         LLM → ranked clips + metadata, snapped to sentence boundaries
              ├── framing.py            face detection → speaker-following crop / split / blur-fit plan
              ├── captions.py           word-by-word ASS captions with style presets
              └── render.py             one ffmpeg pass: trim → frame → captions → encode
```

## Troubleshooting

- **Transcription is slow**: use a GPU or a smaller `WHISPER_MODEL` (`base`). Run `python verify_cuda.py` to check GPU support.
- **YouTube download fails**: update the downloader with `pip install -U yt-dlp`.
- **"API key missing"**: check `.env`, or set `LLM_PROVIDER=heuristic` to try the tool without a key.
- **Wrong person framed**: pick *Center crop* or *Split screen* in the UI, or change the time range.
- **Run the tests**: `pip install pytest && python -m pytest tests`

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request.

## License

This project is licensed under the MIT License. The bundled Anton font is licensed under the SIL Open Font License 1.1 (`fonts/OFL-Anton.txt`).

## Related Projects

- [AI Influencer Generator](https://github.com/SamurAIGPT/AI-Influencer-Generator)
- [Text to Video AI](https://github.com/SamurAIGPT/Text-To-Video-AI)
- [Faceless Video Generator](https://github.com/SamurAIGPT/Faceless-Video-Generator)
- [AI B-roll Generator](https://github.com/Anil-matcha/AI-B-roll)
- [No-code YouTube Shorts Generator](https://www.vadoo.tv/clip-youtube-video)
