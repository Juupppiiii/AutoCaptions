# TaglishCap — Taglish Auto-Subtitle App

Import a video → it transcribes Filipino/English (Taglish) speech with word-level
timing → auto-splits into short 1–4 word CapCut-style caption lines → you style
the text → export a new video with the subtitles burned in.

## What it does
- Speech-to-text via OpenAI Whisper (through `faster-whisper`), which handles
  Filipino/English code-switching and varied accents better than most engines.
  You can also lock the language to Filipino-only or English-only if your video
  isn't mixed.
- Groups words into short lines (you choose 1–4 words max) the way CapCut-style
  captions read — breaking on natural pauses and punctuation, not just a fixed
  word count.
- Style controls: font (pulled live from every font installed on your PC),
  font size, font color, outline on/off with adjustable weight (outline color
  is fixed black, per your spec), CAPITALIZE toggle, bold toggle, and vertical
  position on screen.
- Live style preview on an actual frame from your video before you commit to
  a full export.
- Export burns everything into a new .mp4 — original stays untouched.

## Running it (either way works)

### Option A — just run the Python app (fastest to try)
1. Install Python 3.10+ from python.org (check "Add to PATH" during install).
2. Install **ffmpeg** and make sure it's on your PATH (download a Windows
   build from ffmpeg.org, unzip, add the `bin` folder to PATH). Test with
   `ffmpeg -version` in a new terminal.
3. Open a terminal in this folder and run:
   ```
   pip install -r requirements.txt
   python main.py
   ```
   First run downloads the Whisper model (a few hundred MB to ~1.5GB
   depending on the accuracy setting you pick) — needs internet once.

### Option B — build the standalone .exe
1. Do steps 1–2 above.
2. Double-click **`build_exe.bat`** (or run it from a terminal). It installs
   dependencies and packages everything with PyInstaller.
3. Your app is at `dist\TaglishCap\TaglishCap.exe`. Keep the whole
   `TaglishCap` folder together (DLLs and model cache live alongside the exe).
   From then on it's just double-click and go — no Python needed on the
   machine that runs it.

> Note: I built and source-tested this app's logic (word-chunking, subtitle
> styling, and the actual ffmpeg subtitle-burn) in my own environment and
> confirmed it works end-to-end. I can't produce a Windows .exe binary from
> here directly (that has to be compiled on a Windows machine), which is why
> step 2 above exists — it's a one-time, one-click thing.

## Using the app
1. **Import Video** — pick your .mp4/.mov/.mkv file (vertical or not).
2. **Transcribe** — pick language (Auto-detect works well for genuine Taglish;
   pick Filipino or English if your video is single-language) and an accuracy
   level (`medium` is a good default; use `large-v3` for heavy accents or
   noisy audio, `small` for a fast draft).
3. Adjust style in the right panel — watch the live preview update.
4. **Export** — choose where to save; ffmpeg burns the subtitles in.

## Notes on accuracy
- No speech engine is 100% accurate on mixed-language, accented audio — plan
  to skim the export once. If you want an edit step before burning-in, say so
  and I can add a review/edit screen for the transcribed captions before export.
- Bigger models (`large-v3`) are meaningfully better with strong accents and
  background noise, at the cost of speed (and needing more RAM/VRAM).

## Files
- `main.py` — the GUI (Tkinter, so no extra runtime weight)
- `transcriber.py` — Whisper-based transcription with word timestamps
- `subtitle_builder.py` — groups words into short caption lines
- `exporter.py` — writes the styled `.ass` subtitle file and burns it via ffmpeg
- `fonts_util.py` — scans your installed fonts for the font dropdown
- `types_common.py` — shared data type
- `build_exe.bat` — packages the app into a Windows `.exe`
