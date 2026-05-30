YT Downloader — Windows standalone version

HOW TO USE:
1. Run "YT Downloader.exe"
2. Paste a YouTube URL
3. Click "Get Info"
4. Select a format from the list
5. Click "Download"

VIDEO DOWNLOAD:
Just select a quality from the list and click Download.
The file will be saved to your Downloads folder (or a folder you choose).

MP3 (AUDIO ONLY):
1. Check "Download as MP3 (audio only)"
2. Click Download
3. NOTE: This requires ffmpeg to be installed.

INSTALLING FFMPEG (for MP3 downloads):
Method 1 — winget (recommended):
  Open Command Prompt or PowerShell and run:
    winget install ffmpeg

Method 2 — Manual:
  Download from https://ffmpeg.org/download.html
  Add the "bin" folder to your PATH environment variable.

BUILD FROM SOURCE (if you have Python):
  Run build_exe.bat (requires Python 3.8+)

REQUIREMENTS:
- Windows 7 or later
- yt-dlp is bundled inside the .exe
- ffmpeg is needed ONLY for MP3 conversion
