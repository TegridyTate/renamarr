import os
import logging
from flask import Flask, request, jsonify
import requests
from qbittorrentapi import Client, LoginFailed

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
SONARR_API_KEY = os.getenv('SONARR_API_KEY')
SONARR_URL = os.getenv('SONARR_URL')
QBITTORRENT_HOST = os.getenv('QBITTORRENT_HOST')
QBITTORRENT_USERNAME = os.getenv('QBITTORRENT_USERNAME')
QBITTORRENT_PASSWORD = os.getenv('QBITTORRENT_PASSWORD')
DOWNLOAD_PATH = os.getenv('DOWNLOAD_PATH')
PORT = os.getenv('PORT', 12345)

def get_qbittorrent_client():
    """Connect to qBittorrent and return the client."""
    client = Client(
        host=QBITTORRENT_HOST,
        username=QBITTORRENT_USERNAME,
        password=QBITTORRENT_PASSWORD
    )
    try:
        client.auth_log_in()
    except LoginFailed as e:
        logger.error("Failed to log in to qBittorrent: %s", e)
        raise
    return client

@app.route('/sonarr_webhook', methods=['POST'])
def sonarr_webhook():
    data = request.json
    logger.info("Received webhook data: %s", data)

    # Check if event is Grab
    if data.get('eventType') != 'Grab':
        logger.info("Ignoring non-Grab event: %s", data.get('eventType'))
        return jsonify({'status': 'ignored', 'reason': 'not a Grab event'}), 200

    # Extract series ID and download ID (torrent hash)
    try:
        series_id = data['series']['id']
        download_id = data['downloadId'].lower()
    except KeyError as e:
        logger.error("Missing key in webhook data: %s", e)
        return jsonify({'status': 'error', 'message': f'Missing key: {e}'}), 400

    # Fetch rename data from Sonarr
    headers = {'X-Api-Key': SONARR_API_KEY}
    rename_url = f"{SONARR_URL}/api/v3/rename"
    params = {'seriesId': series_id}
    try:
        response = requests.get(rename_url, headers=headers, params=params)
        response.raise_for_status()
        rename_data = response.json()
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch rename data from Sonarr: %s", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

    logger.info("Rename data: %s", rename_data)

    # Process each rename entry
    qbt_client = None
    try:
        qbt_client = get_qbittorrent_client()
        torrent = qbt_client.torrents_info(torrent_hashes=download_id)
        if not torrent:
            logger.error("Torrent with hash %s not found in qBittorrent", download_id)
            return jsonify({'status': 'error', 'message': 'Torrent not found'}), 404
        torrent = torrent[0]

        # Get the list of files in the torrent
        torrent_files = torrent.files

        for entry in rename_data:
            existing_path = entry.get('existingPath')
            new_path = entry.get('newPath')

            if not existing_path or not new_path:
                logger.warning("Skipping rename entry with missing paths: %s", entry)
                continue

            # Compute relative path within the download directory
            if not existing_path.startswith(DOWNLOAD_PATH):
                logger.warning("Existing path %s is not in download path %s", existing_path, DOWNLOAD_PATH)
                continue
            relative_path = os.path.relpath(existing_path, DOWNLOAD_PATH)

            # Get new filename from new_path
            new_filename = os.path.basename(new_path)
            new_relative_path = os.path.join(os.path.dirname(relative_path), new_filename)

            # Find the file in the torrent that matches relative_path
            for file in torrent_files:
                if file.name == relative_path:
                    logger.info("Renaming %s to %s", file.name, new_relative_path)
                    try:
                        qbt_client.torrents_rename_file(
                            torrent_hash=download_id,
                            file_id=file.id,
                            new_file_name=new_relative_path
                        )
                        logger.info("Successfully renamed file")
                    except Exception as e:
                        logger.error("Failed to rename file %s: %s", file.name, e)
                    break  # Assuming one match per file
            else:
                logger.warning("No file found in torrent matching %s", relative_path)

        return jsonify({'status': 'success'}), 200

    except Exception as e:
        logger.error("An error occurred: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if qbt_client:
            qbt_client.auth_log_out()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)