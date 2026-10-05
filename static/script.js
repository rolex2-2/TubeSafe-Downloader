// ============================================================
// TubeSafe Downloader - Frontend JavaScript
// ============================================================


// ============================================================
// ELEMENTS
// ============================================================

const urlInput = document.getElementById("url");
const infoButton = document.getElementById("infoButton");

const videoInfo = document.getElementById("videoInfo");
const thumbnail = document.getElementById("thumbnail");
const titleElement = document.getElementById("title");
const channelElement = document.getElementById("channel");
const durationElement = document.getElementById("duration");

const formatSelect = document.getElementById("format");
const downloadButton = document.getElementById("downloadButton");

const progressBox = document.getElementById("progressBox");
const statusElement = document.getElementById("status");
const percentElement = document.getElementById("percent");
const progressBar = document.getElementById("progressBar");
const speedElement = document.getElementById("speed");

const resultBox = document.getElementById("result");
const errorBox = document.getElementById("error");


// ============================================================
// VARIABLES
// ============================================================

let currentJobId = null;
let statusTimer = null;


// ============================================================
// HELPER FUNCTIONS
// ============================================================

function show(element) {
    element.classList.remove("hidden");
}


function hide(element) {
    element.classList.add("hidden");
}


function clearMessages() {
    hide(resultBox);
    hide(errorBox);

    resultBox.innerHTML = "";
    errorBox.innerHTML = "";
}


function showError(message) {
    hide(resultBox);

    errorBox.textContent = message;

    show(errorBox);
}


function showResult(message) {
    hide(errorBox);

    resultBox.innerHTML = message;

    show(resultBox);
}


function setProgress(value, statusText, speedText = "") {

    value = Number(value) || 0;

    value = Math.max(0, Math.min(100, value));

    progressBar.style.width = `${value}%`;

    percentElement.textContent = `${Math.round(value)}%`;

    if (statusText) {
        statusElement.textContent = statusText;
    }

    speedElement.textContent = speedText || "";
}


// ============================================================
// GET VIDEO INFORMATION
// ============================================================

async function getInfo() {

    clearMessages();

    const url = urlInput.value.trim();

    if (!url) {
        showError("Please paste a YouTube URL.");
        urlInput.focus();
        return;
    }

    infoButton.disabled = true;

    infoButton.textContent = "Loading...";

    hide(videoInfo);

    try {

        const response = await fetch("/api/info", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                url: url
            })

        });


        const data = await response.json();


        if (!response.ok) {

            throw new Error(
                data.error ||
                "Unable to get video information."
            );

        }


        // --------------------------------------------
        // DISPLAY VIDEO INFORMATION
        // --------------------------------------------

        titleElement.textContent =
            data.title || "Unknown title";


        channelElement.textContent =
            `Channel: ${data.uploader || "Unknown"}`;


        durationElement.textContent =
            `Duration: ${data.duration || "Unknown"}`;


        if (data.thumbnail) {

            thumbnail.src = data.thumbnail;

            thumbnail.alt =
                data.title || "Video thumbnail";

        } else {

            thumbnail.removeAttribute("src");

        }


        show(videoInfo);


        showResult(
            "Video information loaded. Choose a format and click Download."
        );

    }

    catch (error) {

        showError(
            error.message ||
            "Unable to get video information."
        );

    }

    finally {

        infoButton.disabled = false;

        infoButton.textContent = "Get Video";

    }
}


// ============================================================
// START DOWNLOAD
// ============================================================

async function startDownload() {

    clearMessages();

    const url = urlInput.value.trim();

    const selectedFormat = formatSelect.value;


    if (!url) {

        showError(
            "Please paste a YouTube URL first."
        );

        urlInput.focus();

        return;
    }


    // --------------------------------------------
    // DETERMINE MODE
    // --------------------------------------------

    let mode = "mp4";

    let quality = "720p";


    if (selectedFormat === "mp3") {

        mode = "mp3";

    }

    else if (selectedFormat === "tv") {

        mode = "tv";

    }

    else {

        mode = "mp4";

        quality = selectedFormat;

    }


    // --------------------------------------------
    // RESET PROGRESS
    // --------------------------------------------

    show(progressBox);

    setProgress(
        0,
        "Starting download...",
        ""
    );


    downloadButton.disabled = true;

    infoButton.disabled = true;


    try {

        const response = await fetch(
            "/api/download",
            {

                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({

                    url: url,

                    mode: mode,

                    quality: quality

                })

            }
        );


        const data = await response.json();


        if (!response.ok) {

            throw new Error(
                data.error ||
                "Unable to start download."
            );

        }


        if (!data.job_id) {

            throw new Error(
                "The server did not return a download job."
            );

        }


        currentJobId = data.job_id;


        // Start checking progress

        pollJobStatus(currentJobId);

    }

    catch (error) {

        showError(
            error.message ||
            "Unable to start download."
        );


        downloadButton.disabled = false;

        infoButton.disabled = false;

    }
}


