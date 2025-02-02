import os
import time
import requests
import qbittorrentapi
from fastapi import FastAPI, Request

SONARR_API_URL = os.getenv('SONARR_API_URL')
SONARR_API_KEY = os.getenv('SONARR_API_KEY')
QB_HOST = os.getenv('QB_HOST')
QB_USERNAME = os.getenv('QB_USERNAME')
QB_PASSWORD = os.getenv('QB_PASSWORD')

SONARR_API_URL = SONARR_API_URL + "/api/v3"

app = FastAPI()

# Run with Uvicorn
# uvicorn renamarr:app --host 0.0.0.0 --port 8000

@app.post("/sonarr-webhook")
async def sonarr_webhook(request: Request):
    payload = await request.json()
    download_id = payload.get("downloadId")
    series_path = payload.get("series", {}).get("path")
    series_id = payload.get("episodes", [{}])[0].get("seriesId")
    season_number = str(payload.get("episodes", [{}])[0].get("seasonNumber", "1")).zfill(2)
    season_path = os.path.join(series_path, f"Season {season_number}")
    os.makedirs(season_path, exist_ok=True)
    time.sleep(30) # This is bad... Should check for status "downloading" from Sonarr queue, just haven't yet
    qbt_client = qbittorrentapi.Client(host=QB_HOST, username=QB_USERNAME, password=QB_PASSWORD)
    qbt_client.auth_log_in()
    torrent_info = qbt_client.torrents_info(torrent_hashes=download_id)
    files = qbt_client.torrents_files(torrent_hash=download_id)
    file_name, full_path = None, None
    for file in files:
        if file.name.endswith('.mkv'): # Also bad
            full_path = os.path.join(torrent_info[0].save_path, file.name)
            file_name = os.path.basename(file.name)
            break   
    hardlink_path = os.path.join(season_path, file_name)
    os.link(full_path, hardlink_path)
    headers = {"X-Api-Key": SONARR_API_KEY, "Content-Type": "application/json"}
    refresh_resp = requests.post(f"{SONARR_API_URL}/command", json={"name": "refreshSeries", "seriesId": series_id}, headers=headers)
    time.sleep(5) # Seems to kill it-self if I don't wait a few seconds
    rename_resp = requests.get(f"{SONARR_API_URL}/rename?seriesId={series_id}", headers=headers)
    rename_resp.raise_for_status()
    file_id = rename_resp.json()[0].get("episodeFileId")
    rename_command = requests.post(f"{SONARR_API_URL}/command", json={"name": "renameFiles", "seriesId": series_id, "files": [file_id]}, headers=headers)
    rename_command.raise_for_status()

    return {"message": "Webhook processed", "status": "success"}
