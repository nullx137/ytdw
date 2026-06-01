import sys
import os
import re
import json
import threading
import subprocess
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import timedelta

try:
    import yt_dlp
except ImportError:
    print("yt-dlp is required. Install: pip install yt-dlp", file=sys.stderr)
    sys.exit(1)

if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".ytdownloader.json")
ICON_FILE = "icon.ico"

THEMES = {
    "light": {
        "bg": "#f5f5f7", "fg": "#1d1d1f", "muted": "#86868b",
        "card": "#ffffff", "border": "#d2d2d7",
        "accent": "#0071e3", "hover": "#0051a8",
        "accent_fg": "#ffffff", "success": "#28a745", "error": "#dc3545",
    },
    "dark": {
        "bg": "#1e1e1e", "fg": "#f5f5f7", "muted": "#86868b",
        "card": "#2d2d2d", "border": "#3a3a3a",
        "accent": "#0a84ff", "hover": "#0066cc",
        "accent_fg": "#ffffff", "success": "#30d158", "error": "#ff453a",
    },
}

YOUTUBE_URL_RE = re.compile(r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$", re.I)


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def fmt_duration(secs):
    if not secs:
        return "\u2014"
    try:
        return str(timedelta(seconds=int(secs)))
    except (ValueError, OverflowError):
        return "\u2014"


def fmt_size(size):
    if not size:
        return "\u2014"
    f = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024:
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def resource_path(rel):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel)
    return rel


def check_ffmpeg():
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
        return True
    except Exception:
        return False


FFMPEG_AVAILABLE = check_ffmpeg()


class FormatTable(ttk.Frame):
    COLUMNS = [
        ("quality", "Quality", 90, tk.W),
        ("ext", "Format", 70, tk.CENTER),
        ("size", "Size", 90, tk.W),
        ("note", "Note", 140, tk.W),
    ]

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._data = []

        cols = [c[0] for c in self.COLUMNS]
        self.tree = ttk.Treeview(
            self, columns=cols, show="headings",
            selectmode="browse", height=8,
        )
        for cid, label, w, anchor in self.COLUMNS:
            self.tree.heading(cid, text=label)
            self.tree.column(cid, width=w, minwidth=50, anchor=anchor)
        self.tree.heading("quality", anchor=tk.W)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        scroll.grid(row=0, column=1, sticky=tk.NS)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

    def set_formats(self, formats):
        self._data = formats
        self.tree.delete(*self.tree.get_children())
        for i, entry in enumerate(formats):
            tag = "alt" if i % 2 else ""
            self.tree.insert("", tk.END, iid=entry["id"], tags=(tag,) if tag else (),
                             values=(
                                 entry.get("label", entry["id"]),
                                 entry.get("ext", ""),
                                 fmt_size(entry.get("size")),
                                 entry.get("note", ""),
                             ))
        if formats:
            self.tree.selection_set(formats[0]["id"])
            self.tree.focus(formats[0]["id"])
            self.tree.see(formats[0]["id"])

    def get_selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        for f in self._data:
            if f["id"] == sel[0]:
                return f
        return None

    def enable(self):
        self.tree.state(["!disabled"])

    def disable(self):
        self.tree.state(["disabled"])

    def set_selectable(self, enabled):
        self.tree.configure(selectmode="browse" if enabled else "none")

    def _on_select(self, _e=None):
        f = self.get_selected()
        if not f:
            return
        parts = [f"{f.get('label', f['id'])} \u00b7 {f.get('ext', '').upper()}"]
        if f.get("size"):
            parts.append(fmt_size(f["size"]))
        if f.get("note"):
            parts.append(f["note"])
        return " | ".join(parts)


class YTDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.theme_name = "light" if "dark" not in THEMES else self.config.get("theme", "dark")
        self.theme = THEMES.get(self.theme_name, THEMES["dark"])

        self.root.title("YT Downloader")
        self.root.geometry("920x720")
        self.root.minsize(840, 640)

        if os.path.isfile(resource_path(ICON_FILE)):
            try:
                self.root.iconbitmap(resource_path(ICON_FILE))
            except Exception:
                pass

        self.dl_path = tk.StringVar(value=self.config.get("dl_path", self._default_dl_dir()))
        self.video_data = None
        self.formats = []
        self.is_downloading = False
        self.cancel_requested = False
        self.ydl = None

        self._style_setup()
        self._build_ui()
        self._bind_events()

        if not FFMPEG_AVAILABLE:
            self.log("WARNING: ffmpeg not found. MP3 conversion will fail.")

    def _default_dl_dir(self):
        if os.name == "nt":
            return os.path.join(os.environ.get("USERPROFILE", "~"), "Downloads")
        return os.path.join(os.path.expanduser("~"), "Downloads")

    def _style_setup(self):
        t = self.theme
        self.root.configure(bg=t["bg"])

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=t["bg"], foreground=t["fg"],
                        fieldbackground=t["card"])

        style.configure("TFrame", background=t["bg"])
        style.configure("Card.TFrame", background=t["card"])
        style.configure("TLabel", background=t["bg"], foreground=t["fg"])
        style.configure("Card.TLabel", background=t["card"], foreground=t["fg"])
        style.configure("Muted.TLabel", background=t["bg"], foreground=t["muted"])
        style.configure("Card.Muted.TLabel", background=t["card"], foreground=t["muted"])
        style.configure("Bold.TLabel", background=t["bg"], foreground=t["fg"],
                        font=("", 10, "bold"))
        style.configure("Title.TLabel", background=t["card"], foreground=t["fg"],
                        font=("", 13, "bold"))
        style.configure("Header.TLabel", background=t["bg"], foreground=t["fg"],
                        font=("", 15, "bold"))

        style.configure("TButton", padding=(12, 7), relief="flat")
        style.map("TButton",
                  background=[("active", self._lighter(t["card"])),
                              ("pressed", t["muted"])])

        style.configure("Accent.TButton", padding=(16, 8), relief="flat",
                        background=t["accent"], foreground=t["accent_fg"],
                        font=("", 11, "bold"))
        style.map("Accent.TButton",
                  background=[("active", t["hover"]), ("disabled", t["muted"])],
                  foreground=[("disabled", t["accent_fg"])])

        style.configure("TEntry", padding=8, relief="flat",
                        fieldbackground=t["card"])
        style.configure("TCombobox", fieldbackground=t["card"], arrowcolor=t["fg"])

        style.configure("TCheckbutton", background=t["card"], foreground=t["fg"],
                        focuscolor=t["accent"])

        style.configure("TLabelframe", background=t["bg"], relief="flat", borderwidth=1)
        style.configure("TLabelframe.Label", background=t["bg"], foreground=t["fg"],
                        font=("", 10, "bold"))

        style.configure("Treeview",
                        background=t["card"], foreground=t["fg"],
                        fieldbackground=t["card"], borderwidth=0, rowheight=28)
        style.configure("Treeview.Heading",
                        background=t["bg"], foreground=t["fg"],
                        relief="flat", font=("", 9, "bold"))
        style.map("Treeview",
                  background=[("selected", t["accent"])],
                  foreground=[("selected", t["accent_fg"])])
        self._tree_alt = None
        self._update_tree_alt_color(style, t)

        style.configure("Horizontal.TProgressbar",
                        background=t["accent"], troughcolor=t["border"],
                        borderwidth=0, thickness=6)
        style.configure("Vertical.TScrollbar",
                        background=t["card"], troughcolor=t["bg"],
                        bordercolor=t["border"], arrowcolor=t["fg"])

    def _update_tree_alt_color(self, style, t):
        alt_bg = self._lighter(t["card"])
        style.configure("alt.Treeview", background=alt_bg)
        self._tree_alt = alt_bg

    def _lighter(self, color):
        c = color.lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        r = min(255, r + 10)
        g = min(255, g + 10)
        b = min(255, b + 10)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)

        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=20, pady=(18, 8))
        ttk.Label(header, text="YT Downloader", style="Header.TLabel").pack(side=tk.LEFT)
        self.theme_btn = ttk.Button(header, width=10,
                                     command=self._toggle_theme)
        self.theme_btn.pack(side=tk.RIGHT)

        url_card = ttk.Frame(self.root, style="Card.TFrame")
        url_card.pack(fill=tk.X, padx=20, pady=(0, 10))

        url_row = ttk.Frame(url_card, style="Card.TFrame")
        url_row.pack(fill=tk.X, padx=16, pady=14)
        url_row.columnconfigure(1, weight=1)

        ttk.Label(url_row, text="YouTube URL",
                  style="Card.Muted.TLabel").grid(row=0, column=0, columnspan=4,
                                                  sticky=tk.W, pady=(0, 6))

        self.url_entry = ttk.Entry(url_row, font=("", 11))
        self.url_entry.grid(row=1, column=0, columnspan=2, sticky=tk.EW,
                            padx=(0, 8), ipady=6)

        self.paste_btn = ttk.Button(url_row, text="Paste", width=8,
                                    command=self._paste_url)
        self.paste_btn.grid(row=1, column=2, padx=(0, 6))

        self.fetch_btn = ttk.Button(url_row, text="Get Info",
                                    style="Accent.TButton", width=12,
                                    command=self._fetch_info)
        self.fetch_btn.grid(row=1, column=3)

        content = ttk.Frame(self.root)
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        info_card = ttk.Frame(content, style="Card.TFrame")
        info_card.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 5))
        self._build_info_panel(info_card)

        fmt_card = ttk.Frame(content, style="Card.TFrame")
        fmt_card.grid(row=0, column=1, sticky=tk.NSEW, padx=(5, 0))
        self._build_format_panel(fmt_card)

        path_card = ttk.Frame(self.root, style="Card.TFrame")
        path_card.pack(fill=tk.X, padx=20, pady=(0, 10))
        self._build_path_row(path_card)

        self._build_download_row()

        log_card = ttk.Frame(self.root, style="Card.TFrame")
        log_card.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 18))
        self._build_log_panel(log_card)

        self._update_theme_btn()

    def _build_info_panel(self, parent):
        w = ttk.Frame(parent, style="Card.TFrame")
        w.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        ttk.Label(w, text="Video Info", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 12))

        self.title_var = tk.StringVar(value="\u2014")
        self.channel_var = tk.StringVar(value="\u2014")
        self.duration_var = tk.StringVar(value="\u2014")
        self.date_var = tk.StringVar(value="\u2014")
        self.views_var = tk.StringVar(value="\u2014")

        for lbl, var in [("Title", self.title_var), ("Channel", self.channel_var),
                          ("Duration", self.duration_var), ("Uploaded", self.date_var),
                          ("Views", self.views_var)]:
            row = ttk.Frame(w, style="Card.TFrame")
            row.pack(fill=tk.X, pady=3)
            ttk.Label(row, text=lbl, style="Card.Muted.TLabel",
                      width=12, anchor=tk.W).pack(side=tk.LEFT)
            ttk.Label(row, textvariable=var, style="Card.TLabel",
                      wraplength=300).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.info_var = tk.StringVar()
        ttk.Label(w, textvariable=self.info_var, style="Card.Muted.TLabel",
                  font=("", 9)).pack(anchor=tk.W, pady=(8, 0))

        self.audio_var = tk.BooleanVar()
        self.mp3_bitrate_var = tk.StringVar(value="192")
        ttk.Checkbutton(w, text="Download MP3 (audio only)",
                        variable=self.audio_var, style="TCheckbutton",
                        command=self._on_audio_toggle).pack(anchor=tk.W, pady=(16, 0))

        br_row = ttk.Frame(w, style="Card.TFrame")
        br_row.pack(anchor=tk.W, fill=tk.X, pady=(6, 0))
        ttk.Label(br_row, text="Bitrate:", style="Card.Muted.TLabel").pack(side=tk.LEFT, padx=(24, 6))
        self.bitrate_combo = ttk.Combobox(br_row, textvariable=self.mp3_bitrate_var,
                                          width=10, state="readonly",
                                          values=("128", "192", "256", "320", "0 (best)"))
        self.bitrate_combo.pack(side=tk.LEFT)

        self.sub_var = tk.BooleanVar()
        self.sub_lang_var = tk.StringVar(value="en,ru")
        ttk.Checkbutton(w, text="Download subtitles",
                        variable=self.sub_var, style="TCheckbutton").pack(anchor=tk.W, pady=(10, 0))
        sub_row = ttk.Frame(w, style="Card.TFrame")
        sub_row.pack(anchor=tk.W, fill=tk.X, pady=(6, 0))
        ttk.Label(sub_row, text="Langs:", style="Card.Muted.TLabel").pack(side=tk.LEFT, padx=(24, 6))
        ttk.Entry(sub_row, textvariable=self.sub_lang_var, width=18).pack(side=tk.LEFT)
        ttk.Label(sub_row, text="(en,ru,...)", style="Card.Muted.TLabel",
                  font=("", 8)).pack(side=tk.LEFT, padx=(4, 0))

    def _build_format_panel(self, parent):
        w = ttk.Frame(parent, style="Card.TFrame")
        w.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        ttk.Label(w, text="Formats", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 12))

        self.fmt_table = FormatTable(w)
        self.fmt_table.pack(fill=tk.BOTH, expand=True)

    def _build_path_row(self, parent):
        w = ttk.Frame(parent, style="Card.TFrame")
        w.pack(fill=tk.X, padx=16, pady=14)
        w.columnconfigure(1, weight=1)

        ttk.Label(w, text="Save folder",
                  style="Card.Muted.TLabel").grid(row=0, column=0, columnspan=4,
                                                  sticky=tk.W, pady=(0, 6))

        self.path_entry = ttk.Entry(w, textvariable=self.dl_path, font=("", 10))
        self.path_entry.grid(row=1, column=0, columnspan=2, sticky=tk.EW,
                             padx=(0, 8), ipady=4)

        self.browse_btn = ttk.Button(w, text="Browse", width=10,
                                     command=self._browse_path)
        self.browse_btn.grid(row=1, column=2, padx=(0, 6))

        self.open_btn = ttk.Button(w, text="Open", width=10,
                                   command=self._open_folder)
        self.open_btn.grid(row=1, column=3)

    def _build_download_row(self):
        ctrl = ttk.Frame(self.root)
        ctrl.pack(fill=tk.X, padx=20, pady=(0, 10))

        ctrl.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(ctrl, mode="determinate")
        self.progress.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=(0, 8))

        self.status_var = tk.StringVar(value="Ready")
        self.status_lbl = ttk.Label(ctrl, textvariable=self.status_var,
                                    style="Muted.TLabel")
        self.status_lbl.grid(row=1, column=0, sticky=tk.W)

        btn_frame = ttk.Frame(ctrl)
        btn_frame.grid(row=1, column=2, sticky=tk.E)

        self.cancel_btn = ttk.Button(btn_frame, text="Cancel", width=10,
                                     command=self._cancel_download)
        self.cancel_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.cancel_btn.configure(state=tk.DISABLED)

        self.dl_btn = ttk.Button(btn_frame, text="Download",
                                 style="Accent.TButton", width=14,
                                 command=self._start_download)
        self.dl_btn.pack(side=tk.LEFT)

    def _build_log_panel(self, parent):
        w = ttk.Frame(parent, style="Card.TFrame")
        w.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)
        w.columnconfigure(0, weight=1)
        w.rowconfigure(1, weight=1)

        hdr = ttk.Frame(w, style="Card.TFrame")
        hdr.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        ttk.Label(hdr, text="Log", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Button(hdr, text="Clear", width=8,
                   command=self._clear_log).pack(side=tk.RIGHT)

        txt_frame = ttk.Frame(w, style="Card.TFrame")
        txt_frame.grid(row=1, column=0, sticky=tk.NSEW)
        txt_frame.columnconfigure(0, weight=1)
        txt_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(txt_frame, height=5, wrap=tk.WORD,
                                font=("Consolas" if os.name == "nt" else "Monospace", 9),
                                relief="flat", borderwidth=0,
                                bg=self.theme["card"], fg=self.theme["fg"],
                                insertbackground=self.theme["fg"])
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)

        scroll = ttk.Scrollbar(txt_frame, orient=tk.VERTICAL,
                               command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky=tk.NS)
        self.log_text.configure(yscrollcommand=scroll.set)

        self.log_text.bind("<Button-3>", self._log_context_menu)

    def _bind_events(self):
        self.url_entry.bind("<Return>", lambda _: self._fetch_info())
        self.tree = self.fmt_table.tree
        self.tree.bind("<Double-1>", lambda _: self._start_download())

    def _update_theme_btn(self):
        self.theme_btn.configure(text="Dark" if self.theme_name == "light" else "Light")

    def _reload_theme(self):
        for w in list(self.root.winfo_children()):
            w.destroy()
        self._style_setup()
        self._build_ui()
        self._bind_events()

    def _toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.theme = THEMES[self.theme_name]
        self.config["theme"] = self.theme_name
        save_config(self.config)
        self._reload_theme()

    def _paste_url(self):
        try:
            text = self.root.clipboard_get()
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, text.strip())
        except tk.TclError:
            pass

    def _open_folder(self):
        path = self.dl_path.get().strip()
        if not path:
            return
        os.makedirs(path, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def _log_context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Copy", command=self._log_copy)
        menu.add_command(label="Select All", command=self._log_select_all)
        menu.add_separator()
        menu.add_command(label="Clear", command=self._clear_log)
        menu.tk_popup(event.x_root, event.y_root)

    def _log_copy(self):
        try:
            sel = self.log_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            if sel:
                self.root.clipboard_clear()
                self.root.clipboard_append(sel)
        except tk.TclError:
            pass

    def _log_select_all(self):
        self.log_text.tag_add(tk.SEL, "1.0", tk.END)
        self.log_text.mark_set(tk.INSERT, tk.END)
        self.log_text.see(tk.INSERT)

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def _set_busy(self, busy):
        if busy:
            self.fetch_btn.state(["disabled"])
            self.dl_btn.state(["disabled"])
            self.paste_btn.state(["disabled"])
            self.browse_btn.state(["disabled"])
            self.url_entry.state(["disabled"])
            self.fmt_table.disable()
            if self.is_downloading:
                self.cancel_btn.state(["!disabled"])
            else:
                self.cancel_btn.state(["disabled"])
            self.progress.configure(mode="indeterminate")
            self.progress.start(10)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.fetch_btn.state(["!disabled"])
            self.dl_btn.state(["!disabled"])
            self.paste_btn.state(["!disabled"])
            self.browse_btn.state(["!disabled"])
            self.url_entry.state(["!disabled"])
            self.fmt_table.enable()
            self.cancel_btn.state(["disabled"])

    def _on_audio_toggle(self):
        self.fmt_table.set_selectable(not self.audio_var.get())

    # ── fetch ──

    def _fetch_info(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Empty URL", "Paste a YouTube link first")
            return
        if not YOUTUBE_URL_RE.match(url):
            if not messagebox.askyesno("Unrecognized URL",
                                       "This does not look like a YouTube link.\nTry anyway?"):
                return

        self._set_busy(True)
        self.formats = []
        self.video_data = None
        self.title_var.set("Loading\u2026")
        self.channel_var.set("\u2014")
        self.duration_var.set("\u2014")
        self.date_var.set("\u2014")
        self.views_var.set("\u2014")
        self.info_var.set("")
        self.fmt_table.set_formats([])
        self.status_var.set("Fetching video info\u2026")
        self.log("Fetching: " + url)

        threading.Thread(target=self._fetch_thread, args=(url,), daemon=True).start()

    def _fetch_thread(self, url):
        try:
            ydl_opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            formats_raw = info.get("formats") or []
            self.video_data = info

            fmt_list = []
            for f in formats_raw:
                height = f.get("height")
                vcodec = f.get("vcodec", "none")
                acodec = f.get("acodec", "none")
                ext = f.get("ext", "")

                if ext in ("mhtml", "html", "json"):
                    continue
                if vcodec == "none" or not height:
                    continue

                has_audio = acodec != "none"
                size = f.get("filesize") or f.get("filesize_approx")
                fps = f.get("fps") or 0
                label = f"{height}p"
                if fps and fps > 30:
                    label += str(fps)

                fmt_list.append({
                    "id": f["format_id"],
                    "label": label,
                    "ext": ext,
                    "size": size,
                    "note": "video+audio" if has_audio else "video only",
                    "has_audio": has_audio,
                    "height": height,
                    "fps": fps,
                })

            seen_h = {}
            for f in fmt_list:
                h = f["height"]
                if h not in seen_h or f.get("size", 0) > seen_h[h].get("size", 0):
                    seen_h[h] = f
            fmt_list = list(seen_h.values())
            fmt_list.sort(key=lambda x: -x["height"])

            self.formats = fmt_list
            msg = f"Found {len(fmt_list)} format(s)"
            self.root.after(0, lambda: self._on_fetch_done(info, msg))
        except Exception as e:
            self.root.after(0, lambda: self._on_fetch_error(str(e)))

    def _on_fetch_done(self, info, msg):
        self._set_busy(False)
        self._populate_info(info)
        self.fmt_table.set_formats(self.formats)
        self.status_var.set("Ready \u2014 " + msg)
        self.info_var.set(msg)
        self.log("Title: " + info.get("title", "?"))

    def _populate_info(self, info):
        self.title_var.set(info.get("title", "\u2014"))
        self.channel_var.set(info.get("uploader") or info.get("channel", "\u2014"))
        self.duration_var.set(fmt_duration(info.get("duration")))
        raw = info.get("upload_date") or info.get("release_date", "")
        if raw and len(raw) == 8:
            raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        self.date_var.set(raw or "\u2014")
        vc = info.get("view_count")
        self.views_var.set(f"{vc:,}" if vc else "\u2014")

    def _on_fetch_error(self, msg):
        self._set_busy(False)
        self.status_var.set("Error")
        self.title_var.set("Error")
        self.info_var.set(msg)
        self.log("Error: " + msg)
        messagebox.showerror("Fetch Error", msg)

    # ── download ──

    def _browse_path(self):
        path = filedialog.askdirectory(initialdir=self.dl_path.get())
        if path:
            self.dl_path.set(path)
            self.config["dl_path"] = path
            save_config(self.config)

    def _start_download(self):
        if self.is_downloading:
            return
        if not self.video_data:
            messagebox.showwarning("No video", "Click Get Info first")
            return

        url = self.url_entry.get().strip()
        audio_only = self.audio_var.get()

        if audio_only and not FFMPEG_AVAILABLE:
            if not messagebox.askyesno(
                "FFmpeg not found",
                "MP3 conversion requires ffmpeg.\n\n"
                "Install: winget install ffmpeg\n"
                "or visit ffmpeg.org\n\n"
                "Continue anyway?"
            ):
                return

        fmt_id = None
        if not audio_only:
            sel = self.fmt_table.get_selected()
            if not sel:
                messagebox.showwarning("No format", "Select a format")
                return
            fmt_id = sel["id"]

        save_path = self.dl_path.get().strip()
        if not save_path:
            messagebox.showwarning("Bad folder", "Choose a download folder")
            return
        os.makedirs(save_path, exist_ok=True)

        self.is_downloading = True
        self.cancel_requested = False
        self.status_var.set("Starting download\u2026")
        self.log("Starting download\u2026")
        self._set_busy(True)
        self.progress.configure(mode="determinate", value=0, maximum=100)

        threading.Thread(
            target=self._download_thread,
            args=(url, fmt_id, audio_only, save_path),
            daemon=True,
        ).start()

    def _cancel_download(self):
        if not self.is_downloading:
            return
        self.cancel_requested = True
        self.status_var.set("Cancelling\u2026")
        self.log("Cancel requested")
        if self.ydl:
            try:
                self.ydl.params["break_on_reject"] = True
            except Exception:
                pass

    def _progress_hook(self, d):
        if self.cancel_requested:
            raise Exception("Download cancelled by user")
        if d.get("status") == "downloading":
            raw = d.get("_percent_str", "0%").strip().replace("%", "")
            try:
                pct = float(raw)
                self.root.after(0, lambda v=pct: self.progress.configure(value=v))
            except ValueError:
                pass
            pct_s = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            self.root.after(0, lambda: self.status_var.set(
                f"Downloading\u2026  {pct_s}  {speed}  ETA {eta}"
            ))
        elif d.get("status") == "finished":
            self.root.after(0, lambda: self.progress.configure(value=100))
            self.root.after(0, lambda: self.status_var.set("Processing\u2026"))

    def _download_thread(self, url, fmt_id, audio_only, save_path):
        try:
            outtmpl = os.path.join(save_path, "%(title)s.%(ext)s")
            ydl_opts = {
                "outtmpl": outtmpl,
                "progress_hooks": [self._progress_hook],
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
            }

            if audio_only:
                br = self.mp3_bitrate_var.get()
                bitrate = "0" if "best" in br else br
                ydl_opts.update({
                    "format": "bestaudio/best",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": bitrate,
                    }],
                })
            else:
                fmt = self.fmt_table.get_selected()
                if fmt and fmt.get("has_audio"):
                    ydl_opts["format"] = fmt_id
                else:
                    ydl_opts["format"] = f"{fmt_id}+bestaudio/best"

            if self.sub_var.get():
                langs = [x.strip() for x in self.sub_lang_var.get().split(",") if x.strip()]
                if not langs:
                    langs = ["en"]
                ydl_opts.update({
                    "writesubtitles": True,
                    "subtitleslangs": langs,
                    "writeautomaticsub": True,
                    "embedsubs": False,
                })

            self.ydl = yt_dlp.YoutubeDL(ydl_opts)
            info = self.ydl.extract_info(url, download=True)
            title = info.get("title", "Video")
            self.root.after(0, lambda: self.log("Downloaded: " + title))
            self.root.after(0, self._on_dl_success)
        except Exception as e:
            self.root.after(0, lambda: self._on_dl_error(str(e)))
        finally:
            self.ydl = None

    def _on_dl_success(self):
        self.is_downloading = False
        self.cancel_requested = False
        self._set_busy(False)
        self.progress["value"] = 100
        self.status_var.set("Complete!")
        self.log("Download finished")
        if messagebox.askyesno("Complete", "Download finished!\n\nOpen the folder?"):
            self._open_folder()

    def _on_dl_error(self, msg):
        self.is_downloading = False
        self.cancel_requested = False
        self._set_busy(False)
        self.progress["value"] = 0
        if "cancelled" in msg.lower():
            self.status_var.set("Cancelled")
            self.log("Download cancelled")
        else:
            self.status_var.set("Failed")
            self.log("Error: " + msg)
            messagebox.showerror("Download Error", msg)


def main():
    root = tk.Tk()
    YTDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
