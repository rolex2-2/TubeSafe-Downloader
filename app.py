import os
import uuid
import threading
import shutil
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory
)

import yt_dlp


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


# ============================================================
# JOB STORAGE
# ============================================================

# The application uses an in-memory jobs dictionary.
# Therefore use ONE Gunicorn worker.
#
# CMD:
# gunicorn --bind 0.0.0.0:5000 --workers 1 app:app

jobs = {}


# ============================================================
# FIND FFMPEG
# ============================================================

def get_ffmpeg_path():

    # Local Windows installation
    local_ffmpeg = (
        BASE_DIR /
        "ffmpeg" /
        "ffmpeg.exe"
    )

    if local_ffmpeg.exists():
        return str(local_ffmpeg)

    # Docker/Linux installation
    system_ffmpeg = shutil.which("ffmpeg")

    if system_ffmpeg:
        return system_ffmpeg

    return None


def get_ffprobe_path():

    # Local Windows installation
    local_ffprobe = (
        BASE_DIR /
        "ffmpeg" /
        "ffprobe.exe"
    )

    if local_ffprobe.exists():
        return str(local_ffprobe)

    # Docker/Linux installation
    system_ffprobe = shutil.which("ffprobe")

    if system_ffprobe:
        return system_ffprobe

    return None


FFMPEG_PATH = get_ffmpeg_path()
FFPROBE_PATH = get_ffprobe_path()

FFMPEG_READY = bool(FFMPEG_PATH)


# ============================================================
# FIND DENO
# ============================================================

def get_deno_path():

    possible_paths = [

        # Optional environment variable
        os.environ.get("DENO_PATH"),

        # Docker/Linux
        "/root/.deno/bin/deno",
        "/usr/local/bin/deno",
        "/usr/bin/deno",

        # Windows
        str(
            Path.home() /
            ".deno" /
            "bin" /
            "deno.exe"
        ),

        # Optional project-local Deno
        str(
            BASE_DIR /
            "deno.exe"
        )
    ]

    for path in possible_paths:

        if not path:
            continue

        if Path(path).exists():

            return str(
                Path(path)
            )

    # Try PATH
    system_deno = shutil.which("deno")

    if system_deno:
        return system_deno

    return None


DENO_PATH = get_deno_path()

DENO_READY = bool(DENO_PATH)


# ============================================================
# COMMON YT-DLP OPTIONS
# ============================================================

def get_common_ytdlp_options(progress_hook=None):

    options = {

        "quiet": True,

        "no_warnings": True,

        "noplaylist": True,

        # Current YouTube extraction can require
        # yt-dlp's external EJS components.
        "remote_components": [
            "ejs:github"
        ]
    }

    # --------------------------------------------------------
    # DENO
    # --------------------------------------------------------

    if DENO_READY:

        options["js_runtimes"] = {

            "deno": {

                "path": DENO_PATH
            }
        }

    # --------------------------------------------------------
    # PROGRESS HOOK
    # --------------------------------------------------------

    if progress_hook:

        options["progress_hooks"] = [
            progress_hook
        ]

    return options


# ============================================================
# FORMAT OPTIONS
# ============================================================

def get_format_options(mode, quality):

    if not FFMPEG_READY:

        raise RuntimeError(
            "FFmpeg is not installed on this server."
        )

    # ========================================================
    # MP3
    # ========================================================

    if mode == "mp3":

        return {

            "format":
                "bestaudio/best",

            "postprocessors": [

                {
                    "key":
                        "FFmpegExtractAudio",

                    "preferredcodec":
                        "mp3",

                    "preferredquality":
                        "192"
                }
            ],

            "outtmpl":
                str(
                    DOWNLOAD_DIR /
                    "%(title)s.%(ext)s"
                ),

            "restrictfilenames":
                True,

            "ffmpeg_location":
                FFMPEG_PATH
        }

    # ========================================================
    # TV COMPATIBLE MP4
    # ========================================================

    if mode == "tv":

        return {

            "format": (

                "bestvideo[height<=720]"
                "[vcodec^=avc1]"
                "+bestaudio[acodec^=mp4a]/"
                "best[height<=720]"
            ),

            "merge_output_format":
                "mp4",

            "postprocessors": [

                {
                    "key":
                        "FFmpegVideoConvertor",

                    "preferedformat":
                        "mp4"
                }
            ],

            "postprocessor_args": [

                "-c:v",
                "libx264",

                "-preset",
                "veryfast",

                "-crf",
                "23",

                "-c:a",
                "aac",

                "-b:a",
                "192k",

                "-movflags",
                "+faststart"
            ],

            "outtmpl":
                str(
                    DOWNLOAD_DIR /
                    "%(title)s.%(ext)s"
                ),

            "restrictfilenames":
                True,

            "ffmpeg_location":
                FFMPEG_PATH
        }

    # ========================================================
    # STANDARD MP4
    # ========================================================

    quality_map = {

        "480p": 480,

        "720p": 720,

        "1080p": 1080
    }

    height = quality_map.get(
        quality,
        720
    )

    return {

        "format": (

            f"bestvideo[height<={height}]"
            "+bestaudio/"
            f"best[height<={height}]"
        ),

        "merge_output_format":
            "mp4",

        "outtmpl":
            str(
                DOWNLOAD_DIR /
                "%(title)s.%(ext)s"
            ),

        "restrictfilenames":
            True,

        "ffmpeg_location":
            FFMPEG_PATH
    }


