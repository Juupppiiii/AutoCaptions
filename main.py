"""
TaglishCap — Taglish Auto-Subtitle App

3-panel layout
──────────────────────────────────────────────────────────────────────
│ TITLE BAR                                                          │
├──────────────────────┬──────────────────────┬──────────────────────┤
│ PREVIEW              │ CAPTION EDITOR       │ CONTROLS SIDEBAR     │
│ ─ video frame        │ ─ scrollable table   │ ─ ① import           │
│ ─ caption overlay    │   (text / in / out)  │ ─ ② transcribe       │
│ ─ timeline scrubber  │ ─ add / delete row   │ ─ ③ export           │
│                      │ ─ select → seek      │ ─ style controls     │
│                      │                      │ ─ activity log       │
├──────────────────────┴──────────────────────┴──────────────────────┤
│ STATUS BAR                          ░░░░░░░░░░░░  XX%              │
──────────────────────────────────────────────────────────────────────

Caption editor rows are live — edits immediately reflect in the preview.
Clicking a row seeks the preview frame to that caption's start time.
The timeline scrubber lets you scrub through the video independently.
"""
from __future__ import annotations

import os
import tempfile
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, colorchooser, messagebox
from typing import List, Optional
from dataclasses import dataclass, field

from PIL import Image, ImageTk, ImageDraw, ImageFont

import fonts_util
import transcriber
import exporter
from subtitle_builder import build_captions, Caption
from types_common import Word


# ── Palette ───────────────────────────────────────────────────────────────
C = {
    "bg":        "#12121f",
    "bg2":       "#1a1a2e",
    "bg3":       "#0f3460",
    "bg4":       "#0a0a18",   # editor table bg
    "accent":    "#e94560",
    "accent2":   "#533483",
    "fg":        "#e8e8f0",
    "fg_dim":    "#6a7a8a",
    "success":   "#4caf7d",
    "warn":      "#f0a500",
    "error":     "#ff4f6d",
    "canvas_bg": "#080810",
    "log_bg":    "#0c0c18",
    "log_fg":    "#7ec8e3",
    "sep":       "#252540",
    "btn":       "#e94560",
    "btn_fg":    "#ffffff",
    "btn_dis":   "#2a2a42",
    "btn_dis_fg":"#4a4a6a",
    "input_bg":  "#0e1828",
    "row_sel":   "#1e3a5a",   # selected editor row
    "row_alt":   "#141428",   # alternating editor row
    "tl_bg":     "#0a0a18",   # timeline bg
    "tl_head":   "#e94560",   # timeline playhead
    "pct_fg":    "#e94560",
}

APP_TITLE = "TaglishCap  ·  Taglish Auto-Subtitles"

SIZE_MIN, SIZE_MAX = 1, 32
def _px(v: int) -> int:
    return int(14 + (v - 1) * (160 - 14) / 31)


# ── Widget helpers ────────────────────────────────────────────────────────
def _lbl(p, text="", size=9, bold=False, fg=None, bg=None, **kw):
    return tk.Label(p, text=text,
                    font=("Segoe UI", size, "bold" if bold else "normal"),
                    fg=fg or C["fg"], bg=bg or C["bg2"], **kw)

def _btn(p, text, cmd, enabled=True, width=18, bg=None):
    b = tk.Button(p, text=text, command=cmd,
                  font=("Segoe UI", 9, "bold"),
                  bg=(bg or C["btn"]) if enabled else C["btn_dis"],
                  fg=C["btn_fg"] if enabled else C["btn_dis_fg"],
                  activebackground=C["accent2"], activeforeground="#fff",
                  relief="flat", bd=0, padx=10, pady=6,
                  cursor="hand2" if enabled else "",
                  width=width,
                  state="normal" if enabled else "disabled")
    return b

def _section(p, title):
    f = tk.Frame(p, bg=C["bg3"])
    tk.Label(f, text=title.upper(),
             font=("Segoe UI", 7, "bold"),
             fg=C["fg_dim"], bg=C["bg3"],
             padx=10, pady=3).pack(side="left")
    return f

def _sep(p):
    return tk.Frame(p, bg=C["sep"], height=1)

def _slider_row(p, var, lo, hi, cmd, value_width=3):
    row = tk.Frame(p, bg=C["bg2"])
    row.pack(fill="x", padx=10, pady=(0, 4))
    ttk.Scale(row, from_=lo, to=hi, variable=var,
              command=cmd).pack(side="left", fill="x", expand=True)
    tk.Label(row, textvariable=var,
             font=("Segoe UI", 9, "bold"),
             fg=C["accent"], bg=C["bg2"],
             width=value_width).pack(side="left", padx=(4, 0))


# ── Caption row (mutable, mirrors editor cells) ───────────────────────────
@dataclass
class CaptionRow:
    text_var:  tk.StringVar
    start_var: tk.StringVar
    end_var:   tk.StringVar
    frame:     tk.Frame        = field(repr=False)

    def to_caption(self) -> Optional[Caption]:
        try:
            return Caption(
                text=self.text_var.get().strip(),
                start=_parse_time(self.start_var.get()),
                end=_parse_time(self.end_var.get()),
            )
        except ValueError:
            return None


