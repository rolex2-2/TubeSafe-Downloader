from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory
)

from pathlib import Path
import os
import re
import shutil
import subprocess
import threading
import uuid

import yt_dlp


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

LOCAL_FFMPEG = BASE_DIR / "ffmpeg" / "ffmpeg.exe"


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

# Limit incoming JSON/request size
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024


# ============================================================
# JOB STORAGE
# ============================================================

jobs = {}

jobs_lock = threading.Lock()


# ============================================================
# FFMPEG DETECTION
# ============================================================

def get_ffmpeg_path():
    """
    Find FFmpeg.

    Priority:
    1. Local Windows ffmpeg/ffmpeg.exe
    2. FFmpeg installed in system PATH
    """

    if LOCAL_FFMPEG.exists():
        return str(LOCAL_FFMPEG)

    system_ffmpeg = shutil.which("ffmpeg")

    if system_ffmpeg:
        return system_ffmpeg

    return None


FFMPEG_PATH = get_ffmpeg_path()

FFMPEG_READY = FFMPEG_PATH is not None


def get_ffmpeg_directory():
    """
    yt-dlp accepts either an FFmpeg executable or its directory.
    """

    if not FFMPEG_PATH:
        return None

    return str(Path(FFMPEG_PATH).parent)


# ============================================================
# FILE NAME CLEANING
# ============================================================

def clean_name(name):
    """
    Make a safe filename for Windows/Linux.
    """

    name = str(name or "download")

    name = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        name
    )

    name = name.strip(" .")

    if not name:
        name = "download"

    return name[:180]


# ============================================================
# URL VALIDATION
# ============================================================

def valid_youtube_url(url):
    """
    Allow YouTube URLs only.
    """

    pattern = re.compile(
        r"^https?://"
        r"(www\.)?"
        r"(youtube\.com|youtu\.be|m\.youtube\.com)"
        r"/",
        re.IGNORECASE
    )

    return bool(pattern.match(url))


# ============================================================
# JOB UPDATE
# ============================================================

def set_job(job_id, **values):

    with jobs_lock:

        current = jobs.get(job_id, {})

        current.update(values)

        jobs[job_id] = current


# ============================================================
# PROGRESS
# ============================================================

def update_progress(job_id, data):

    status = data.get("status")

    if status == "downloading":

        percent = data.get("_percent_str", "0%")

        percent = (
            str(percent)
            .replace("%", "")
            .strip()
        )

        try:
            value = float(percent)
        except (ValueError, TypeError):
            value = 0

        speed = data.get("_speed_str", "")

        eta = data.get("_eta_str", "")

        set_job(
            job_id,
            status="downloading",
            progress=min(99, max(0, value)),
            message="Downloading...",
            speed=speed,
            eta=eta
        )

    elif status == "finished":

        set_job(
            job_id,
            status="processing",
            progress=99,
            message="Processing downloaded file..."
        )


# ============================================================
# TV CONVERSION
# ============================================================

def run_ffmpeg_tv_compatible(src, dst):

    if not FFMPEG_PATH:

        raise RuntimeError(
            "FFmpeg was not found. "
            "Install FFmpeg or use the Docker deployment."
        )

    command = [

        FFMPEG_PATH,

        "-y",

        "-i",
        str(src),

        "-map",
        "0:v:0",

        "-map",
        "0:a:0?",

        # H.264
        "-c:v",
        "libx264",

        "-preset",
        "medium",

        "-crf",
        "22",

        # Broad TV compatibility
        "-pix_fmt",
        "yuv420p",

        # AAC
        "-c:a",
        "aac",

        "-b:a",
        "160k",

        # Better playback from USB/network
        "-movflags",
        "+faststart",

        str(dst)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            "FFmpeg conversion failed:\n"
            + result.stderr[-3000:]
        )


# ============================================================
# DOWNLOAD JOB
# ============================================================

