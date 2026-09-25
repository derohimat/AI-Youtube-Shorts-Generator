"""Command-line interface. For the point-and-click web UI run: python app.py

Examples:
    python main.py "https://youtu.be/VIDEO_ID"
    python main.py video.mp4 --clips 3 --max 45 --style clean-white --auto-approve
    xargs -a urls.txt -I{} python main.py --auto-approve {}
"""
import argparse
import sys

from Components import pipeline
from Components.captions import DEFAULT_PRESET, PRESETS
from Components.framing import MODES
from Components.highlights import PROVIDERS


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Turn a long video into vertical shorts.")
    parser.add_argument("source", nargs="?", help="YouTube URL or local video file")
    parser.add_argument("--clips", type=int, default=3, help="number of clips to find (default 3)")
    parser.add_argument("--min", dest="min_len", type=int, default=20, help="minimum clip length in seconds")
    parser.add_argument("--max", dest="max_len", type=int, default=60, help="maximum clip length in seconds")
    parser.add_argument("--style", default=DEFAULT_PRESET, choices=list(PRESETS), help="caption style")
    parser.add_argument("--framing", default="auto", choices=MODES, help="how to fit the video in 9:16")
    parser.add_argument("--provider", choices=PROVIDERS, help="LLM provider (default from .env)")
    parser.add_argument("--model", help="LLM model name (default per provider)")
    parser.add_argument("--language", help="spoken language code, e.g. en, id, es (default: auto-detect)")
    parser.add_argument("--instructions", default="", help='what to look for, e.g. "funny moments"')
    parser.add_argument("--no-loudnorm", action="store_true", help="keep the original audio loudness")
    parser.add_argument("--auto-approve", action="store_true", help="render all found clips without asking")
    return parser.parse_args(argv)


def choose_clips(clips):
    print("\nSuggested clips:")
    for clip in clips:
        print(f"  [{clip['id']}] {pipeline.format_time(clip['start'])}-{pipeline.format_time(clip['end'])} "
              f"({clip['end'] - clip['start']:.0f}s) score {clip['score']}/10  {clip['title']}")
        if clip.get("reason"):
            print(f"       {clip['reason']}")
    answer = input("\nClip numbers to render (e.g. 1,3), Enter = all, q = quit: ").strip().lower()
    if answer == "q":
        sys.exit(0)
    if not answer:
        return clips
    wanted = {int(x) for x in answer.replace(" ", ",").split(",") if x.isdigit()}
    return [c for c in clips if c["id"] in wanted]


def main(argv=None):
    args = parse_args(argv)
    source = args.source or input("Enter YouTube video URL or local video file path: ")

    project = pipeline.prepare(source, language=args.language)
    clips = pipeline.suggest_clips(project, args.clips, args.min_len, args.max_len, args.instructions,
                                   provider=args.provider, model=args.model)
    if not clips:
        print("No suitable clips found.")
        return 1
    if not args.auto_approve:
        clips = choose_clips(clips)

    for clip in clips:
        result = pipeline.render_clip(project, clip, style=args.style, framing=args.framing,
                                      loudnorm=not args.no_loudnorm)
        print(f"\n✓ {result['video']}\n{result['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
