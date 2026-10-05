from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory,
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
    Return the directory containing FFmpeg.
    """

    if not FFMPEG_PATH:
        return None

    return str(Path(FFMPEG_PATH).parent)


# ============================================================
# DENO / JAVASCRIPT RUNTIME
# ============================================================

def get_deno_path():
    """
    Find Deno.

    Render Docker:
        /root/.deno/bin/deno

    Other Linux installations:
        PATH lookup

    Windows:
        PATH lookup
    """

    possible_paths = [
        "/root/.deno/bin/deno",
        "/usr/local/bin/deno",
        "/usr/bin/deno",
    ]

    for path in possible_paths:
        if Path(path).exists():
            return path

    return shutil.which("deno")


DENO_PATH = get_deno_path()

DENO_READY = DENO_PATH is not None


# ============================================================
# YT-DLP COMMON OPTIONS
# ============================================================

def get_common_ytdlp_options(progress_hook=None):
    """
    Common yt-dlp configuration.

    Current yt-dlp versions use an external JavaScript
    runtime for YouTube challenge solving.

    Deno is the recommended runtime.
    """

    options = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "continuedl": True,

        # Allow yt-dlp to obtain EJS scripts when necessary.
        "remote_components": ["ejs:github"],
    }

    if progress_hook:
        options["progress_hooks"] = [
            progress_hook
        ]

    # Explicitly tell yt-dlp which JS runtime to use.
    if DENO_READY:

        options["js_runtimes"] = {
            "deno": DENO_PATH
        }

    return options


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
        name,
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
        re.IGNORECASE,
    )

    return bool(pattern.match(url))


# ============================================================
# JOB UPDATE
# ============================================================

def set_job(job_id, **values):

    with jobs_lock:

        current = jobs.get(
            job_id,
            {},
        )

        current.update(values)

        jobs[job_id] = current


# ============================================================
# PROGRESS
# ============================================================

def update_progress(job_id, data):

    status = data.get("status")

    if status == "downloading":

        percent = data.get(
            "_percent_str",
            "0%",
        )

        percent = (
            str(percent)
            .replace("%", "")
            .strip()
        )

        try:
            value = float(percent)
        except (
            ValueError,
            TypeError,
        ):
            value = 0

        speed = data.get(
            "_speed_str",
            "",
        )

        eta = data.get(
            "_eta_str",
            "",
        )

        set_job(
            job_id,
            status="downloading",
            progress=min(
                99,
                max(
                    0,
                    value,
                ),
            ),
            message="Downloading...",
            speed=speed,
            eta=eta,
        )

    elif status == "finished":

        set_job(
            job_id,
            status="processing",
            progress=99,
            message="Processing downloaded file...",
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

        # H.264 video
        "-c:v",
        "libx264",

        "-preset",
        "medium",

        "-crf",
        "22",

        # Broad TV compatibility
        "-pix_fmt",
        "yuv420p",

        # AAC audio
        "-c:a",
        "aac",

        "-b:a",
        "160k",

        # Better playback from USB/network
        "-movflags",
        "+faststart",

        str(dst),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:

        raise RuntimeError(
            "FFmpeg conversion failed:\n"
            + result.stderr[-3000:]
        )


# ============================================================
# DOWNLOAD JOB
# ============================================================

def download_job(
    job_id,
    url,
    mode,
    quality,
):

    set_job(
        job_id,
        status="starting",
        progress=0,
        message="Starting download...",
    )

    try:

        # ----------------------------------------------------
        # FFmpeg check
        # ----------------------------------------------------

        if not FFMPEG_READY:

            raise RuntimeError(
                "FFmpeg was not found on the server. "
                "The Docker deployment should install FFmpeg automatically."
            )

        # ----------------------------------------------------
        # Deno check
        # ----------------------------------------------------

        if not DENO_READY:

            raise RuntimeError(
                "Deno JavaScript runtime was not found. "
                "It is required by current yt-dlp YouTube extraction."
            )

        # ----------------------------------------------------
        # Output template
        # ----------------------------------------------------

        output_template = str(
            DOWNLOAD_DIR
            / f"{job_id}_%(title)s.%(ext)s"
        )

        # ----------------------------------------------------
        # Progress hook
        # ----------------------------------------------------

        progress_hook = lambda data: (
            update_progress(
                job_id,
                data,
            )
        )

        # ----------------------------------------------------
        # Common options
        # ----------------------------------------------------

        common_options = get_common_ytdlp_options(
            progress_hook
        )

        common_options.update({

            "outtmpl": output_template,

            "ffmpeg_location":
                get_ffmpeg_directory(),

        })

        # ----------------------------------------------------
        # MP3
        # ----------------------------------------------------

        if mode == "mp3":

            options = {
                **common_options,

                "format":
                    "bestaudio/best",

                "postprocessors": [

                    {
                        "key":
                            "FFmpegExtractAudio",

                        "preferredcodec":
                            "mp3",

                        "preferredquality":
                            "192",
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
                    "bestvideo+bestaudio/"
                    "best"
                )

            options = {
                **common_options,

                "format":
                    format_string,

                "merge_output_format":
                    "mp4",
            }

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True,
            )

            title = clean_name(
                info.get(
                    "title",
                    "download",
                )
            )

        # ----------------------------------------------------
        # FIND DOWNLOADED FILE
        # ----------------------------------------------------

        candidates = list(
            DOWNLOAD_DIR.glob(
                f"{job_id}_*"
            )
        )

        candidates = [

            file

            for file in candidates

            if not file.name.endswith(
                (
                    ".part",
                    ".ytdl",
                )
            )

        ]

        if not candidates:

            raise FileNotFoundError(
                "Downloaded file was not found."
            )

        source_file = max(
            candidates,
            key=lambda file:
            file.stat().st_mtime,
        )

        # ----------------------------------------------------
        # TV COMPATIBLE
        # ----------------------------------------------------

        if mode == "tv":

            set_job(
                job_id,

                status="processing",

                progress=99,

                message=(
                    "Converting to "
                    "H.264 + AAC..."
                ),
            )

            final_name = (
                f"{title}_TV_Compatible.mp4"
            )

            final_path = (
                DOWNLOAD_DIR
                / final_name
            )

            run_ffmpeg_tv_compatible(
                source_file,
                final_path,
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

            mp3_candidates = list(
                DOWNLOAD_DIR.glob(
                    f"{job_id}_*.mp3"
                )
            )

            if mp3_candidates:

                final_path = max(
                    mp3_candidates,
                    key=lambda file:
                    file.stat().st_mtime,
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

            filename=final_path.name,

        )

    except Exception as exc:

        set_job(

            job_id,

            status="error",

            progress=0,

            message=str(exc),

        )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    return render_template(
        "index.html",
        ffmpeg_ready=FFMPEG_READY,
    )


# ============================================================
# VIDEO INFORMATION
# ============================================================

@app.post("/api/info")
def video_info():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    url = str(
        payload.get(
            "url",
            "",
        )
    ).strip()

    if not url:

        return jsonify({
            "error":
                "Enter a YouTube URL."
        }), 400

    if not valid_youtube_url(url):

        return jsonify({
            "error":
                "Please enter a valid YouTube URL."
        }), 400

    try:

        # IMPORTANT:
        # Use the same JS/EJS configuration
        # here as in the actual download.

        options = get_common_ytdlp_options()

        options.update({

            "skip_download": True,

        })

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            data = ydl.extract_info(
                url,
                download=False,
            )

        # ----------------------------------------------------
        # Duration
        # ----------------------------------------------------

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
                    60,
                )

                hours, minutes = divmod(
                    minutes,
                    60,
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

        # ----------------------------------------------------
        # Return information
        # ----------------------------------------------------

        return jsonify({

            "title":
                data.get(
                    "title",
                    "Unknown title",
                ),

            "thumbnail":
                data.get(
                    "thumbnail",
                    "",
                ),

            "duration":
                duration or "Unknown",

            "uploader":
                data.get(
                    "uploader",
                    "Unknown channel",
                ),

            "url":
                url,

        })

    except Exception as exc:

        return jsonify({

            "error":
                str(exc),

        }), 400


# ============================================================
# START DOWNLOAD
# ============================================================

@app.post("/api/download")
def start_download():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    url = str(
        payload.get(
            "url",
            "",
        )
    ).strip()

    mode = str(
        payload.get(
            "mode",
            "tv",
        )
    )

    quality = str(
        payload.get(
            "quality",
            "720p",
        )
    )

    if not url:

        return jsonify({
            "error":
                "Enter a YouTube URL."
        }), 400

    if not valid_youtube_url(url):

        return jsonify({
            "error":
                "Please enter a valid YouTube URL."
        }), 400

    if mode not in {
        "tv",
        "mp4",
        "mp3",
    }:

        return jsonify({
            "error":
                "Invalid download format."
        }), 400

    if quality not in {
        "480p",
        "720p",
        "1080p",
    }:

        quality = "720p"

    # --------------------------------------------------------
    # Generate job ID
    # --------------------------------------------------------

    job_id = uuid.uuid4().hex

    set_job(
        job_id,

        status="queued",

        progress=0,

        message="Queued...",
    )

    # --------------------------------------------------------
    # Background download
    # --------------------------------------------------------

    thread = threading.Thread(

        target=download_job,

        args=(

            job_id,

            url,

            mode,

            quality,

        ),

        daemon=True,
    )

    thread.start()

    return jsonify({

        "job_id":
            job_id,

    })


# ============================================================
# JOB STATUS
# ============================================================

@app.get("/api/status/<job_id>")
def job_status(job_id):

    with jobs_lock:

        job = jobs.get(
            job_id
        )

    if not job:

        return jsonify({

            "status":
                "unknown",

            "message":
                "Job not found.",

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

        as_attachment=True,
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():

    return jsonify({

        "status":
            "ok",

        "ffmpeg":
            FFMPEG_READY,

        "deno":
            DENO_READY,

        "deno_path":
            DENO_PATH,

        "yt_dlp":
            yt_dlp.version.__version__,

    })


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000,
        )
    )

    app.run(

        host="0.0.0.0",

        port=port,

        debug=True,
    )