"""
Transcribes a video/audio file to word-level timestamped text.

Changes in this version:
  - large-v3-turbo added (8× faster than large-v3, near-identical accuracy)
  - beam_size=3 (better accuracy vs beam_size=2, still fast)
  - initial_prompt seeds Whisper with common Taglish words so it handles
    code-switching better from the very first segment
  - PROGRESS:N emitted per segment for real % bar in UI
  - Audio extracted to 16 kHz mono WAV via system ffmpeg (bypasses PyAV DLLs)
  - CUDA int8 → CPU int8 automatic fallback
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Callable, List, Optional

from faster_whisper import WhisperModel
from types_common import Word


# ── Language map ─────────────────────────────────────────────────────────
LANGUAGE_MAP: dict[str, Optional[str]] = {
    "Auto-detect (best for Taglish)": None,
    "Filipino / Tagalog":             "tl",
    "English":                        "en",
    "Japanese":                       "ja",
    "Korean":                         "ko",
    "Spanish":                        "es",
}

MODEL_SIZES = [
    "tiny", "small", "medium",
    "large-v2", "large-v3", "large-v3-turbo",
]

MODEL_HINTS: dict[str, str] = {
    "tiny":            "fastest · lower accuracy",
    "small":           "fast · decent accuracy",
    "medium":          "recommended balance",
    "large-v2":        "high accuracy · slower",
    "large-v3":        "best accuracy · slowest",
    "large-v3-turbo":  "best accuracy · 8× faster than large-v3 ✦",
}

# Taglish seed prompt — common Filipino words + English mix.
# This helps Whisper orient itself to code-switched speech from segment 1.
_TAGLISH_PROMPT = (
    "Kumusta, oo, hindi, sige, talaga, ganun, kasi, pero, tapos, "
    "parang, yung, diba, naman, lang, pala, nga, din, doon, dito, "
    "kami, kayo, sila, ako, ikaw, siya, namin, ninyo, nila, "
    "mag-aaral, mahal, gusto, pwede, salamat, po, ate, kuya, "
    "okay, so, like, right, actually, basically, you know, "
)


# ── DLL error detection ───────────────────────────────────────────────────
_CUDA_KEYWORDS = (
    "cublas", "cudart", "cudnn", "cufft", "cusolver",
    "curand", "nvinfer", "dll", "cannot be loaded",
    "not found", "no such file",
)

def _is_dll_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in _CUDA_KEYWORDS)


# ── Model helpers ─────────────────────────────────────────────────────────
def _make_model(size: str, device: str, compute: str) -> WhisperModel:
    return WhisperModel(
        size, device=device, compute_type=compute,
        num_workers=4, cpu_threads=0,
    )

def _load_model(size: str, log: Callable[[str], None]) -> WhisperModel:
    """CUDA int8 → CPU int8 with DLL-aware fallback."""
    try:
        log(f"Loading '{size}' [cuda/int8]…")
        m = _make_model(size, "cuda", "int8")
        log("Model ready — CUDA / int8.")
        return m
    except Exception as exc:
        if _is_dll_error(exc):
            log("  CUDA DLLs missing → CPU mode.")
        else:
            log(f"  CUDA failed: {exc} → CPU mode.")

    log(f"Loading '{size}' [cpu/int8]…")
    m = _make_model(size, "cpu", "int8")
    log("Model ready — CPU / int8.")
    return m


# ── Audio extraction ──────────────────────────────────────────────────────
def _extract_audio(path: str, log: Callable[[str], None]) -> tuple[str, float]:
    """16 kHz mono WAV + duration via system ffmpeg. Returns (wav_path, dur)."""
    ffmpeg  = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found on PATH.\n"
            "Install from https://ffmpeg.org/download.html "
            "and add its bin\\ folder to PATH."
        )
    dur = 0.0
    if ffprobe:
        try:
            raw = subprocess.check_output(
                [ffprobe, "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
            dur = float(raw)
        except Exception:
            pass

    wav = os.path.join(tempfile.gettempdir(), "taglishcap_audio.wav")
    log("Extracting audio…")
    r = subprocess.run(
        [ffmpeg, "-y", "-i", path,
         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", wav],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"Audio extraction failed (code {r.returncode}).\n{r.stderr[-600:]}"
        )
    kb = os.path.getsize(wav) // 1024
    log(f"Audio ready — {kb} KB, {dur:.1f} s")
    return wav, dur


# ── Main entry point ──────────────────────────────────────────────────────
def transcribe(
    media_path: str,
    model_size: str = "medium",
    language_label: str = "Auto-detect (best for Taglish)",
    progress_cb: Optional[Callable[[str], None]] = None,
) -> List[Word]:
    """
    Returns List[Word(text, start, end)].

    Emits to progress_cb:
      "PROGRESS:N"  — N = 0-100 for progress bar
      other strings — human-readable log lines
    """
    def log(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    if not os.path.isfile(media_path):
        raise RuntimeError(f"File not found: {media_path}")

    lang = LANGUAGE_MAP.get(language_label)

    # Use Taglish seed prompt only when auto-detecting or Filipino
    use_prompt = lang in (None, "tl")
    initial_prompt = _TAGLISH_PROMPT if use_prompt else None

    wav, dur = _extract_audio(media_path, log)

    try:
        model = _load_model(model_size, log)
        log("Transcribing…")

        def _run(mdl: WhisperModel):
            return mdl.transcribe(
                wav,
                language=lang,
                task="transcribe",
                initial_prompt=initial_prompt,
                beam_size=3,                        # accuracy/speed balance
                best_of=1,
                patience=1.0,
                temperature=[0.0, 0.2, 0.4],
                compression_ratio_threshold=2.4,
                log_prob_threshold=-1.0,
                no_speech_threshold=0.6,
                condition_on_previous_text=True,
                chunk_length=20,
                word_timestamps=True,
                prepend_punctuations="\"'¿([{-",
                append_punctuations="\"'.。,，!！?？:：)]}、",
                vad_filter=True,
                vad_parameters=dict(
                    threshold=0.45,
                    min_speech_duration_ms=200,
                    max_speech_duration_s=float("inf"),
                    min_silence_duration_ms=350,
                    speech_pad_ms=150,
                ),
            )

        try:
            segs, info = _run(model)
        except Exception as exc:
            if _is_dll_error(exc):
                log("CUDA DLL error → retrying on CPU…")
                model = _make_model(model_size, "cpu", "int8")
                try:
                    segs, info = _run(model)
                except Exception as e2:
                    raise RuntimeError(f"Transcription failed (CPU): {e2}") from e2
            else:
                raise RuntimeError(f"Transcription setup failed: {exc}") from exc

        detected = info.language if lang is None else lang
        log(f"Language: {detected} ({info.language_probability:.0%})")

        words: List[Word] = []
        last_pct = -1
        seg = None

        try:
            for seg in segs:
                if not seg.words:
                    continue
                for w in seg.words:
                    txt = w.word.strip()
                    if not txt:
                        continue
                    s = max(0.0, w.start)
                    e = max(s + 0.01, w.end)
                    words.append(Word(text=txt, start=s, end=e))

                if dur > 0:
                    pct = min(99, int(seg.end / dur * 100))
                    if pct != last_pct:
                        log(f"PROGRESS:{pct}")
                        last_pct = pct

                log(f"  {seg.end:.1f}s — {len(words)} words")

        except Exception as exc:
            if _is_dll_error(exc):
                raise RuntimeError(
                    "CUDA DLL failed mid-transcription.\n"
                    "Restart the app — it will use CPU automatically.\n"
                    f"Detail: {exc}"
                ) from exc
            if words:
                pos = f"{seg.end:.1f}s" if seg else "?"
                log(f"Warning: interrupted at {pos} — "
                    f"returning {len(words)} partial words.")
                return words
            raise RuntimeError(
                f"Transcription failed before any words captured.\n{exc}"
            ) from exc

        if not words:
            raise RuntimeError(
                "No speech detected. Try a larger model, confirm the video "
                "has an audio track, or select a specific language."
            )

        log("PROGRESS:100")
        log(f"Done — {len(words)} words.")
        return words

    finally:
        try:
            if os.path.exists(wav):
                os.remove(wav)
        except Exception:
            pass
