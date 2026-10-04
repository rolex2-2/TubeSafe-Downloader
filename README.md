# TubeSafe Downloader

A local Flask application using yt-dlp and FFmpeg.

## Features
- YouTube URL information lookup
- Standard MP4 download
- MP3 audio extraction
- TV Compatible MP4 conversion
- H.264/AVC video
- AAC audio
- yuv420p pixel format
- 720p/1080p/480p options
- Browser progress/status polling

## 1. Install Python
Use Python 3.11+.

## 2. Create virtual environment (Windows PowerShell)

```powershell
cd C:\path\to\youtube_tv_downloader
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Install FFmpeg

Install FFmpeg and make sure `ffmpeg.exe` is available in PATH.

Test:

```powershell
ffmpeg -version
```

## 4. Run

```powershell
python app.py
```

Open:

http://127.0.0.1:5000

## TV Compatible mode

The TV mode re-encodes the downloaded media to:
- MP4
- H.264 (`libx264`)
- AAC audio
- yuv420p
- 160 kbps AAC
- fast-start MP4

No format can guarantee playback on every TV model. Older TVs can have additional limitations on resolution, bitrate, frame rate, audio sample rate, file size, or USB filesystem.

## Important

Use the downloader only for media you have permission to download. Respect copyright and the terms that apply to the service/content.
