"""Check that faster-whisper can use the GPU."""
import os
import wave

import ctranslate2
from faster_whisper import WhisperModel


def test_cuda():
    count = ctranslate2.get_cuda_device_count()
    print(f"CUDA devices visible to CTranslate2: {count}")
    if not count:
        print("CUDA is NOT available - transcription will run on CPU (slower but works).")
        return

    print("Loading WhisperModel on CUDA...")
    model = WhisperModel("tiny", device="cuda", compute_type="float16")
    with wave.open("test_audio.wav", "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00" * 32000)  # 1 second of silence
    segments, _ = model.transcribe("test_audio.wav")
    list(segments)
    os.remove("test_audio.wav")
    print("GPU transcription works.")


if __name__ == "__main__":
    test_cuda()
