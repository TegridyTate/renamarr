# renamarr
WIP: Goal is to rename in-progress (downloading) torrents in qbittorrent depending on Sonarr &amp; Radarr's suggested renaming. 

## Instructions (docker-compose)
Add the following to your docker-compose.yml
```
    renamarr:
      container_name: renamarr
      build: ./renamarr
      environment:
        - SONARR_API_KEY=<SONARR_API_KEY>
        - SONARR_URL=<SONARR_HOST>
        - QBITTORRENT_HOST=<QBITTORRENT_HOST>
        - QBITTORRENT_USERNAME=<QBITTORRENT_USERNAME>
        - QBITTORRENT_PASSWORD=<QBITTORRENT_PASSWORD>
        - DOWNLOAD_PATH=/qbittorrent
        - PORT=12345
      ports:
        - 12345:12345
      networks:
        - bridge
      volumes:
        - ./renamarr:/app
```

Run `docker-compose build renamarr`

In Sonarr (Radarr integration not yet added), go to `Settings` > `Connect` > `+` > `Webhook`. Only leave `On Grab` checked, set `Webhook URL` to `http:<YOUR_LOCAL_IP>:12345/sonarr_webhook`. Press `Test` to see if it works.

To fully test flow, add a show and send a download to qbittorrent.