def _parse_time(s: str) -> float:
    """Parse 'H:MM:SS.cc' or plain float string → seconds."""
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    parts = s.split(":")
    if len(parts) == 3:
        h, m, sec = parts
        return int(h) * 3600 + int(m) * 60 + float(sec)
    raise ValueError(f"Cannot parse time: {s!r}")


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    h  = int(t // 3600)
    m  = int((t % 3600) // 60)
    s  = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs >= 100:
        cs, s = 0, s + 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ── App ───────────────────────────────────────────────────────────────────
class TaglishCapApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1440x820")
        self.root.minsize(1100, 680)
        self.root.configure(bg=C["bg"])

        self.video_path:  Optional[str]       = None
        self.words:       Optional[List[Word]] = None
        self.video_w      = 1080
        self.video_h      = 1920
        self.video_dur    = 0.0
        self.fonts_map: dict = fonts_util.list_system_fonts()

        # preview state
        self._preview_frame_path: Optional[str]            = None
        self._preview_photo:      Optional[ImageTk.PhotoImage] = None
        self._seek_time:          float = 0.0   # current preview position (s)

        # caption editor state
        self._caption_rows: List[CaptionRow] = []
        self._selected_row: Optional[int]    = None

        # style state
        self._color_hex = "#FFFFFF"

        self._busy = False

        self._apply_theme()
        self._build_ui()
        self.root.after(200, self.refresh_preview)

    # ── ttk theme ─────────────────────────────────────────────────────────
    def _apply_theme(self) -> None:
        s = ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".",
            background=C["bg2"], foreground=C["fg"],
            fieldbackground=C["input_bg"],
            troughcolor=C["bg3"],
            bordercolor=C["sep"], lightcolor=C["sep"], darkcolor=C["sep"])
        s.configure("TCombobox",
            fieldbackground=C["input_bg"], background=C["input_bg"],
            foreground=C["fg"], arrowcolor=C["accent"],
            selectbackground=C["accent2"], selectforeground=C["fg"])
        s.configure("TScale",
            background=C["bg2"], troughcolor=C["bg3"])
        s.configure("TCheckbutton",
            background=C["bg2"], foreground=C["fg"],
            focuscolor=C["accent"])
        s.map("TCheckbutton",
            background=[("active", C["bg2"])],
            foreground=[("active", C["accent"])])
        for nm in ("Spin.Horizontal.TProgressbar",
                   "Fill.Horizontal.TProgressbar"):
            s.configure(nm, troughcolor=C["bg4"],
                        background=C["accent"], borderwidth=0)

    # ── Top-level layout ──────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # ── Title bar ─────────────────────────────────────────────────────
        tbar = tk.Frame(self.root, bg=C["bg3"], height=46)
        tbar.pack(side="top", fill="x")
        tbar.pack_propagate(False)

        tk.Label(tbar, text="🎬  TaglishCap",
                 font=("Segoe UI", 13, "bold"),
                 fg=C["accent"], bg=C["bg3"], padx=14).pack(side="left", pady=8)
        tk.Label(tbar, text="Taglish Auto-Subtitle Generator",
                 font=("Segoe UI", 9), fg=C["fg_dim"], bg=C["bg3"]).pack(side="left")

        self._badge_var = tk.StringVar(value="")
        tk.Label(tbar, textvariable=self._badge_var,
                 font=("Segoe UI", 8, "bold"),
                 fg=C["bg2"], bg=C["success"],
                 padx=8, pady=2).pack(side="right", padx=14, pady=12)

        # ── Body (3 columns) ──────────────────────────────────────────────
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(side="top", fill="both", expand=True)
        body.columnconfigure(0, weight=5)   # preview
        body.columnconfigure(1, weight=4)   # editor
        body.columnconfigure(2, weight=0)   # sidebar (fixed)
        body.rowconfigure(0, weight=1)

        self._build_preview_panel(body)
        self._build_editor_panel(body)
        self._build_sidebar(body)
        self._build_statusbar(self.root)

    # ── Panel 1: Preview + timeline ───────────────────────────────────────
    def _build_preview_panel(self, parent) -> None:
        panel = tk.Frame(parent, bg=C["bg"], padx=8, pady=8)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)

        # top: import button + filename
        top = tk.Frame(panel, bg=C["bg"])
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._import_btn = _btn(top, "①  Import Video", self.import_video, width=16)
        self._import_btn.pack(side="left")
        self._file_lbl = tk.Label(top, text="No video loaded",
                                   font=("Segoe UI", 9, "italic"),
                                   fg=C["fg_dim"], bg=C["bg"], anchor="w")
        self._file_lbl.pack(side="left", padx=10)

        # canvas
        self._canvas = tk.Canvas(panel, bg=C["canvas_bg"],
                                  highlightthickness=1,
                                  highlightbackground=C["sep"])
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._canvas.bind("<Configure>", lambda _e: self.refresh_preview())

        # placeholder
        self._canvas.create_text(300, 250,
            text="Import a video to see preview",
            fill=C["fg_dim"], font=("Segoe UI", 11), tags="ph")

        # timeline scrubber
        tl_frame = tk.Frame(panel, bg=C["tl_bg"], height=36)
        tl_frame.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        tl_frame.columnconfigure(1, weight=1)

        # time label left
        self._tl_pos_lbl = tk.Label(tl_frame, text="0:00:00",
                                     font=("Segoe UI", 8),
                                     fg=C["fg_dim"], bg=C["tl_bg"], width=8)
        self._tl_pos_lbl.grid(row=0, column=0, padx=(6, 2))

        # scrubber slider
        self._tl_var = tk.DoubleVar(value=0.0)
        self._tl_slider = ttk.Scale(tl_frame, from_=0, to=100,
                                     variable=self._tl_var,
                                     command=self._on_timeline_scrub)
        self._tl_slider.grid(row=0, column=1, sticky="ew", padx=4, pady=6)

        # duration label right
        self._tl_dur_lbl = tk.Label(tl_frame, text="0:00:00",
                                     font=("Segoe UI", 8),
                                     fg=C["fg_dim"], bg=C["tl_bg"], width=8)
        self._tl_dur_lbl.grid(row=0, column=2, padx=(2, 6))

    def _on_timeline_scrub(self, _val=None) -> None:
        if not self.video_path or self.video_dur <= 0:
            return
        t = self._tl_var.get() / 100.0 * self.video_dur
        self._tl_pos_lbl.config(text=_fmt_time(t))
        self._seek_preview(t)

    # ── Panel 2: Caption editor ───────────────────────────────────────────
    def _build_editor_panel(self, parent) -> None:
        panel = tk.Frame(parent, bg=C["bg2"], padx=0, pady=0)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)

        # header bar
        hdr = tk.Frame(panel, bg=C["bg3"])
        hdr.grid(row=0, column=0, sticky="ew")

        tk.Label(hdr, text="CAPTION EDITOR",
                 font=("Segoe UI", 8, "bold"),
                 fg=C["fg_dim"], bg=C["bg3"],
                 padx=10, pady=4).pack(side="left")

        # toolbar buttons
        tbr = tk.Frame(hdr, bg=C["bg3"])
        tbr.pack(side="right", padx=6, pady=2)

        self._add_row_btn = tk.Button(tbr, text="＋ Add",
            command=self._add_caption_row,
            font=("Segoe UI", 8, "bold"),
            bg=C["bg3"], fg=C["success"],
            activebackground=C["success"], activeforeground=C["bg"],
            relief="flat", bd=0, padx=8, pady=2, cursor="hand2")
        self._add_row_btn.pack(side="left", padx=2)

        self._del_row_btn = tk.Button(tbr, text="✕ Delete",
            command=self._delete_selected_row,
            font=("Segoe UI", 8, "bold"),
            bg=C["bg3"], fg=C["error"],
            activebackground=C["error"], activeforeground=C["bg"],
            relief="flat", bd=0, padx=8, pady=2, cursor="hand2")
        self._del_row_btn.pack(side="left", padx=2)

        self._sort_btn = tk.Button(tbr, text="⇅ Sort",
            command=self._sort_captions,
            font=("Segoe UI", 8, "bold"),
            bg=C["bg3"], fg=C["fg_dim"],
            activebackground=C["accent2"], activeforeground="#fff",
            relief="flat", bd=0, padx=8, pady=2, cursor="hand2")
        self._sort_btn.pack(side="left", padx=2)

        # column labels
        col_hdr = tk.Frame(panel, bg=C["bg4"])
        col_hdr.grid(row=1, column=0, sticky="ew")
        for col, (label, w) in enumerate([
            ("#", 3), ("Caption Text", 22), ("In", 9), ("Out", 9)
        ]):
            tk.Label(col_hdr, text=label,
                     font=("Segoe UI", 8, "bold"),
                     fg=C["fg_dim"], bg=C["bg4"],
                     width=w, anchor="w", padx=4, pady=3,
                     ).grid(row=0, column=col, sticky="ew")
        col_hdr.columnconfigure(1, weight=1)

        # scrollable rows
        scroll_frame = tk.Frame(panel, bg=C["bg4"])
        scroll_frame.grid(row=2, column=0, sticky="nsew")
        panel.rowconfigure(2, weight=1)
        scroll_frame.rowconfigure(0, weight=1)
        scroll_frame.columnconfigure(0, weight=1)

        self._editor_canvas = tk.Canvas(scroll_frame,
                                         bg=C["bg4"],
                                         highlightthickness=0)
        vsb = ttk.Scrollbar(scroll_frame, orient="vertical",
                             command=self._editor_canvas.yview)
        self._editor_canvas.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        self._editor_canvas.grid(row=0, column=0, sticky="nsew")

        self._rows_frame = tk.Frame(self._editor_canvas, bg=C["bg4"])
        self._rows_frame.columnconfigure(1, weight=1)
        self._editor_win = self._editor_canvas.create_window(
            (0, 0), window=self._rows_frame, anchor="nw")

        self._editor_canvas.bind("<Configure>", self._on_editor_resize)
        self._rows_frame.bind("<Configure>", self._on_rows_resize)
        self._editor_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._editor_canvas.yview_scroll(
                int(-e.delta / 120), "units"))

        # bottom: caption count label
        self._caption_count_lbl = tk.Label(panel,
            text="No captions",
            font=("Segoe UI", 8), fg=C["fg_dim"], bg=C["bg3"],
            anchor="w", padx=10, pady=3)
        self._caption_count_lbl.grid(row=3, column=0, sticky="ew")

    def _on_editor_resize(self, e) -> None:
        self._editor_canvas.itemconfig(self._editor_win, width=e.width)

    def _on_rows_resize(self, _e) -> None:
        self._editor_canvas.configure(
            scrollregion=self._editor_canvas.bbox("all"))

    # ── Panel 3: Controls sidebar ─────────────────────────────────────────
    def _build_sidebar(self, parent) -> None:
        outer = tk.Frame(parent, bg=C["bg2"], width=300)
        outer.grid(row=0, column=2, sticky="nsew")
        outer.pack_propagate(False)

        scv = tk.Canvas(outer, bg=C["bg2"], highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=scv.yview)
        scv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        scv.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(scv, bg=C["bg2"])
        wid = scv.create_window((0, 0), window=inner, anchor="nw", width=290)
        scv.bind("<Configure>", lambda e: scv.itemconfig(wid, width=e.width))
        inner.bind("<Configure>",
                   lambda _e: scv.configure(scrollregion=scv.bbox("all")))

        self._build_sidebar_content(inner)

    def _build_sidebar_content(self, p) -> None:
        PAD = dict(padx=10, pady=3)

        # ── ② Transcribe ─────────────────────────────────────────────────
        _section(p, "②  Transcribe").pack(fill="x", pady=(10, 3))

        _lbl(p, "Language", 8, fg=C["fg_dim"]).pack(anchor="w", **PAD)
        self._lang_var = tk.StringVar(
            value=list(transcriber.LANGUAGE_MAP.keys())[0])
        ttk.Combobox(p, textvariable=self._lang_var,
                     values=list(transcriber.LANGUAGE_MAP.keys()),
                     state="readonly", font=("Segoe UI", 9),
                     ).pack(fill="x", **PAD)

        _lbl(p, "Model / accuracy", 8, fg=C["fg_dim"]).pack(
            anchor="w", padx=10, pady=(6, 2))
        mf = tk.Frame(p, bg=C["bg2"])
        mf.pack(fill="x", **PAD)
        self._model_var = tk.StringVar(value="medium")
        for m in transcriber.MODEL_SIZES:
            hint = transcriber.MODEL_HINTS.get(m, "")
            tk.Radiobutton(mf, text=f"{m}  —  {hint}",
                           variable=self._model_var, value=m,
                           font=("Segoe UI", 8),
                           fg=C["fg"], bg=C["bg2"],
                           selectcolor=C["bg3"],
                           activebackground=C["bg2"],
                           activeforeground=C["accent"],
                           anchor="w").pack(fill="x", pady=1)

        self._transcribe_btn = _btn(p, "②  Transcribe",
                                    self.start_transcribe, enabled=False)
        self._transcribe_btn.pack(fill="x", padx=10, pady=(6, 2))

        # transcription progress bar
        tpf = tk.Frame(p, bg=C["bg2"])
        tpf.pack(fill="x", padx=10, pady=(0, 4))
        self._t_pbar = ttk.Progressbar(tpf, mode="determinate", length=180,
                                        style="Fill.Horizontal.TProgressbar",
                                        maximum=100)
        self._t_pbar.pack(side="left", fill="x", expand=True)
        self._t_pct_lbl = tk.Label(tpf, text="",
                                    font=("Segoe UI", 9, "bold"),
                                    fg=C["pct_fg"], bg=C["bg2"], width=5)
        self._t_pct_lbl.pack(side="left", padx=(4, 0))

        _sep(p).pack(fill="x", pady=6)

        # ── ③ Export ─────────────────────────────────────────────────────
        _section(p, "③  Export").pack(fill="x", pady=(0, 3))

        _lbl(p, "Video quality", 8, fg=C["fg_dim"]).pack(anchor="w", **PAD)
        self._quality_var = tk.StringVar(value="crf18")
        quality_labels = list(exporter.QUALITY_LABELS.values())
        quality_keys   = list(exporter.QUALITY_LABELS.keys())
        self._quality_display_var = tk.StringVar(
            value=exporter.QUALITY_LABELS["crf18"])
        quality_cb = ttk.Combobox(
            p, textvariable=self._quality_display_var,
            values=quality_labels, state="readonly",
            font=("Segoe UI", 9))
        quality_cb.pack(fill="x", **PAD)

        def _on_quality_change(*_):
            disp = self._quality_display_var.get()
            for k, v in exporter.QUALITY_LABELS.items():
                if v == disp:
                    self._quality_var.set(k)
                    break
        self._quality_display_var.trace_add("write", _on_quality_change)

        self._export_btn = _btn(p, "③  Export Video",
                                self.start_export, enabled=False)
        self._export_btn.pack(fill="x", padx=10, pady=(6, 4))

        _sep(p).pack(fill="x", pady=6)

        # ── Caption Style ─────────────────────────────────────────────────
        _section(p, "Caption Style").pack(fill="x", pady=(0, 3))

        _lbl(p, "Words per line  (1–4)", 8, fg=C["fg_dim"]).pack(anchor="w", **PAD)
        self._max_words_var = tk.IntVar(value=3)
        _slider_row(p, self._max_words_var, 1, 4,
                    lambda *_: self.refresh_preview(), value_width=2)

        _lbl(p, "Font", 8, fg=C["fg_dim"]).pack(anchor="w", padx=10, pady=(6, 1))
        self._font_var = tk.StringVar(value=next(iter(self.fonts_map), "Arial"))
        ttk.Combobox(p, textvariable=self._font_var,
                     values=list(self.fonts_map.keys()),
                     state="readonly", font=("Segoe UI", 9),
                     ).pack(fill="x", **PAD)
        self._font_var.trace_add("write", lambda *_: self.refresh_preview())

        _lbl(p, "Font size  (1–32)", 8, fg=C["fg_dim"]).pack(
            anchor="w", padx=10, pady=(6, 1))
        self._size_var = tk.IntVar(value=16)
        _slider_row(p, self._size_var, SIZE_MIN, SIZE_MAX,
                    lambda *_: self.refresh_preview(), value_width=3)

        _lbl(p, "Font color", 8, fg=C["fg_dim"]).pack(anchor="w", padx=10, pady=(6, 1))
        crow = tk.Frame(p, bg=C["bg2"])
        crow.pack(fill="x", **PAD)
        self._color_swatch = tk.Label(crow, bg=self._color_hex,
                                       width=4, relief="flat",
                                       highlightthickness=1,
                                       highlightbackground=C["sep"])
        self._color_swatch.pack(side="left")
        tk.Button(crow, text="Choose…", command=self.pick_color,
                  font=("Segoe UI", 8),
                  bg=C["bg3"], fg=C["fg"],
                  activebackground=C["accent2"], activeforeground="#fff",
                  relief="flat", bd=0, padx=8, pady=3,
                  cursor="hand2").pack(side="left", padx=8)

        _sep(p).pack(fill="x", pady=6)

        self._outline_var = tk.BooleanVar(value=True)
        self._chk(p, "Black outline", self._outline_var)

        _lbl(p, "Outline weight  (1–32)", 8, fg=C["fg_dim"]).pack(anchor="w", **PAD)
        self._outline_w_var = tk.IntVar(value=4)
        _slider_row(p, self._outline_w_var, 1, 32,
                    lambda *_: self.refresh_preview(), value_width=3)

        _sep(p).pack(fill="x", pady=6)

        self._cap_var  = tk.BooleanVar(value=False)
        self._bold_var = tk.BooleanVar(value=True)
        self._chk(p, "CAPITALIZE subtitles", self._cap_var)
        self._chk(p, "Bold", self._bold_var)

        _sep(p).pack(fill="x", pady=6)

        _lbl(p, "Vertical position  (0 top → 100 bottom)", 8,
             fg=C["fg_dim"]).pack(anchor="w", **PAD)
        self._vpos_var = tk.IntVar(value=78)
        _slider_row(p, self._vpos_var, 0, 100,
                    lambda *_: self.refresh_preview(), value_width=4)

        _sep(p).pack(fill="x", pady=6)

        # Activity log
        _section(p, "Activity Log").pack(fill="x", pady=(0, 3))
        lf = tk.Frame(p, bg=C["log_bg"],
                      highlightthickness=1, highlightbackground=C["sep"])
        lf.pack(fill="x", padx=10, pady=(0, 10))
        self._log_text = tk.Text(lf, height=7, bg=C["log_bg"], fg=C["log_fg"],
                                  font=("Consolas", 8), relief="flat", bd=0,
                                  state="disabled", wrap="word", cursor="arrow")
        lsb = ttk.Scrollbar(lf, orient="vertical", command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self._log_text.pack(side="left", fill="both", expand=True, padx=3, pady=3)
        for tag, fg in [("info", C["log_fg"]), ("success", C["success"]),
                         ("warn", C["warn"]),   ("error", C["error"])]:
            self._log_text.tag_configure(tag, foreground=fg)

    def _chk(self, p, label, var):
        tk.Checkbutton(p, text=label, variable=var,
                       command=self.refresh_preview,
                       font=("Segoe UI", 9),
                       fg=C["fg"], bg=C["bg2"],
                       selectcolor=C["bg3"],
                       activebackground=C["bg2"],
                       activeforeground=C["accent"],
                       relief="flat", bd=0,
                       anchor="w").pack(fill="x", padx=10, pady=1)

    # ── Status bar ────────────────────────────────────────────────────────
    def _build_statusbar(self, parent) -> None:
        bar = tk.Frame(parent, bg=C["bg3"], height=44)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)

        self._status_var = tk.StringVar(value="Import a video to begin.")
        tk.Label(bar, textvariable=self._status_var,
                 font=("Segoe UI", 9), fg=C["fg"], bg=C["bg3"],
                 anchor="w", padx=14).pack(side="left", fill="y")

        self._e_pct_lbl = tk.Label(bar, text="",
                                    font=("Segoe UI", 9, "bold"),
                                    fg=C["pct_fg"], bg=C["bg3"], width=5)
        self._e_pct_lbl.pack(side="right", padx=(0, 14))

        self._e_pbar = ttk.Progressbar(bar, mode="indeterminate", length=160,
                                        style="Spin.Horizontal.TProgressbar",
                                        maximum=100)
        self._e_pbar.pack(side="right", pady=10)

    # ── Logging ───────────────────────────────────────────────────────────
    def _log(self, msg: str, tag: str = "info") -> None:
        def _w():
            self._log_text.configure(state="normal")
            self._log_text.insert("end", msg.rstrip() + "\n", tag)
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        self.root.after(0, _w)

    def _log_clear(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    # ── Caption editor: row management ───────────────────────────────────
    def _clear_editor(self) -> None:
        for row in self._caption_rows:
            row.frame.destroy()
        self._caption_rows.clear()
        self._selected_row = None
        self._update_row_numbers()

    def _add_caption_row(self,
                          text: str = "",
                          start: float = 0.0,
                          end: float = 0.0,
                          select: bool = False) -> CaptionRow:
        idx = len(self._caption_rows)
        bg  = C["row_alt"] if idx % 2 == 0 else C["bg4"]

        frame = tk.Frame(self._rows_frame, bg=bg)
        frame.grid(row=idx, column=0, sticky="ew")
        self._rows_frame.columnconfigure(0, weight=1)

        # Row number label
        num_lbl = tk.Label(frame, text=str(idx + 1),
                            font=("Segoe UI", 8),
                            fg=C["fg_dim"], bg=bg, width=3, anchor="e")
        num_lbl.grid(row=0, column=0, padx=(4, 2), pady=2)

        # Text entry
        text_var = tk.StringVar(value=text)
        text_entry = tk.Entry(frame, textvariable=text_var,
                               font=("Segoe UI", 9),
                               bg=bg, fg=C["fg"],
                               insertbackground=C["accent"],
                               relief="flat", bd=0)
        text_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=2)
        frame.columnconfigure(1, weight=1)

        # Start time
        start_var = tk.StringVar(value=_fmt_time(start))
        start_entry = tk.Entry(frame, textvariable=start_var,
                                font=("Consolas", 8), width=10,
                                bg=bg, fg=C["accent"],
                                insertbackground=C["fg"],
                                relief="flat", bd=0)
        start_entry.grid(row=0, column=2, padx=2, pady=2)

        # End time
        end_var = tk.StringVar(value=_fmt_time(end))
        end_entry = tk.Entry(frame, textvariable=end_var,
                              font=("Consolas", 8), width=10,
                              bg=bg, fg=C["accent"],
                              insertbackground=C["fg"],
                              relief="flat", bd=0)
        end_entry.grid(row=0, column=3, padx=(2, 6), pady=2)

        row = CaptionRow(text_var=text_var, start_var=start_var,
                          end_var=end_var, frame=frame)
        self._caption_rows.append(row)

        # Bind selection + preview seek on click
        for widget in (frame, num_lbl, text_entry, start_entry, end_entry):
            widget.bind("<Button-1>", lambda e, i=idx: self._select_row(i))

        # Live preview update on any edit
        text_var.trace_add("write", lambda *_: self.refresh_preview())
        start_var.trace_add("write", lambda *_: self.refresh_preview())

        if select:
            self.root.after(50, lambda: self._select_row(idx))

        self._update_caption_count()
        return row

    def _select_row(self, idx: int) -> None:
        # Deselect old
        if self._selected_row is not None:
            old = self._caption_rows[self._selected_row]
            bg = C["row_alt"] if self._selected_row % 2 == 0 else C["bg4"]
            self._recolor_row(old.frame, bg)

        self._selected_row = idx

        # Highlight new
        row = self._caption_rows[idx]
        self._recolor_row(row.frame, C["row_sel"])

        # Seek preview to caption start time
        try:
            t = _parse_time(row.start_var.get())
            self._seek_preview(t)
        except ValueError:
            pass

        # Scroll editor to make row visible
        self._editor_canvas.update_idletasks()
        bbox = self._editor_canvas.bbox(self._editor_win)
        if bbox:
            row_y = row.frame.winfo_y()
            row_h = row.frame.winfo_height()
            total = bbox[3]
            frac  = row_y / max(total, 1)
            self._editor_canvas.yview_moveto(frac)

        self.refresh_preview()

    @staticmethod
    def _recolor_row(frame: tk.Frame, bg: str) -> None:
        frame.configure(bg=bg)
        for w in frame.winfo_children():
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass

    def _delete_selected_row(self) -> None:
        if self._selected_row is None or not self._caption_rows:
            return
        idx = self._selected_row
        self._caption_rows[idx].frame.destroy()
        self._caption_rows.pop(idx)
        self._selected_row = None
        self._rebuild_row_grid()
        self._update_caption_count()
        self.refresh_preview()

    def _add_caption_row_action(self) -> None:
        """Add a blank row after the currently selected row (or at end)."""
        if self._selected_row is not None and self._caption_rows:
            # insert after selected — simplest: append then sort won't help,
            # so we rebuild from scratch with an inserted blank
            rows_data = [(r.text_var.get(),
                          _parse_time_safe(r.start_var.get()),
                          _parse_time_safe(r.end_var.get()))
                         for r in self._caption_rows]
            ins = self._selected_row + 1
            rows_data.insert(ins, ("", 0.0, 0.0))
            self._clear_editor()
            for i, (t, s, e) in enumerate(rows_data):
                self._add_caption_row(t, s, e, select=(i == ins))
        else:
            self._add_caption_row("", 0.0, 0.0, select=True)

    def _sort_captions(self) -> None:
        """Re-sort rows by start time."""
        data = []
        for r in self._caption_rows:
            try:
                s = _parse_time(r.start_var.get())
            except ValueError:
                s = 0.0
            data.append((s, r.text_var.get(),
                          r.start_var.get(), r.end_var.get()))
        data.sort(key=lambda x: x[0])
        self._clear_editor()
        for _, txt, start_s, end_s in data:
            try:
                s = _parse_time(start_s)
                e = _parse_time(end_s)
            except ValueError:
                s, e = 0.0, 0.0
            self._add_caption_row(txt, s, e)
        self.refresh_preview()

    def _rebuild_row_grid(self) -> None:
        """Re-grid all rows after deletion to fix numbering + alternating bg."""
        for i, row in enumerate(self._caption_rows):
            bg = C["row_alt"] if i % 2 == 0 else C["bg4"]
            self._recolor_row(row.frame, bg)
            row.frame.grid(row=i, column=0, sticky="ew")
            # update number label
            for w in row.frame.winfo_children():
                if isinstance(w, tk.Label):
                    try:
                        w.configure(text=str(i + 1))
                    except Exception:
                        pass
                    break

    def _update_row_numbers(self) -> None:
        self._rebuild_row_grid()

    def _update_caption_count(self) -> None:
        n = len(self._caption_rows)
        self._caption_count_lbl.config(
            text=f"{n} caption{'s' if n != 1 else ''}")

    def _get_edited_captions(self) -> List[Caption]:
        """Collect captions from the editor rows, skipping invalid rows."""
        caps = []
        for row in self._caption_rows:
            cap = row.to_caption()
            if cap and cap.text:
                caps.append(cap)
        return caps

    # ── Import ────────────────────────────────────────────────────────────
    def import_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a video",
            filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),
                       ("All files", "*.*")])
        if not path:
            return
        self.video_path = path
        self.words = None
        self._clear_editor()
        self._file_lbl.config(text=os.path.basename(path))
        self._badge_var.set("")

        try:
            self.video_w, self.video_h = exporter.get_video_dimensions(path)
        except Exception as e:
            self._log(f"Dimensions unavailable: {e}", "warn")
            self.video_w, self.video_h = 1080, 1920

        self.video_dur = exporter.get_video_duration(path)
        self._tl_dur_lbl.config(text=_fmt_time(self.video_dur))
        self._tl_slider.config(to=max(1, self.video_dur))
        self._tl_var.set(0)

        self._set_btn(self._transcribe_btn, True)
        self._set_btn(self._export_btn, False)
        self._set_status("Video loaded — click Transcribe.")
        self._log_clear()
        self._log(f"Loaded: {os.path.basename(path)}", "success")
        self._log(f"Size: {self.video_w}×{self.video_h},  "
                  f"Duration: {_fmt_time(self.video_dur)}")
        self._load_preview_frame(0.0)

    def _load_preview_frame(self, t: float) -> None:
        if not self.video_path:
            return
        tmp = os.path.join(tempfile.gettempdir(), "taglishcap_preview.png")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(t), "-i", self.video_path,
                 "-frames:v", "1", tmp],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=True)
            self._preview_frame_path = tmp
        except Exception as e:
            self._log(f"Preview frame unavailable: {e}", "warn")
            self._preview_frame_path = None
        self._seek_time = t
        self.refresh_preview()

    def _seek_preview(self, t: float) -> None:
        """Seek the preview to time t (seconds). Throttled via after()."""
        self._seek_time = t
        if hasattr(self, "_seek_pending") and self._seek_pending:
            return
        self._seek_pending = True
        self.root.after(120, self._do_seek)

    def _do_seek(self) -> None:
        self._seek_pending = False
        self._load_preview_frame(self._seek_time)

    def pick_color(self) -> None:
        _, h = colorchooser.askcolor(color=self._color_hex,
                                      title="Choose font color")
        if h:
            self._color_hex = h
            self._color_swatch.config(bg=h)
            self.refresh_preview()

    # ── Transcribe ────────────────────────────────────────────────────────
    def start_transcribe(self) -> None:
        if not self.video_path or self._busy:
            return
        self._busy = True
        self._set_btn(self._transcribe_btn, False)
        self._set_btn(self._export_btn, False)
        self._badge_var.set("")
        self._log_clear()
        self._log("Starting transcription…")
        self._t_pbar.config(value=0)
        self._t_pct_lbl.config(text="0%")
        self._e_pbar.config(mode="indeterminate")
        self._e_pbar.start(12)
        self._set_status("Transcribing…")
        threading.Thread(target=self._transcribe_worker, daemon=True).start()

    def _transcribe_worker(self) -> None:
        def cb(msg: str) -> None:
            if msg.startswith("PROGRESS:"):
                try:
                    pct = int(msg.split(":")[1])
                    self.root.after(0, self._set_t_pct, pct)
                except ValueError:
                    pass
                return
            tag = "warn" if msg.lower().startswith("warn") else "info"
            self._log(msg, tag)
            if len(msg) < 90:
                self.root.after(0, self._set_status, msg)

        try:
            self.words = transcriber.transcribe(
                self.video_path,
                model_size=self._model_var.get(),
                language_label=self._lang_var.get(),
                progress_cb=cb,
            )
            self.root.after(0, self._transcribe_done)
        except Exception as e:
            self.root.after(0, self._on_error, f"Transcription failed:\n{e}")

    def _set_t_pct(self, pct: int) -> None:
        self._t_pbar.config(value=pct)
        self._t_pct_lbl.config(text=f"{pct}%")

    def _transcribe_done(self) -> None:
        self._busy = False
        self._e_pbar.stop()
        self._e_pbar.config(mode="determinate", value=0)
        self._t_pbar.config(value=100)
        self._t_pct_lbl.config(text="100%")

        n = len(self.words or [])
        self._set_btn(self._transcribe_btn, True)
        self._set_btn(self._export_btn, True)
        self._set_status(f"Transcribed {n} words — review captions then Export.")
        self._badge_var.set(f"  {n} words  ")
        self._log(f"✓ Done — {n} words.", "success")

        # Populate caption editor from transcription
        self._build_editor_from_words()
        self.refresh_preview()

    def _build_editor_from_words(self) -> None:
        if not self.words:
            return
        captions = build_captions(
            self.words,
            max_words=self._max_words_var.get(),
            capitalize=self._cap_var.get(),
        )
        self._clear_editor()
        for cap in captions:
            self._add_caption_row(cap.text, cap.start, cap.end)
        self._update_caption_count()

    # ── Export ────────────────────────────────────────────────────────────
    def start_export(self) -> None:
        if not self.video_path or self._busy:
            return
        captions = self._get_edited_captions()
        if not captions:
            messagebox.showwarning("No captions",
                "The caption editor is empty. Transcribe first or add rows.")
            return
        out = filedialog.asksaveasfilename(
            title="Save exported video as…",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4")])
        if not out:
            return
        self._busy = True
        self._set_btn(self._export_btn, False)
        self._set_btn(self._transcribe_btn, False)
        self._log_clear()
        self._log(f"Exporting {len(captions)} captions…")
        self._e_pbar.config(mode="indeterminate")
        self._e_pbar.start(12)
        threading.Thread(target=self._export_worker,
                         args=(out, captions), daemon=True).start()

    def _export_worker(self, out_path: str, captions: List[Caption]) -> None:
        def cb(msg: str) -> None:
            if msg.startswith("PROGRESS:"):
                try:
                    pct = int(msg.split(":")[1])
                    self.root.after(0, self._set_e_pct, pct)
                except ValueError:
                    pass
                return
            tag = "warn" if "warn" in msg.lower() else "info"
            self._log(msg, tag)
            if len(msg) < 90:
                self.root.after(0, self._set_status, msg)

        try:
            ass_path = os.path.join(tempfile.gettempdir(), "taglishcap_subs.ass")
            font_name = self._font_var.get()

            exporter.build_ass_file(
                captions, ass_path,
                video_width=self.video_w,
                video_height=self.video_h,
                font_name=font_name,
                font_size=_px(self._size_var.get()),
                font_color_hex=self._color_hex,
                outline_enabled=self._outline_var.get(),
                outline_weight=self._outline_w_var.get(),
                bold=self._bold_var.get(),
                vertical_position_pct=self._vpos_var.get(),
            )
            fonts_dir = os.path.dirname(
                self.fonts_map.get(font_name, "")) or None

            exporter.burn_subtitles(
                self.video_path, ass_path, out_path,
                quality_mode=self._quality_var.get(),
                fonts_dir=fonts_dir,
                progress_cb=cb,
            )
            self.root.after(0, self._export_done, out_path)
        except Exception as e:
            self.root.after(0, self._on_error, f"Export failed:\n{e}")

    def _set_e_pct(self, pct: int) -> None:
        self._e_pbar.stop()
        self._e_pbar.config(mode="determinate", value=pct)
        self._e_pct_lbl.config(text=f"{pct}%")

    def _export_done(self, out_path: str) -> None:
        self._busy = False
        self._e_pbar.config(value=100)
        self._e_pct_lbl.config(text="100%")
        self._set_btn(self._export_btn, True)
        self._set_btn(self._transcribe_btn, True)
        self._set_status(f"Done! → {os.path.basename(out_path)}")
        self._log(f"✓ Exported: {out_path}", "success")
        messagebox.showinfo("Export complete", f"Saved to:\n{out_path}")

    def _on_error(self, msg: str) -> None:
        self._busy = False
        self._e_pbar.stop()
        self._e_pbar.config(mode="determinate", value=0)
        self._e_pct_lbl.config(text="")
        self._set_btn(self._transcribe_btn, bool(self.video_path))
        self._set_btn(self._export_btn, bool(self._caption_rows))
        self._set_status("Error — see log.")
        self._log(msg, "error")
        messagebox.showerror("TaglishCap", msg)

    # ── Helpers ───────────────────────────────────────────────────────────
    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)

    @staticmethod
    def _set_btn(btn: tk.Button, enabled: bool) -> None:
        btn.config(
            state="normal" if enabled else "disabled",
            bg=C["btn"] if enabled else C["btn_dis"],
            fg=C["btn_fg"] if enabled else C["btn_dis_fg"],
            cursor="hand2" if enabled else "",
        )

    # Wire the "+" button properly after sidebar is built
    def _wire_add_btn(self) -> None:
        self._add_row_btn.config(command=self._add_caption_row_action)

    # ── Preview rendering ─────────────────────────────────────────────────
    def refresh_preview(self) -> None:
        canvas = self._canvas
        cw = canvas.winfo_width()  or 480
        ch = canvas.winfo_height() or 420
        canvas.delete("all")

        # Base image
        if self._preview_frame_path and os.path.exists(self._preview_frame_path):
            base = Image.open(self._preview_frame_path).convert("RGBA")
        else:
            base = Image.new("RGBA", (self.video_w, self.video_h), (14, 14, 24, 255))

        scale = min(cw / base.width, ch / base.height)
        dw = max(1, int(base.width  * scale))
        dh = max(1, int(base.height * scale))
        img = base.resize((dw, dh), Image.LANCZOS)

        # Determine caption to show:
        # 1. If a row is selected, show that row's text
        # 2. Otherwise show the caption whose time range covers _seek_time
        sample = "Sample Caption"
        if self._selected_row is not None and self._caption_rows:
            row = self._caption_rows[self._selected_row]
            sample = row.text_var.get() or "Sample Caption"
        elif self._caption_rows:
            t = self._seek_time
            for row in self._caption_rows:
                try:
                    s = _parse_time(row.start_var.get())
                    e = _parse_time(row.end_var.get())
                    if s <= t <= e:
                        sample = row.text_var.get() or ""
                        break
                except ValueError:
                    pass

        if self._cap_var.get():
            sample = sample.upper()

        # Font
        font_path = self.fonts_map.get(self._font_var.get())
        font_size_disp = max(8, int(_px(self._size_var.get()) * scale))
        try:
            pil_font = (ImageFont.truetype(font_path, font_size_disp)
                        if font_path and os.path.exists(font_path)
                        else ImageFont.load_default())
        except Exception:
            pil_font = ImageFont.load_default()

        # Measure + position
        td = ImageDraw.Draw(img)
        bbox = td.textbbox((0, 0), sample, font=pil_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (dw - tw) / 2
        y = dh * (self._vpos_var.get() / 100.0) - th

        # Overlay (text only, no background bar)
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)

        if self._outline_var.get():
            raw_ow = self._outline_w_var.get()
            ow = max(1, int(raw_ow / 32 * 12 * scale))
            for dx in range(-ow, ow + 1):
                for dy in range(-ow, ow + 1):
                    if dx or dy:
                        od.text((x + dx, y + dy), sample,
                                font=pil_font, fill=(0, 0, 0, 255))

        r, g, b = _hex_to_rgb(self._color_hex)
        od.text((x, y), sample, font=pil_font, fill=(r, g, b, 255))

        result = Image.alpha_composite(img, ov).convert("RGB")

        # Time indicator top-left
        try:
            ind_font = (ImageFont.truetype(font_path, max(8, int(10 * scale)))
                        if font_path and os.path.exists(font_path)
                        else ImageFont.load_default())
        except Exception:
            ind_font = ImageFont.load_default()
        ImageDraw.Draw(result).text(
            (8, 6), _fmt_time(self._seek_time),
            font=ind_font, fill=(200, 200, 200))

        # Update timeline slider position
        if self.video_dur > 0:
            pct = self._seek_time / self.video_dur * 100
            self._tl_var.set(pct)
            self._tl_pos_lbl.config(text=_fmt_time(self._seek_time))

        self._preview_photo = ImageTk.PhotoImage(result)
        canvas.create_image(cw // 2, ch // 2, image=self._preview_photo)

    # ── Entry init fix ────────────────────────────────────────────────────
    def _post_init(self) -> None:
        """Wire up anything that needs widgets to already exist."""
        self._add_row_btn.config(command=self._add_caption_row_action)


# ── Utility ───────────────────────────────────────────────────────────────
def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def _parse_time_safe(s: str) -> float:
    try:
        return _parse_time(s)
    except ValueError:
        return 0.0


# ── Entry point ───────────────────────────────────────────────────────────
def main() -> None:
    root = tk.Tk()
    root.configure(bg=C["bg"])
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = TaglishCapApp(root)
    app._post_init()
    root.mainloop()


if __name__ == "__main__":
    main()