# ============================================================
# PROGRESS HOOK
# ============================================================

def create_progress_hook(job_id):

    def progress_hook(data):

        try:

            if job_id not in jobs:
                return

            status = data.get("status")

            # ------------------------------------------------
            # DOWNLOADING
            # ------------------------------------------------

            if status == "downloading":

                downloaded = data.get(
                    "downloaded_bytes",
                    0
                )

                total = data.get(
                    "total_bytes"
                )

                if not total:

                    total = data.get(
                        "total_bytes_estimate"
                    )

                if total:

                    progress = (
                        downloaded /
                        total
                    ) * 100

                else:

                    progress = 0

                speed = data.get(
                    "speed"
                )

                eta = data.get(
                    "eta"
                )

                speed_text = ""

                if speed:

                    speed_mb = (
                        speed /
                        1024 /
                        1024
                    )

                    speed_text = (
                        f"{speed_mb:.2f} MiB/s"
                    )

                eta_text = ""

                if eta is not None:

                    minutes = (
                        int(eta) // 60
                    )

                    seconds = (
                        int(eta) % 60
                    )

                    eta_text = (
                        f"{minutes}:{seconds:02d}"
                    )

                jobs[job_id].update({

                    "status":
                        "downloading",

                    "progress":
                        round(
                            progress,
                            1
                        ),

                    "message":
                        "Downloading...",

                    "speed":
                        speed_text,

                    "eta":
                        eta_text
                })

            # ------------------------------------------------
            # PROCESSING
            # ------------------------------------------------

            elif status == "finished":

                jobs[job_id].update({

                    "status":
                        "processing",

                    "progress":
                        99,

                    "message":
                        "Processing video..."
                })

        except Exception:

            pass

    return progress_hook


# ============================================================
# FIND DOWNLOADED FILE
# ============================================================

def find_downloaded_file():

    files = []

    for file in DOWNLOAD_DIR.iterdir():

        if not file.is_file():
            continue

        if file.suffix.lower() in [

            ".mp4",
            ".mp3",
            ".m4a",
            ".webm",
            ".mkv",
            ".mov",
            ".avi"

        ]:

            files.append(file)

    if not files:
        return None

    files.sort(

        key=lambda x:
            x.stat().st_mtime,

        reverse=True
    )

    return files[0]


# ============================================================
# VIDEO INFORMATION
# ============================================================

@app.route(
    "/api/info",
    methods=["POST"]
)
def video_info():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        url = (
            data.get("url")
            or ""
        ).strip()

        if not url:

            return jsonify({

                "error":
                    "Please provide a YouTube URL."

            }), 400

        options = (
            get_common_ytdlp_options()
        )

        options.update({

            "skip_download":
                True
        })

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            info = ydl.extract_info(

                url,

                download=False
            )

        duration_seconds = (
            info.get("duration")
        )

        duration_text = "Unknown"

        if duration_seconds:

            duration_seconds = int(
                duration_seconds
            )

            hours = (
                duration_seconds //
                3600
            )

            minutes = (
                duration_seconds %
                3600
            ) // 60

            seconds = (
                duration_seconds %
                60
            )

            if hours:

                duration_text = (

                    f"{hours}:"
                    f"{minutes:02d}:"
                    f"{seconds:02d}"
                )

            else:

                duration_text = (

                    f"{minutes}:"
                    f"{seconds:02d}"
                )

        return jsonify({

            "title":
                info.get(
                    "title",
                    "Unknown title"
                ),

            "uploader":
                info.get(
                    "uploader",
                    "Unknown"
                ),

            "duration":
                duration_text,

            "thumbnail":
                info.get(
                    "thumbnail"
                ),

            "id":
                info.get(
                    "id"
                ),

            "webpage_url":
                info.get(
                    "webpage_url"
                )
        })

    except Exception as error:

        return jsonify({

            "error":
                str(error)

        }), 500


# ============================================================
# START DOWNLOAD
# ============================================================

