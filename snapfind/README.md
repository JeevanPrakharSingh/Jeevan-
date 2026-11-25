# SnapFind AI

The organizer creates an event, selects a folder or multiple image files, and copies the event link. Guests open the link, agree to face matching, then follow the front-camera prompts to blink and turn their head. Guests can download individual original photos, a ZIP of selected photos, or a ZIP of every returned match.

## Run locally

Use Python 3.10–3.12 with pip and run these commands from this folder.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python setup_models.py
export ADMIN_PASSWORD='replace-with-your-own-long-password'
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python app.py
```

Open http://127.0.0.1:5000 and select **Organizer dashboard**. For a local test of the guest page, open the generated guest link on the same computer. Camera access works on localhost or over HTTPS in supported browsers.

## Host for your guests

Run the included Dockerfile on a Python-capable container host with HTTPS, a persistent writable volume mounted at `/data`, and enough free space for the originals and concurrent ZIP downloads. Set `ADMIN_PASSWORD`, a persistent random `SECRET_KEY`, and `SNAPFIND_HTTPS=1` as hosting environment variables. Route the public HTTPS URL to the container's port and configure the host's request limit to allow at least 51 MiB. Open the organizer dashboard on the public URL so the copied link works on guests' devices. Keep this application on a single replica with its persistent volume.

For example, on a host with a TLS proxy already configured:

```bash
docker build -t snapfind .
docker volume create snapfind-data
docker run -d --name snapfind -p 127.0.0.1:8080:8080 \
  -e ADMIN_PASSWORD -e SECRET_KEY -e SNAPFIND_HTTPS=1 \
  -v snapfind-data:/data snapfind
```

## Uploads and access

- Use JPG, MPO, PNG, WebP, HEIC, AVIF, TIFF, BMP or GIF originals up to 50 MiB each. For MPO files, SnapFind indexes and distributes the camera’s primary image as a single JPEG, up to 4096 pixels on the longest side. An album can total 5 GB or more if the host has capacity. Keep the upload tab open. For interruptions, choose the folder again in the same event. Previously uploaded files in this version are skipped by content hash.
- Photos are indexed as they arrive, so finish your upload before sharing the event link. The files are stored on the host and never made available as a public folder.
- The guest page only offers live camera capture. A short-lived, one-use server challenge requires a random blink count, a head turn and a consistent face. Showing a still photo on another phone will fail the blink check. This active check cannot guarantee rejection of every prerecorded video, manipulated image sequence or virtual camera; use independently verified identity and a production-grade presentation-attack detector for sensitive galleries.
- Matching is approximate. Poor lighting, small faces and similar-looking people can affect results. Download access belongs to the browser session that completed matching and expires after one hour. Previously shared result URLs from the original project are invalidated by the session requirement.
- Camera frames are processed for the search and discarded. Event photos and their face embeddings remain in the data volume. Get permission from the people whose photos you index, and use a dedicated data volume per installation when separate retention is required. To erase all event data, stop the app and remove that installation's data volume or directory.
- YuNet and SFace download from OpenCV Zoo and the blink model downloads from Google MediaPipe during setup or Docker build. The model binaries are separate from this source package.

## Upgrade an existing Mac installation

Stop SnapFind with Control-C. Copy the updated application files into the existing project, preserving the project's `data` and `models` folders. From that project directory, use a fresh environment to avoid mixing old and new OpenCV packages:

```bash
python3 -m venv .venv-live
source .venv-live/bin/activate
python -m pip install -r requirements.txt
python setup_models.py
python app.py
```

Use the same organizer password and the same `SNAPFIND_DATA` path if you previously set one. The captured blink frames are processed in memory and discarded. Old download sessions need to capture a fresh selfie after the upgrade.