// ============================================================
// POLL DOWNLOAD STATUS
// ============================================================

async function pollJobStatus(jobId) {

    try {

        const response = await fetch(
            `/api/status/${encodeURIComponent(jobId)}`
        );


        const data = await response.json();


        if (!response.ok) {

            throw new Error(
                data.message ||
                "Unable to check download status."
            );

        }


        // --------------------------------------------
        // PROGRESS
        // --------------------------------------------

        const progress =
            Number(data.progress) || 0;


        const status =
            data.status || "starting";


        let statusText =
            data.message || "Processing...";


        let speedText = "";


        if (data.speed) {

            speedText =
                `Speed: ${data.speed}`;

        }


        if (data.eta) {

            if (speedText) {

                speedText +=
                    ` • ETA: ${data.eta}`;

            } else {

                speedText =
                    `ETA: ${data.eta}`;

            }

        }


        setProgress(
            progress,
            statusText,
            speedText
        );


        // --------------------------------------------
        // QUEUED
        // --------------------------------------------

        if (status === "queued") {

            scheduleNextPoll(jobId);

            return;

        }


        // --------------------------------------------
        // STARTING
        // --------------------------------------------

        if (status === "starting") {

            scheduleNextPoll(jobId);

            return;

        }


        // --------------------------------------------
        // DOWNLOADING
        // --------------------------------------------

        if (status === "downloading") {

            scheduleNextPoll(jobId);

            return;

        }


        // --------------------------------------------
        // PROCESSING
        // --------------------------------------------

        if (status === "processing") {

            setProgress(
                Math.max(progress, 99),
                statusText,
                speedText
            );

            scheduleNextPoll(jobId);

            return;

        }


        // --------------------------------------------
        // COMPLETE
        // --------------------------------------------

        if (status === "complete") {

            setProgress(
                100,
                "Download ready.",
                ""
            );


            if (data.filename) {

                const downloadUrl =
                    `/download/${encodeURIComponent(
                        data.filename
                    )}`;


                showResult(`
                    <strong>Download complete!</strong>
                    <br><br>
                    <a
                        href="${downloadUrl}"
                        class="download-link"
                        download
                    >
                        ⬇ Download File
                    </a>
                `);

            }

            else {

                showError(
                    "The server completed the download but no file was found."
                );

            }


            downloadButton.disabled = false;

            infoButton.disabled = false;

            currentJobId = null;

            return;

        }


        // --------------------------------------------
        // ERROR
        // --------------------------------------------

        if (status === "error") {

            throw new Error(
                data.message ||
                "Download failed."
            );

        }


        // --------------------------------------------
        // UNKNOWN STATUS
        // --------------------------------------------

        scheduleNextPoll(jobId);

    }

    catch (error) {

        showError(
            error.message ||
            "An error occurred during the download."
        );


        downloadButton.disabled = false;

        infoButton.disabled = false;

        currentJobId = null;

    }
}


// ============================================================
// NEXT POLL
// ============================================================

function scheduleNextPoll(jobId) {

    clearTimeout(statusTimer);


    statusTimer = setTimeout(
        () => {

            if (currentJobId === jobId) {

                pollJobStatus(jobId);

            }

        },
        1000
    );
}


// ============================================================
// URL ENTER KEY
// ============================================================

urlInput.addEventListener(
    "keydown",
    function(event) {

        if (event.key === "Enter") {

            event.preventDefault();

            getInfo();

        }

    }
);


// ============================================================
// FORMAT CHANGE
// ============================================================

formatSelect.addEventListener(
    "change",
    function() {

        clearMessages();

    }
);


// ============================================================
// CLEANUP
// ============================================================

window.addEventListener(
    "beforeunload",
    function() {

        clearTimeout(statusTimer);

    }
);