def download_job(job_id, url, mode, quality):

    set_job(
        job_id,
        status="starting",
        progress=0,
        message="Starting download..."
    )

    temporary_files = []

    try:

        # ----------------------------------------------------
        # Check FFmpeg
        # ----------------------------------------------------

        if mode in {"tv", "mp3"} and not FFMPEG_READY:

            raise RuntimeError(
                "FFmpeg is required for this format, "
                "but it was not found on the server."
            )


        # ----------------------------------------------------
        # Output template
        # ----------------------------------------------------

        output_template = str(
            DOWNLOAD_DIR /
            f"{job_id}_%(title)s.%(ext)s"
        )


        # ----------------------------------------------------
        # COMMON OPTIONS
        # ----------------------------------------------------

        common_options = {

            "outtmpl": output_template,

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

            "progress_hooks": [
                lambda data:
                update_progress(job_id, data)
            ],

            "retries": 3,

            "fragment_retries": 3,

            "continuedl": True,
        }


        # Tell yt-dlp where FFmpeg is
        if FFMPEG_READY:

            common_options["ffmpeg_location"] = (
                get_ffmpeg_directory()
            )


        # ----------------------------------------------------
        # MP3
        # ----------------------------------------------------

        if mode == "mp3":

            options = {

                **common_options,

                "format": "bestaudio/best",

                "postprocessors": [

                    {

                        "key": "FFmpegExtractAudio",

                        "preferredcodec": "mp3",

                        "preferredquality": "192",

                    }

                ],
            }


        # ----------------------------------------------------
        # VIDEO
        # ----------------------------------------------------

        else:

            if quality == "480p":

                format_string = (
                    "bestvideo[height<=480]"
                    "+bestaudio/"
                    "best[height<=480]"
                )

            elif quality == "720p":

                format_string = (
                    "bestvideo[height<=720]"
                    "+bestaudio/"
                    "best[height<=720]"
                )

            elif quality == "1080p":

                format_string = (
                    "bestvideo[height<=1080]"
                    "+bestaudio/"
                    "best[height<=1080]"
                )

            else:

                format_string = (
                    "bestvideo+bestaudio/best"
                )


            options = {

                **common_options,

                "format": format_string,

                "merge_output_format": "mp4",
            }


        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        with yt_dlp.YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            title = clean_name(
                info.get("title", "download")
            )


        # ----------------------------------------------------
        # FIND DOWNLOADED FILE
        # ----------------------------------------------------

        candidates = list(
            DOWNLOAD_DIR.glob(
                f"{job_id}_*"
            )
        )

        # Ignore partial files
        candidates = [
            file
            for file in candidates
            if not file.name.endswith(
                (".part", ".ytdl")
            )
        ]

        if not candidates:

            raise FileNotFoundError(
                "Downloaded file was not found."
            )


        source_file = max(
            candidates,
            key=lambda file:
            file.stat().st_mtime
        )


        # ----------------------------------------------------
        # TV COMPATIBLE CONVERSION
        # ----------------------------------------------------

        if mode == "tv":

            set_job(
                job_id,
                status="processing",
                progress=99,
                message=(
                    "Converting to "
                    "H.264 + AAC..."
                )
            )

            final_name = (
                f"{title}_TV_Compatible.mp4"
            )

            final_path = (
                DOWNLOAD_DIR /
                final_name
            )

            run_ffmpeg_tv_compatible(
                source_file,
                final_path
            )

            if (
                source_file.exists()
                and source_file != final_path
            ):

                source_file.unlink()


        # ----------------------------------------------------
        # MP3
        # ----------------------------------------------------

        elif mode == "mp3":

            # yt-dlp normally creates .mp3
            mp3_candidates = list(
                DOWNLOAD_DIR.glob(
                    f"{job_id}_*.mp3"
                )
            )

            if mp3_candidates:

                final_path = max(
                    mp3_candidates,
                    key=lambda file:
                    file.stat().st_mtime
                )

            else:

                final_path = source_file


        # ----------------------------------------------------
        # STANDARD MP4
        # ----------------------------------------------------

        else:

            final_path = source_file


        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        set_job(
            job_id,
            status="complete",
            progress=100,
            message="Ready for download.",
            filename=final_path.name
        )


    except Exception as exc:

        set_job(
            job_id,
            status="error",
            progress=0,
            message=str(exc)
        )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    return render_template(
        "index.html",
        ffmpeg_ready=FFMPEG_READY
    )


