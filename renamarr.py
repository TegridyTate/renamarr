import os
import time
import logging
import requests
import qbittorrentapi
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import subprocess

# ------------------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)
logging.basicConfig(
    level=numeric_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Environment Variables and Configurations
# ------------------------------------------------------------------------------
SONARR_API_URL = os.getenv('SONARR_API_URL')
SONARR_API_KEY = os.getenv('SONARR_API_KEY')
QB_HOST = os.getenv('QB_HOST')
QB_USERNAME = os.getenv('QB_USERNAME')
QB_PASSWORD = os.getenv('QB_PASSWORD')
LINK_FS = os.getenv('LINK_FS') # FS where links will occurr needs to be mounted

if not (SONARR_API_URL and SONARR_API_KEY and QB_HOST and QB_USERNAME and QB_PASSWORD):
    logger.error("One or more required environment variables are missing.")
    logger.error(f"SONARR_API_URL: {SONARR_API_URL}")
    logger.error(f"SONARR_API_KEY: {SONARR_API_KEY}")
    logger.error(f"QB_HOST: {QB_HOST}")
    logger.error(f"QB_USERNAME: {QB_USERNAME}")
    logger.error(f"QB_PASSWORD: {QB_PASSWORD}")
    raise ValueError("Missing required environment variables.")

# Ensure proper URL formatting for Sonarr API
SONARR_API_URL = SONARR_API_URL.rstrip('/') + "/api/v3"

# Allowed video file extensions
ALLOWED_VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv'}

# Check if HardLinking allowed
# TODO: Find better check, there might be a package for this.
test_file = LINK_FS + '/test.txt'
test_link = LINK_FS + '/test2.txt'

try:
    with open(test_file, 'w') as f:
        f.write('1')
    logging.debug(f"File created: {test_file}")
except Exception as e:
    logging.error(f"Failed to create {test_file}: {e}")
    raise

try:
    os.link(test_file, test_link)
    logging.debug(f"Hard link created: {test_file} -> {test_link}")
except Exception as e:
    logging.error(f"Failed to create hard link: {e}")
    os.remove(test_file)
    raise

try:
    test_stat = os.stat(test_file)
    test2_stat = os.stat(test_link)

    if test_stat.st_ino == test2_stat.st_ino:
        logging.debug("Hardlink test passed")
    else:
        raise ValueError("Inodes do not match, hardlinking failed")
except Exception as e:
    logging.error(f"Hardlink check failed: {e}")
    os.remove(test_file)
    os.remove(test_link)
    raise

finally:
    try:
        if os.path.exists(test_file):
            os.remove(test_file)
        if os.path.exists(test_link):
            os.remove(test_link)
        logging.debug("Cleanup successful")
    except Exception as e:
        logging.error(f"Failed to clean up files: {e}")

# ------------------------------------------------------------------------------
# FastAPI Application Initialization
# ------------------------------------------------------------------------------
app = FastAPI()

# ------------------------------------------------------------------------------
# Webhook Endpoint for Sonarr
# ------------------------------------------------------------------------------
@app.post("/sonarr-webhook")
async def sonarr_webhook(request: Request):
    """
    Process incoming Sonarr webhook events and handle post-download operations.

    This endpoint performs the following tasks:
      1. Validates and parses the incoming JSON payload.
      2. Exits early if the event is a test (eventType == 'test').
      3. Waits for the torrent download to complete.
         TODO: Replace the static sleep with a dynamic check for torrent status.
      4. Connects to qBittorrent and retrieves torrent and file details.
      5. Searches for a file with one of the allowed video extensions.
      6. Creates a hard link of the file in the appropriate season directory.
      7. Triggers Sonarr to refresh the series and rename files.

    Returns:
      JSONResponse indicating the status of the webhook processing.
    """
    try:
        payload = await request.json()
        logger.debug(f"Received payload: {payload}")

        # ------------------------------------------------------------------------------
        # Early Exit for Test Events
        # ------------------------------------------------------------------------------
        event_type = payload.get("eventType", "").lower()
        if event_type == "test":
            logger.info("Received test event from Sonarr. Exiting early.")
            return {"message": "Test event received", "status": "success"}

        # ------------------------------------------------------------------------------
        # Validate Essential Payload Data
        # ------------------------------------------------------------------------------
        download_id = payload.get("downloadId")
        series_info = payload.get("series", {})
        series_path = series_info.get("path")
        episodes = payload.get("episodes", [])

        if not download_id or not series_path or not episodes:
            logger.error("Missing required data in the payload.")
            raise HTTPException(status_code=400, detail="Missing required data in payload.")

        series_id = episodes[0].get("seriesId")
        season_number = str(episodes[0].get("seasonNumber", "1")).zfill(2)
        season_path = os.path.join(series_path, f"Season {season_number}")

        # Ensure that the season directory exists
        os.makedirs(season_path, exist_ok=True)
        logger.info(f"Season directory ensured at: {season_path}")

        # ------------------------------------------------------------------------------
        # Wait for Torrent Completion
        # ------------------------------------------------------------------------------
        # TODO: Replace the static sleep with a dynamic check for torrent download completion.
        logger.debug("Waiting 30 seconds to ensure download completion.")
        time.sleep(30)

        # ------------------------------------------------------------------------------
        # Connect to qBittorrent and Retrieve Torrent Data
        # ------------------------------------------------------------------------------
        try:
            qbt_client = qbittorrentapi.Client(
                host=QB_HOST,
                username=QB_USERNAME,
                password=QB_PASSWORD
            )
            qbt_client.auth_log_in()
            logger.info("Authenticated with qBittorrent successfully.")
        except Exception as e:
            logger.error(f"Failed to authenticate with qBittorrent: {e}")
            raise HTTPException(status_code=500, detail="qBittorrent authentication failed.")

        try:
            torrent_info = qbt_client.torrents_info(torrent_hashes=download_id)
            if not torrent_info:
                logger.error("Torrent info not found for given downloadId.")
                raise HTTPException(status_code=404, detail="Torrent info not found.")
            logger.debug(f"Torrent info: {torrent_info}")
        except Exception as e:
            logger.error(f"Error retrieving torrent info: {e}")
            raise HTTPException(status_code=500, detail="Error retrieving torrent info.")

        try:
            files = qbt_client.torrents_files(torrent_hash=download_id)
            logger.debug(f"Retrieved files: {files}")
        except Exception as e:
            logger.error(f"Error retrieving torrent files: {e}")
            raise HTTPException(status_code=500, detail="Error retrieving torrent files.")

        # ------------------------------------------------------------------------------
        # Search for a Video File with an Allowed Extension
        # ------------------------------------------------------------------------------
        file_name, full_path = None, None
        for file in files:
            _, ext = os.path.splitext(file.name.lower())
            if ext in ALLOWED_VIDEO_EXTENSIONS:
                full_path = os.path.join(torrent_info[0].save_path, file.name)
                file_name = os.path.basename(file.name)
                # I spent like an hour debugging the hardlink
                # Just to realize it was linking the DAMN SAMPLE
                if "Sample" in full_path:
                    continue
                logger.info(f"Selected file: {file_name} with extension {ext}")
                break

        if not full_path or not file_name:
            logger.error("No video file found with allowed extensions.")
            raise HTTPException(status_code=404, detail="No video file found with allowed extensions.")

        # ------------------------------------------------------------------------------
        # Create Hard Link in the Season Directory
        # ------------------------------------------------------------------------------
        hardlink_path = os.path.join(season_path, file_name)
        try:
            logger.debug(f"File Path: {full_path}")
            logger.debug(f"Hardlink Path: {hardlink_path}")
            # os.link(full_path, hardlink_path) # Silently failing in Docker
            # subprocess.run(['ln', full_path, hardlink_path], check=True) # ln also silently failing
            os.system(f'ln "{full_path}" "{hardlink_path}"')
            logger.info(f"Created hard link from {full_path} to {hardlink_path}")
        except subprocess.CalledProcessError as e:
            logging.error(f"Error creating hardlink with ln: {e}")
            raise HTTPException(status_code=500, detail="Error creating hard link using ln.")
        except Exception as e:
            logger.error(f"Error creating hard link: {e}")
            raise HTTPException(status_code=500, detail="Error creating hard link.")

        # ------------------------------------------------------------------------------
        # Trigger Sonarr Commands: Refresh and Rename
        # ------------------------------------------------------------------------------
        headers = {"X-Api-Key": SONARR_API_KEY, "Content-Type": "application/json"}

        try:
            logger.debug("Sending refreshSeries command to Sonarr.")
            refresh_resp = requests.post(
                f"{SONARR_API_URL}/command",
                json={"name": "refreshSeries", "seriesId": series_id},
                headers=headers
            )
            refresh_resp.raise_for_status()
            logger.info("refreshSeries command sent successfully.")
        except Exception as e:
            logger.error(f"Error sending refreshSeries command: {e}")
            raise HTTPException(status_code=500, detail="Error sending refreshSeries command to Sonarr.")

        # Wait for Sonarr to process the refresh command
        logger.debug("Waiting 5 seconds for Sonarr to process the refresh command.")
        time.sleep(5)

        try:
            logger.debug("Retrieving rename information from Sonarr.")
            rename_resp = requests.get(
                f"{SONARR_API_URL}/rename?seriesId={series_id}",
                headers=headers
            )
            rename_resp.raise_for_status()
            rename_info = rename_resp.json()
            if not rename_info or not isinstance(rename_info, list):
                logger.error("Invalid rename response received from Sonarr.")
                raise HTTPException(status_code=500, detail="Invalid rename response from Sonarr.")
            file_id = rename_info[0].get("episodeFileId")
            if not file_id:
                logger.error("Episode file ID not found in rename response.")
                raise HTTPException(status_code=500, detail="Episode file ID not found.")
            logger.debug(f"Episode file ID retrieved: {file_id}")
        except Exception as e:
            logger.error(f"Error retrieving rename information: {e}")
            raise HTTPException(status_code=500, detail="Error retrieving rename information from Sonarr.")

        try:
            logger.debug("Sending renameFiles command to Sonarr.")
            rename_command = requests.post(
                f"{SONARR_API_URL}/command",
                json={"name": "renameFiles", "seriesId": series_id, "files": [file_id]},
                headers=headers
            )
            rename_command.raise_for_status()
            logger.info("renameFiles command sent successfully.")
        except Exception as e:
            logger.error(f"Error sending renameFiles command: {e}")
            raise HTTPException(status_code=500, detail="Error sending renameFiles command to Sonarr.")

        return {"message": "Webhook processed", "status": "success"}

    except Exception as e:
        logger.exception("Unexpected error during webhook processing.")
        return JSONResponse(status_code=500, content={"message": "An error occurred", "error": str(e)})

# ------------------------------------------------------------------------------
# Additional Endpoints / Docker Enhancements
# ------------------------------------------------------------------------------
# TODO: Add health check endpoints (e.g., /health, /readiness) for Docker orchestration.
# TODO: Implement graceful shutdown handlers to properly manage container termination signals when running in Docker.