@app.route(
    "/api/download",
    methods=["POST"]
)
def start_download():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        url = (
            data.get("url")
            or ""
        ).strip()

        mode = (
            data.get("mode")
            or "mp4"
        ).strip()

        quality = (
            data.get("quality")
            or "720p"
        ).strip()

        if not url:

            return jsonify({

                "error":
                    "Please provide a YouTube URL."

            }), 400

        allowed_modes = [

            "mp4",
            "tv",
            "mp3"
        ]

        if mode not in allowed_modes:

            return jsonify({

                "error":
                    "Invalid download mode."

            }), 400

        allowed_quality = [

            "480p",
            "720p",
            "1080p"
        ]

        if quality not in allowed_quality:

            quality = "720p"

        if not FFMPEG_READY:

            return jsonify({

                "error":
                    "FFmpeg is not installed on the server."

            }), 500

        if not DENO_READY:

            return jsonify({

                "error":
                    "Deno is not available on the server."

            }), 500

        job_id = str(
            uuid.uuid4()
        )

        jobs[job_id] = {

            "status":
                "queued",

            "progress":
                0,

            "message":
                "Download queued.",

            "speed":
                "",

            "eta":
                "",

            "filename":
                None,

            "error":
                None
        }

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

            "job_id":
                job_id
        })

    except Exception as error:

        return jsonify({

            "error":
                str(error)

        }), 500


# ============================================================
# DOWNLOAD WORKER
# ============================================================

def download_job(
    job_id,
    url,
    mode,
    quality
):

    try:

        jobs[job_id].update({

            "status":
                "starting",

            "progress":
                0,

            "message":
                "Starting download..."
        })

        progress_hook = (
            create_progress_hook(
                job_id
            )
        )

        options = (
            get_common_ytdlp_options(
                progress_hook
            )
        )

        format_options = (
            get_format_options(
                mode,
                quality
            )
        )

        options.update(
            format_options
        )

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            ydl.download([
                url
            ])

        # ----------------------------------------------------
        # FIND FILE
        # ----------------------------------------------------

        downloaded_file = (
            find_downloaded_file()
        )

        if not downloaded_file:

            raise RuntimeError(

                "Download completed but "
                "the output file could not be found."
            )

        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        jobs[job_id].update({

            "status":
                "complete",

            "progress":
                100,

            "message":
                "Download ready.",

            "filename":
                downloaded_file.name,

            "speed":
                "",

            "eta":
                ""
        })

    except Exception as error:

        error_message = str(
            error
        )

        jobs[job_id].update({

            "status":
                "error",

            "progress":
                0,

            "message":
                error_message,

            "error":
                error_message,

            "filename":
                None
        })


# ============================================================
# JOB STATUS
# ============================================================

@app.route(
    "/api/status/<job_id>"
)
def job_status(job_id):

    job = jobs.get(
        job_id
    )

    if not job:

        return jsonify({

            "status":
                "error",

            "message":
                "Download job not found."

        }), 404

    return jsonify({

        "status":
            job.get(
                "status",
                "starting"
            ),

        "progress":
            job.get(
                "progress",
                0
            ),

        "message":
            job.get(
                "message",
                ""
            ),

        "speed":
            job.get(
                "speed",
                ""
            ),

        "eta":
            job.get(
                "eta",
                ""
            ),

        "filename":
            job.get(
                "filename"
            )
    })


# ============================================================
# DOWNLOAD FILE
# ============================================================

@app.route(
    "/download/<path:filename>"
)
def download_file(filename):

    file_path = (
        DOWNLOAD_DIR /
        filename
    )

    if not file_path.exists():

        return jsonify({

            "error":
                "File not found."

        }), 404

    return send_from_directory(

        DOWNLOAD_DIR,

        filename,

        as_attachment=True
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route(
    "/health"
)
def health():

    return jsonify({

        "status":
            "ok",

        "ffmpeg":
            FFMPEG_READY,

        "ffmpeg_path":
            FFMPEG_PATH,

        "ffprobe":
            bool(
                FFPROBE_PATH
            ),

        "ffprobe_path":
            FFPROBE_PATH,

        "deno":
            DENO_READY,

        "deno_path":
            DENO_PATH,

        "yt_dlp":
            yt_dlp.version.__version__
    })


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def index():

    return render_template(

        "index.html",

        ffmpeg_ready=
            FFMPEG_READY,

        deno_ready=
            DENO_READY
    )


# ============================================================
# CLEAN OLD DOWNLOADS
# ============================================================

def cleanup_old_files():

    import time

    now = time.time()

    max_age = (
        60 * 60
    )

    try:

        for file in (
            DOWNLOAD_DIR.iterdir()
        ):

            if not file.is_file():
                continue

            try:

                age = (
                    now -
                    file.stat().st_mtime
                )

                if age > max_age:

                    file.unlink(
                        missing_ok=True
                    )

            except Exception:

                pass

    except Exception:

        pass


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("TubeSafe Downloader")
    print("=" * 60)

    print(
        "FFmpeg:",
        "READY"
        if FFMPEG_READY
        else "NOT FOUND"
    )

    print(
        "Deno:",
        "READY"
        if DENO_READY
        else "NOT FOUND"
    )

    print(
        "yt-dlp:",
        yt_dlp.version.__version__
    )

    print("=" * 60)
    print()

    app.run(

        host="0.0.0.0",

        port=5000,

        debug=True
    )