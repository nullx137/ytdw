import sys
import os
import re
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import timedelta

# ── Windows DPI awareness ─────────────────────
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


# ── helpers ────────────────────────────────────

def fmt_duration(secs):
    if not secs:
        return "—"
    try:
        return str(timedelta(seconds=int(secs)))
    except (ValueError, OverflowError):
        return "—"


def fmt_size(size):
    if not size:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def resource_path(relative_path):
    """Get path for PyInstaller bundle."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return relative_path


# ── FFmpeg check ───────────────────────────────

def check_ffmpeg():
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except Exception:
        return False


FFMPEG_AVAILABLE = check_ffmpeg()


# ── widgets ────────────────────────────────────

class FormatTable(ttk.Frame):
    def __init__(self, parent, callback, **kw):
        super().__init__(parent, **kw)
        self.callback = callback
        self._data = []

        columns = ("#0", "ext", "size")
        self.tree = ttk.Treeview(
            self, columns=columns[1:], show="tree headings",
            selectmode="browse", height=10
        )
        self.tree.heading("#0", text="Quality")
        self.tree.heading("ext", text="Format")
        self.tree.heading("size", text="Size")
        self.tree.column("#0", width=110, minwidth=80)
        self.tree.column("ext", width=70, minwidth=50)
        self.tree.column("size", width=95, minwidth=70)

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
        for entry in formats:
            self.tree.insert("", tk.END,
                text=entry.get("label", entry["id"]),
                values=(entry.get("ext", ""), fmt_size(entry.get("size"))),
                iid=entry["id"],
            )
        if formats:
            self.tree.selection_set(formats[0]["id"])
            self.tree.focus(formats[0]["id"])
            self.tree.see(formats[0]["id"])
        self.callback(f"Format: video+audio | {len(formats)} available")

    def get_selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        for fmt in self._data:
            if fmt["id"] == sel[0]:
                return fmt
        return None

    def enable(self):
        self.tree.state(["!disabled"])

    def disable(self):
        self.tree.state(["disabled"])

    def _on_select(self, _e):
        fmt = self.get_selected()
        if fmt:
            parts = [f"Format: {fmt.get('label', fmt['id'])}"]
            if fmt.get("size"):
                parts.append(fmt_size(fmt["size"]))
            if fmt.get("note"):
                parts.append(fmt["note"])
            self.callback(" | ".join(parts))


class VideoInfo(ttk.LabelFrame):
    def __init__(self, parent, **kw):
        super().__init__(parent, text="Video Info", **kw)
        self.columnconfigure(1, weight=1)

        self.title_var = tk.StringVar(value="—")
        self.duration_var = tk.StringVar(value="—")
        self.channel_var = tk.StringVar(value="—")
        self.date_var = tk.StringVar(value="—")

        labels_def = [
            ("Title:", self.title_var, 0),
            ("Channel:", self.channel_var, 1),
            ("Duration:", self.duration_var, 2),
            ("Uploaded:", self.date_var, 3),
        ]

        for i, (lbl, var, row) in enumerate(labels_def):
            ttk.Label(self, text=lbl, font=("", 9, "bold")).grid(
                row=row, column=0, sticky=tk.W, padx=(5, 2), pady=1
            )
            ttk.Label(self, textvariable=var, wraplength=400).grid(
                row=row, column=1, sticky=tk.W, padx=(0, 5), pady=1
            )

        self.audio_var = tk.BooleanVar()
        ttk.Checkbutton(
            self, text="Download as MP3 (audio only)", variable=self.audio_var
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(8, 2))

        self.info_var = tk.StringVar()
        ttk.Label(self, textvariable=self.info_var, wraplength=450, font=("", 8)).grid(
            row=5, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(2, 5)
        )

    def set_info(self, data, formats_str):
        self.title_var.set(data.get("title", "—"))
        self.duration_var.set(fmt_duration(data.get("duration")))
        self.channel_var.set(data.get("uploader", data.get("channel", "—")))
        raw = data.get("upload_date", data.get("release_date", ""))
        if raw and len(raw) == 8:
            raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        self.date_var.set(raw or "—")
        self.info_var.set(formats_str)


# ── main app ───────────────────────────────────

class YTDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("YT Downloader")
        self.root.geometry("800x620")
        self.root.minsize(720, 540)

        if os.name == "nt":
            try:
                self.root.iconbitmap(resource_path("icon.ico"))
            except Exception:
                pass

        self.dl_path = tk.StringVar(value=self._get_default_download_dir())
        self.video_data = None
        self.formats = []
        self.is_downloading = False

        self._build_ui()
        self._setup_bindings()

        if not FFMPEG_AVAILABLE:
            self.log("WARNING: ffmpeg not found. Audio download (MP3) will fail.")
            self.log("Download: https://ffmpeg.org/download.html or use 'winget install ffmpeg'")

    def _get_default_download_dir(self):
        if os.name == "nt":
            return os.path.join(os.environ["USERPROFILE"], "Downloads")
        return os.path.join(os.path.expanduser("~"), "Downloads")

    def _build_ui(self):
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # URL row
        url_frame = ttk.Frame(main)
        url_frame.pack(fill=tk.X, pady=(0, 8))
        url_frame.columnconfigure(1, weight=1)

        ttk.Label(url_frame, text="YouTube URL:").grid(row=0, column=0, padx=(0, 6))
        self.url_entry = ttk.Entry(url_frame)
        self.url_entry.grid(row=0, column=1, sticky=tk.EW)

        self.fetch_btn = ttk.Button(url_frame, text="Get Info", command=self.fetch_info, width=12)
        self.fetch_btn.grid(row=0, column=2, padx=(6, 0))

        # Middle: info + formats
        middle = ttk.Frame(main)
        middle.pack(fill=tk.BOTH, expand=True)
        middle.columnconfigure(0, weight=1)
        middle.columnconfigure(1, weight=1)
        middle.rowconfigure(0, weight=1)

        left_frame = ttk.Frame(middle)
        left_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 5))
        self.info_panel = VideoInfo(left_frame)
        self.info_panel.pack(fill=tk.BOTH, expand=True)

        right_frame = ttk.Frame(middle)
        right_frame.grid(row=0, column=1, sticky=tk.NSEW, padx=(5, 0))

        fmt_lf = ttk.LabelFrame(right_frame, text="Available Formats")
        fmt_lf.pack(fill=tk.BOTH, expand=True)
        fmt_lf.columnconfigure(0, weight=1)
        fmt_lf.rowconfigure(0, weight=1)

        self.fmt_table = FormatTable(fmt_lf, self._on_format_info)
        self.fmt_table.grid(row=0, column=0, sticky=tk.NSEW, padx=4, pady=4)

        # Bottom: path + download
        bottom = ttk.Frame(main)
        bottom.pack(fill=tk.X, pady=(10, 0))
        bottom.columnconfigure(1, weight=1)

        ttk.Label(bottom, text="Save folder:").grid(row=0, column=0, padx=(0, 6))
        self.path_entry = ttk.Entry(bottom, textvariable=self.dl_path)
        self.path_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 6))

        browse_btn = ttk.Button(bottom, text="Browse", command=self.browse_path, width=10)
        browse_btn.grid(row=0, column=2)

        # Download
        self.dl_btn = ttk.Button(main, text="Download", command=self.start_download)
        self.dl_btn.pack(pady=(10, 6))

        # Progress
        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 4))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status_var, font=("", 9)).pack()

        # Log
        log_lf = ttk.LabelFrame(main, text="Log")
        log_lf.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        log_lf.columnconfigure(0, weight=1)
        log_lf.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_lf, height=5, wrap=tk.WORD, font=("", 9))
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW, padx=4, pady=4)

        log_scroll = ttk.Scrollbar(log_lf, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky=tk.NS)

    def _setup_bindings(self):
        self.url_entry.bind("<Return>", lambda _: self.fetch_info())

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def _on_format_info(self, text):
        self.info_panel.info_var.set(text)

    def set_busy(self, busy):
        state = tk.DISABLED if busy else tk.NORMAL
        self.fetch_btn.configure(state=state)
        self.dl_btn.configure(state=state)
        (self.fmt_table.disable if busy else self.fmt_table.enable)()

    # ── fetch ──

    def fetch_info(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Empty URL", "Paste a YouTube link first")
            return

        self.set_busy(True)
        self.formats = []
        self.video_data = None
        self.info_panel.set_info({"title": "Loading…"}, "Fetching…")
        self.fmt_table.set_formats([])
        self.status_var.set("Fetching video info…")
        self.log("Fetching: " + url)
        self.root.update_idletasks()

        threading.Thread(target=self._fetch_thread, args=(url,), daemon=True).start()

    def _fetch_thread(self, url):
        try:
            ydl_opts = {"quiet": True, "no_warnings": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            formats_raw = info.get("formats") or []
            self.video_data = info

            seen = set()
            fmt_list = []
            for f in formats_raw:
                height = f.get("height")
                vcodec = f.get("vcodec", "none")
                ext = f.get("ext", "")

                if ext in ("mhtml", "html", "json"):
                    continue
                if not height:
                    continue
                if vcodec == "none":
                    continue

                key = (height, ext, f.get("fps"))
                if key in seen:
                    continue
                seen.add(key)

                fmt_list.append({
                    "id": f["format_id"],
                    "label": f"{height}p",
                    "ext": ext,
                    "size": f.get("filesize") or f.get("filesize_approx"),
                    "note": f.get("format_note", "") or "",
                    "fps": f.get("fps"),
                })

            fmt_list.sort(key=lambda x: -int(re.sub(r"\D", "", x["id"]) or 0))

            deduped = {}
            for f in fmt_list:
                key = f["label"]
                if key not in deduped:
                    deduped[key] = f
            fmt_list = list(deduped.values())
            fmt_list.sort(key=lambda x: -int(re.sub(r"\D", "", x["id"]) or 0))

            self.formats = fmt_list
            formats_str = f"{len(fmt_list)} video formats"
            self.root.after(0, lambda: self._on_fetch_done(info, formats_str))
        except Exception as e:
            self.root.after(0, lambda: self._on_fetch_error(str(e)))

    def _on_fetch_done(self, info, formats_str):
        self.set_busy(False)
        self.info_panel.set_info(info, formats_str)
        self.fmt_table.set_formats(self.formats)
        self.status_var.set("Ready — " + formats_str)
        self.log("Title: " + info.get("title", "?"))

    def _on_fetch_error(self, msg):
        self.set_busy(False)
        self.status_var.set("Error")
        self.info_panel.set_info({"title": "Error"}, msg)
        self.log("Error: " + msg)
        messagebox.showerror("Error", "Failed to get video info:\n" + msg)

    # ── download ──

    def browse_path(self):
        path = filedialog.askdirectory(initialdir=self.dl_path.get())
        if path:
            self.dl_path.set(path)

    def start_download(self):
        if self.is_downloading:
            return
        if not self.video_data:
            messagebox.showwarning("No video", "First get video info")
            return

        url = self.url_entry.get().strip()
        audio_only = self.info_panel.audio_var.get()

        if audio_only and not FFMPEG_AVAILABLE:
            ret = messagebox.askyesno(
                "FFmpeg not found",
                "MP3 conversion requires ffmpeg.\n\n"
                "Download: https://ffmpeg.org/download.html\n"
                "Or run in terminal:  winget install ffmpeg\n\n"
                "Continue anyway (may fail)?"
            )
            if not ret:
                return

        fmt_id = None
        if not audio_only:
            sel_fmt = self.fmt_table.get_selected()
            if not sel_fmt:
                messagebox.showwarning("No format", "Select a video format")
                return
            fmt_id = sel_fmt["id"]

        save_path = self.dl_path.get().strip()
        if not save_path or not os.path.isdir(save_path):
            messagebox.showwarning("Bad folder", "Choose an existing download folder")
            return

        self.is_downloading = True
        self.set_busy(True)
        self.progress["value"] = 0
        self.status_var.set("Starting download…")
        self.log("Starting download…")
        self.root.update_idletasks()

        threading.Thread(
            target=self._download_thread,
            args=(url, fmt_id, audio_only, save_path),
            daemon=True,
        ).start()

    def _progress_hook(self, d):
        if not self.is_downloading:
            return
        if d["status"] == "downloading":
            raw = d.get("_percent_str", "0%").strip().replace("%", "")
            try:
                self.root.after(0, lambda v=float(raw): self.progress.configure(value=v))
            except ValueError:
                pass

            pct = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "")
            eta = d.get("_eta_str", "")
            self.root.after(0, lambda: self.status_var.set(
                f"Downloading… {pct}  {speed}  ETA: {eta}"
            ))
        elif d["status"] == "finished":
            self.root.after(0, lambda: self.progress.configure(value=100))
            self.root.after(0, lambda: self.status_var.set("Processing…"))

    def _download_thread(self, url, fmt_id, audio_only, save_path):
        try:
            outtmpl = os.path.join(save_path, "%(title)s.%(ext)s")
            ydl_opts = {
                "outtmpl": outtmpl,
                "progress_hooks": [self._progress_hook],
                "quiet": True,
                "no_warnings": True,
            }

            if audio_only:
                ydl_opts.update({
                    "format": "bestaudio/best",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                })
            else:
                ydl_opts["format"] = fmt_id + "+bestaudio/best"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                self.root.after(0, lambda t=info.get("title", "Video"): self.log("Downloaded: " + t))

            self.root.after(0, self._on_dl_success)
        except Exception as e:
            self.root.after(0, lambda: self._on_dl_error(str(e)))

    def _on_dl_success(self):
        self.is_downloading = False
        self.set_busy(False)
        self.status_var.set("Complete!")
        self.progress["value"] = 100
        self.log("Download finished successfully")
        messagebox.showinfo("Done", "Download completed!")

    def _on_dl_error(self, msg):
        self.is_downloading = False
        self.set_busy(False)
        self.progress["value"] = 0
        self.status_var.set("Failed")
        self.log("Error: " + msg)
        messagebox.showerror("Error", "Download failed:\n" + msg)


# ── run ────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = YTDownloaderApp(root)
    root.mainloop()
