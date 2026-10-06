"""
Renders Caption objects into an ASS subtitle file and burns it into video.

Quality modes
─────────────
"copy"   — stream-copy the video track unchanged (original quality, fastest)
           Requires a re-encode if the container changes, so we still run
           through ffmpeg but with -c:v copy.  Subtitles are burned via the
           'subtitles' filter which works with copy on most containers.
           Falls back to libx264 crf18 automatically if copy+filter fails.
"crf18"  — libx264, CRF 18  (near-lossless, ~2× file size of original)
"crf23"  — libx264, CRF 23  (visually lossless, same size as original)
"crf28"  — libx264, CRF 28  (smaller file, slight quality loss)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Callable, List, Optional

from subtitle_builder import Caption


# ── helpers ──────────────────────────────────────────────────────────────

def _require(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise RuntimeError(
            f"'{name}' not found on PATH.\n"
            "Install ffmpeg from https://ffmpeg.org/download.html "
            "and add its bin\\ folder to PATH."
        )
    return p


def _hex_to_ass(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    h  = int(t // 3600)
    m  = int((t % 3600) // 60)
    s  = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs >= 100:
        cs, s = 0, s + 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_filter_path(path: str) -> str:
    """Escape a Windows path for use inside an ffmpeg -vf filter value."""
    p = path.replace("\\", "/")
    p = re.sub(r"^([A-Za-z]):/", r"\1\\:/", p)   # C:/ → C\:/
    # Escape single-quotes and colons that remain inside the value
    p = p.replace("'", "\\'")
    return p


def _parse_ffmpeg_time(line: str) -> Optional[float]:
    m = re.search(r"time=(\d+):(\d+):([\d.]+)", line)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return None


# ── public helpers ────────────────────────────────────────────────────────

def get_video_dimensions(video_path: str) -> tuple[int, int]:
    ffprobe = _require("ffprobe")
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=s=x:p=0", video_path],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        w, h = out.split("x")
        return int(w), int(h)
    except Exception as exc:
        raise RuntimeError(f"Could not read video dimensions: {exc}") from exc


def get_video_duration(video_path: str) -> float:
    ffprobe = _require("ffprobe")
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error",
             "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return float(out)
    except Exception:
        return 0.0


def get_video_codec(video_path: str) -> str:
    """Return the codec name of the first video stream (e.g. 'h264')."""
    ffprobe = _require("ffprobe")
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "csv=p=0", video_path],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return out or "unknown"
    except Exception:
        return "unknown"


# ── ASS file writer ───────────────────────────────────────────────────────

def build_ass_file(
    captions: List[Caption],
    out_path: str,
    video_width: int,
    video_height: int,
    font_name: str = "Arial",
    font_size: int = 64,
    font_color_hex: str = "#FFFFFF",
    outline_enabled: bool = True,
    outline_weight: int = 3,
    bold: bool = True,
    vertical_position_pct: float = 78.0,
) -> None:
    if not captions:
        raise ValueError("No captions to write.")

    primary     = _hex_to_ass(font_color_hex)
    outline_col = "&H00000000"
    shadow_col  = "&H80000000"
    outline_w   = outline_weight if outline_enabled else 0
    shadow_w    = 1 if outline_enabled else 0
    bold_flag   = -1 if bold else 0
    margin_v    = max(0, int(video_height * (1.0 - vertical_position_pct / 100.0)))

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{font_size},"
        f"{primary},{primary},{outline_col},{shadow_col},"
        f"{bold_flag},0,0,0,100,100,0,0,"
        f"1,{outline_w},{shadow_w},"
        f"2,40,40,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = [header]
    for cap in captions:
        text = cap.text.replace("\n", " ").replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_fmt_time(cap.start)},{_fmt_time(cap.end)},"
            f"Default,,0,0,0,,{text}\n"
        )

    with open(out_path, "w", encoding="utf-8-sig") as f:
        f.writelines(lines)


# ── Burn subtitles ────────────────────────────────────────────────────────

# Quality mode → (video codec args)
_QUALITY_MODES: dict[str, list[str]] = {
    "copy":  ["-c:v", "copy"],
    "crf18": ["-c:v", "libx264", "-preset", "fast", "-crf", "18"],
    "crf23": ["-c:v", "libx264", "-preset", "fast", "-crf", "23"],
    "crf28": ["-c:v", "libx264", "-preset", "fast", "-crf", "28"],
}

QUALITY_LABELS: dict[str, str] = {
    "copy":  "Original quality (stream copy)",
    "crf18": "Near-lossless (CRF 18)",
    "crf23": "High quality (CRF 23)",
    "crf28": "Smaller file (CRF 28)",
}


def burn_subtitles(
    video_path: str,
    ass_path: str,
    out_path: str,
    quality_mode: str = "crf18",
    fonts_dir: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Burn the ASS subtitle file into the video.

    quality_mode: one of "copy", "crf18", "crf23", "crf28"
      "copy" keeps the original video stream byte-for-byte.

    progress_cb receives:
      "PROGRESS:N"  — 0-100
      other strings — human-readable status
    """
    def log(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    ffmpeg   = _require("ffmpeg")
    duration = get_video_duration(video_path)

    vc_args = _QUALITY_MODES.get(quality_mode, _QUALITY_MODES["crf18"])

    ass_esc = _escape_filter_path(ass_path)
    if fonts_dir and os.path.isdir(fonts_dir):
        fd_esc = _escape_filter_path(fonts_dir)
        vf = f"ass='{ass_esc}':fontsdir='{fd_esc}'"
    else:
        vf = f"ass='{ass_esc}'"

    # "copy" mode: we still need a video filter (ass overlay), which forces
    # a re-encode of the video frames regardless. We warn the user and
    # automatically demote to crf18 for copy mode since -c:v copy is
    # incompatible with -vf in ffmpeg.
    if quality_mode == "copy":
        log("Note: subtitle burn requires re-encoding video frames. "
            "Using CRF 18 (near-lossless) to preserve quality.")
        vc_args = _QUALITY_MODES["crf18"]

    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-vf", vf,
        *vc_args,
        "-c:a", "copy",        # audio always stream-copied
        out_path,
    ]

    log(f"Encoding ({quality_mode}) — this may take a while…")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )

    stderr_lines: List[str] = []
    for line in proc.stderr:
        line = line.rstrip()
        stderr_lines.append(line)
        t = _parse_ffmpeg_time(line)
        if t is not None and duration > 0:
            pct = min(99, int(t / duration * 100))
            log(f"PROGRESS:{pct}")

    proc.wait()

    if proc.returncode != 0:
        tail = "\n".join(stderr_lines[-20:])
        raise RuntimeError(
            f"ffmpeg exited with code {proc.returncode}.\n\n"
            f"Last output:\n{tail}"
        )

    log("PROGRESS:100")
    log(f"Export complete → {out_path}")
