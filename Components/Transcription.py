"""Word-level speech-to-text with faster-whisper."""

_MODEL_CACHE = {}


def _load_model(model_size):
    from faster_whisper import WhisperModel

    if model_size not in _MODEL_CACHE:
        import ctranslate2
        cuda = ctranslate2.get_cuda_device_count() > 0
        device, compute_type = ("cuda", "float16") if cuda else ("cpu", "int8")
        print(f"Loading Whisper '{model_size}' on {device}")
        _MODEL_CACHE[model_size] = WhisperModel(model_size, device=device, compute_type=compute_type)
    return _MODEL_CACHE[model_size]


def transcribeAudio(audio_path, model_size=None, language=None):
    """Transcribe audio and return a transcript dict.

    {"language": "en", "segments": [{"text", "start", "end", "words": [{"w", "s", "e"}]}]}
    """
    from Components import config

    model = _load_model(model_size or config.WHISPER_MODEL)
    segments, info = model.transcribe(
        audio=audio_path,
        beam_size=5,
        language=language or config.WHISPER_LANGUAGE,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    result = []
    for segment in segments:
        words = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
                 for w in (segment.words or []) if w.word.strip()]
        result.append({"text": segment.text.strip(), "start": round(segment.start, 3),
                       "end": round(segment.end, 3), "words": words})
    print(f"✓ Transcription complete: {len(result)} segments, language={info.language}")
    return {"language": info.language, "segments": result}


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(transcribeAudio(sys.argv[1] if len(sys.argv) > 1 else "audio.wav"), indent=1))