# ============================================================
# VIDEO INFORMATION
# ============================================================

@app.post("/api/info")
def video_info():

    payload = request.get_json(
        silent=True
    ) or {}

    url = str(
        payload.get("url", "")
    ).strip()


    if not url:

        return jsonify({
            "error": "Enter a YouTube URL."
        }), 400


    if not valid_youtube_url(url):

        return jsonify({
            "error": "Please enter a valid YouTube URL."
        }), 400


    try:

        options = {

            "quiet": True,

            "no_warnings": True,

            "noplaylist": True,

            "skip_download": True,
        }


        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            data = ydl.extract_info(
                url,
                download=False
            )


        duration = data.get(
            "duration_string"
        )

        if not duration:

            seconds = data.get(
                "duration"
            )

            if seconds:

                minutes, seconds = divmod(
                    int(seconds),
                    60
                )

                hours, minutes = divmod(
                    minutes,
                    60
                )

                if hours:

                    duration = (
                        f"{hours}:"
                        f"{minutes:02d}:"
                        f"{seconds:02d}"
                    )

                else:

                    duration = (
                        f"{minutes}:"
                        f"{seconds:02d}"
                    )


        return jsonify({

            "title":
                data.get(
                    "title",
                    "Unknown title"
                ),

            "thumbnail":
                data.get(
                    "thumbnail",
                    ""
                ),

            "duration":
                duration or "Unknown",

            "uploader":
                data.get(
                    "uploader",
                    "Unknown channel"
                ),

            "url":
                url
        })


    except Exception as exc:

        return jsonify({
            "error": str(exc)
        }), 400


# ============================================================
# START DOWNLOAD
# ============================================================

@app.post("/api/download")
def start_download():

    payload = request.get_json(
        silent=True
    ) or {}


    url = str(
        payload.get("url", "")
    ).strip()


    mode = str(
        payload.get(
            "mode",
            "tv"
        )
    )


    quality = str(
        payload.get(
            "quality",
            "720p"
        )
    )


    if not url:

        return jsonify({
            "error": "Enter a YouTube URL."
        }), 400


    if not valid_youtube_url(url):

        return jsonify({
            "error": "Please enter a valid YouTube URL."
        }), 400


    if mode not in {
        "tv",
        "mp4",
        "mp3"
    }:

        return jsonify({
            "error": "Invalid download format."
        }), 400


    if quality not in {
        "480p",
        "720p",
        "1080p"
    }:

        quality = "720p"


    # --------------------------------------------------------
    # Generate unique job ID
    # --------------------------------------------------------

    job_id = uuid.uuid4().hex


    set_job(
        job_id,
        status="queued",
        progress=0,
        message="Queued..."
    )


    # --------------------------------------------------------
    # Start background job
    # --------------------------------------------------------

    thread = threading.Thread(

        target=download_job,

        args=(
            job_id,
            url,
            mode,
            quality
        ),

        daemon=True
    )

    thread.start()


    return jsonify({
        "job_id": job_id
    })


# ============================================================
# JOB STATUS
# ============================================================

@app.get("/api/status/<job_id>")
def job_status(job_id):

    with jobs_lock:

        job = jobs.get(job_id)


    if not job:

        return jsonify({
            "status": "unknown",
            "message": "Job not found."
        }), 404


    return jsonify(job)


# ============================================================
# DOWNLOAD FILE
# ============================================================

@app.get("/download/<path:filename>")
def download_file(filename):

    return send_from_directory(
        DOWNLOAD_DIR,
        filename,
        as_attachment=True
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():

    return jsonify({

        "status": "ok",

        "ffmpeg": FFMPEG_READY,

        "yt_dlp": yt_dlp.version.__version__

    })


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=True
    )