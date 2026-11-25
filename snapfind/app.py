import hashlib
import io
import os
import secrets
import sqlite3
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, abort, jsonify, render_template, request, send_file, session
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from liveness import (FRAME_COUNT, FRAME_INTERVAL_MS, LIVENESS_ERROR, MAX_FRAME_BYTES,
                      MODEL as LIVENESS_MODEL, blink_states, confirmed_blinks)

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SNAPFIND_DATA", BASE / "data")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
MODELS = [BASE / "models" / name for name in (
    "face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx",
)]
LIMIT = 50 * 1024 * 1024
PHOTO_FORMATS = {
    "JPEG": ("image/jpeg", (".jpg", ".jpeg", ".jfif")),
    "PNG": ("image/png", (".png",)),
    "WEBP": ("image/webp", (".webp",)),
    "HEIF": ("image/heif", (".heic", ".heif")),
    "AVIF": ("image/avif", (".avif",)),
    "TIFF": ("image/tiff", (".tif", ".tiff")),
    "BMP": ("image/bmp", (".bmp",)),
    "GIF": ("image/gif", (".gif",)),
}
register_heif_opener(thumbnails=False)
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=LIMIT + 1024 * 1024, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("SNAPFIND_HTTPS") == "1")
engine = None
engine_lock = threading.Lock()


@contextmanager
def db():
    conn = sqlite3.connect(DATA / "index.db", timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


with db() as conn:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,name TEXT);
        CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY,event TEXT,name TEXT);
        CREATE TABLE IF NOT EXISTS faces(photo TEXT,embedding BLOB);
        CREATE TABLE IF NOT EXISTS tickets(token TEXT PRIMARY KEY,event TEXT,expires INTEGER);
        CREATE TABLE IF NOT EXISTS grants(token TEXT,photo TEXT);
        CREATE TABLE IF NOT EXISTS uploads(event TEXT,digest TEXT,photo TEXT,PRIMARY KEY(event,digest));
        CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY,event TEXT,guest TEXT,direction INTEGER,expires INTEGER);
        CREATE TABLE IF NOT EXISTS attempts(bucket TEXT PRIMARY KEY,count INTEGER,expires INTEGER);
        CREATE INDEX IF NOT EXISTS photo_event ON photos(event);
        CREATE INDEX IF NOT EXISTS face_photo ON faces(photo);
        CREATE INDEX IF NOT EXISTS grant_token ON grants(token);
    """)


def limited(bucket, maximum, seconds):
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM attempts WHERE expires <= ?", (now,))
        row = conn.execute(
            "INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1 RETURNING count",
            (bucket, now + seconds),
        ).fetchone()
    return row["count"] > maximum


@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify(error=error.description), error.code


@app.after_request
def private_response(response):
    response.headers.update({
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(self), microphone=()",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    })
    return response


def extract(stream):
    with Image.open(stream) as original:
        if original.format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("Unsupported camera frame")
        return index_image(original)


def index_image(original):
    global engine
    original.thumbnail((2560, 2560))
    ImageOps.exif_transpose(original, in_place=True)
    pixels = cv2.cvtColor(np.asarray(original.convert("RGB")), cv2.COLOR_RGB2BGR)
    with engine_lock:
        if engine is None:
            detector = cv2.FaceDetectorYN.create(str(MODELS[0]), "", (320, 320), 0.9, 0.3, 5000)
            recognizer = cv2.FaceRecognizerSF.create(str(MODELS[1]), "")
            engine = detector, recognizer
        detector, recognizer = engine
        detector.setInputSize((pixels.shape[1], pixels.shape[0]))
        _, faces = detector.detect(pixels)
        result = []
        if faces is not None:
            for face in faces:
                if min(face[2:4]) < 30:
                    continue
                vector = recognizer.feature(recognizer.alignCrop(pixels, face)).flatten().astype("float32")
                vector /= max(float(np.linalg.norm(vector)), 1e-8)
                # Nose position relative to the two eyes serves as a coarse head-turn signal.
                eyes = sorted((float(face[4]), float(face[6])))
                pose = (float(face[8]) - sum(eyes) / 2) / max(eyes[1] - eyes[0], 1)
                result.append((vector, pose))
        return result


def ready():
    if not all(path.is_file() for path in MODELS):
        abort(503, "Face models are missing. Run python setup_models.py on the server.")


def admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            abort(403, "Sign in as organizer first.")
        return view(*args, **kwargs)
    return wrapped


@app.get("/")
def home():
    return render_template("index.html", event=request.args.get("event", ""), admin=session.get("admin", False))


@app.post("/login")
def login():
    password = os.environ.get("ADMIN_PASSWORD")
    supplied = (request.get_json(silent=True) or {}).get("password")
    if not password:
        abort(503, "Set ADMIN_PASSWORD on the server to enable organizer sign-in.")
    if limited("login:" + (request.remote_addr or ""), 10, 300):
        abort(429, "Too many sign-in attempts. Try again in five minutes.")
    if not isinstance(supplied, str) or not secrets.compare_digest(password.encode(), supplied.encode()):
        abort(403, "Incorrect organizer password.")
    session["admin"] = True
    return jsonify(ok=True)


@app.get("/events")
@admin
def list_events():
    with db() as conn:
        rows = conn.execute("""SELECT e.id,e.name,COUNT(p.id) AS total FROM events e
                               LEFT JOIN photos p ON p.event=e.id GROUP BY e.id ORDER BY e.rowid DESC""")
        return jsonify(events=[dict(row) for row in rows])


@app.post("/events")
@admin
def create_event():
    value = (request.get_json(silent=True) or {}).get("name")
    name = value.strip()[:120] if isinstance(value, str) else ""
    if not name:
        abort(400, "Enter an event name.")
    eid = secrets.token_urlsafe(18)
    with db() as conn:
        conn.execute("INSERT INTO events VALUES (?,?)", (eid, name))
    return jsonify(id=eid)


def event_details(eid):
    with db() as conn:
        row = conn.execute("""SELECT e.name,COUNT(p.id) AS total FROM events e
                              LEFT JOIN photos p ON p.event=e.id WHERE e.id=? GROUP BY e.id""",
                           (eid,)).fetchone()
    if row is None:
        abort(404, "Event link could not be found.")
    return row


@app.get("/events/<eid>")
def event(eid):
    row = event_details(eid)
    return jsonify(name=row["name"], ready=row["total"] > 0)


@app.post("/events/<eid>/photos")
@admin
def upload(eid):
    ready()
    event_details(eid)
    photo = request.files.get("photo")
    if not photo:
        abort(400, "Choose a photo.")
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: photo.stream.read(1024 * 1024), b""):
        size += len(chunk)
        if size > LIMIT:
            abort(413, "Each photo must be 50 MiB or smaller.")
        digest.update(chunk)
    fingerprint = digest.hexdigest()
    photo.stream.seek(0)
    with db() as conn:
        if conn.execute("SELECT 1 FROM uploads WHERE event=? AND digest=?", (eid, fingerprint)).fetchone():
            return jsonify(duplicate=True)
    pid = secrets.token_hex(16)
    name = (secure_filename(photo.filename) or "photo.jpg")[-180:]
    path = DATA / pid
    try:
        with Image.open(photo.stream) as original:
            if original.format not in PHOTO_FORMATS:
                abort(415, f"{original.format} images are unsupported. Use JPG, PNG, WebP, HEIC, AVIF, TIFF, BMP or GIF.")
            _, extensions = PHOTO_FORMATS[original.format]
            suffix = Path(name).suffix.lower()
            if suffix not in extensions:
                name = (Path(name).stem or "photo") + extensions[0]
            vectors = index_image(original)
        photo.stream.seek(0)
        photo.save(path)
        with db() as conn:
            inserted = conn.execute("INSERT OR IGNORE INTO uploads VALUES (?,?,?)", (eid, fingerprint, pid))
            if not inserted.rowcount:
                path.unlink()
                return jsonify(duplicate=True)
            conn.execute("INSERT INTO photos VALUES (?,?,?)", (pid, eid, name))
            conn.executemany("INSERT INTO faces VALUES (?,?)", [(pid, v.tobytes()) for v, _ in vectors])
    except (OSError, ValueError, Image.DecompressionBombError):
        path.unlink(missing_ok=True)
        app.logger.exception("Photo could not be read or saved")
        abort(422, "This image could not be opened or saved. Try exporting it as a new JPG.")
    except cv2.error:
        path.unlink(missing_ok=True)
        app.logger.exception("Face indexing failed")
        abort(503, "The face indexer could not process this photo. Try again shortly.")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return jsonify(faces=len(vectors))


@app.post("/events/<eid>/challenge")
def challenge(eid):
    ready()
    if not LIVENESS_MODEL.is_file():
        abort(503, "Liveness model is missing. Run python setup_models.py on the server.")
    session.pop("verified_result", None)
    if not event_details(eid)["total"]:
        abort(409, "The organizer hasn't uploaded photos yet.")
    if limited("camera:" + (request.remote_addr or ""), 30, 300):
        abort(429, "Too many camera attempts. Try again in a few minutes.")
    guest = session.setdefault("guest", secrets.token_urlsafe(24))
    ticket = secrets.token_urlsafe(24)
    direction = secrets.choice([-2, -1, 1, 2])
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM challenges WHERE expires<=? OR guest=?", (now, guest))
        conn.execute("INSERT INTO challenges VALUES (?,?,?,?,?)", (ticket, eid, guest, direction, now + 120))
    return jsonify(challenge=ticket, direction="left" if direction > 0 else "right",
                   blinks=abs(direction), frames=FRAME_COUNT, interval=FRAME_INTERVAL_MS)


@app.post("/events/<eid>/search")
def search(eid):
    if request.content_length is None or request.content_length > 6 * 1024 * 1024:
        abort(413, "The camera capture is too large.")
    ready()
    if request.form.get("consent") != "yes":
        abort(400, "Confirm consent before searching.")
    now = int(time.time())
    with db() as conn:
        challenge_row = conn.execute(
            "DELETE FROM challenges WHERE id=? AND event=? AND guest=? AND expires>? RETURNING direction, expires",
            (request.form.get("challenge"), eid, session.get("guest"), now),
        ).fetchone()
    if not challenge_row:
        abort(400, "Camera session expired. Start the camera again.")
    if now - (challenge_row["expires"] - 120) < 4:
        abort(422, LIVENESS_ERROR)
    frames = [request.files.get(name) for name in ("front", "turn", "return")]
    if any(frame is None for frame in frames):
        abort(422, LIVENESS_ERROR)
    try:
        samples = [extract(frame.stream) for frame in frames]
    except (OSError, ValueError, cv2.error, Image.DecompressionBombError):
        abort(422, LIVENESS_ERROR)
    if any(len(sample) != 1 for sample in samples):
        abort(422, LIVENESS_ERROR)
    (query, front), (turned, side), (returned, center) = [sample[0] for sample in samples]
    if (abs(front) > 0.24 or abs(center) > 0.24 or
            (side - front) * np.sign(challenge_row["direction"]) < 0.16 or abs(center - front) > 0.16 or
            np.dot(query, turned) < 0.40 or np.dot(query, returned) < 0.55):
        abort(422, LIVENESS_ERROR)
    burst = request.files.getlist("blink")
    if len(burst) != FRAME_COUNT or not LIVENESS_MODEL.is_file():
        abort(422, LIVENESS_ERROR)
    compressed = [item.stream.read(MAX_FRAME_BYTES + 1) for item in burst]
    if any(not frame or len(frame) > MAX_FRAME_BYTES for frame in compressed):
        abort(422, LIVENESS_ERROR)
    try:
        states = list(blink_states(compressed))
        blink_indices = confirmed_blinks(states, abs(challenge_row["direction"]))
        if not blink_indices:
            abort(422, LIVENESS_ERROR)
        # Bind the person blinking to both front-facing frames used for the search.
        checkpoints = [states.index("open"), *blink_indices,
                       len(states) - 1 - states[::-1].index("open")]
        for index in checkpoints:
            sample = extract(io.BytesIO(compressed[index]))
            if len(sample) != 1 or min(np.dot(query, sample[0][0]),
                                       np.dot(returned, sample[0][0])) < 0.55:
                abort(422, LIVENESS_ERROR)
    except (OSError, ValueError, RuntimeError, cv2.error, Image.DecompressionBombError):
        app.logger.exception("Liveness frames could not be verified")
        abort(422, LIVENESS_ERROR)
    token = secrets.token_urlsafe(32)
    found = {}
    with db() as conn:
        rows = conn.execute("""SELECT p.id,p.name,f.embedding FROM photos p
                               JOIN faces f ON p.id=f.photo WHERE p.event=?""", (eid,))
        for row in rows:
            vector = np.frombuffer(row["embedding"], dtype="float32")
            if min(np.dot(query, vector), np.dot(returned, vector)) >= 0.62:
                found[row["id"]] = row["name"]
        conn.execute("DELETE FROM grants WHERE token IN (SELECT token FROM tickets WHERE expires<=?)", (now,))
        conn.execute("DELETE FROM tickets WHERE expires<=?", (now,))
        conn.execute("INSERT INTO tickets VALUES (?,?,?)", (token, eid, now + 3600))
        conn.executemany("INSERT INTO grants VALUES (?,?)", [(token, pid) for pid in found])
    session["verified_result"] = token
    return jsonify(token=token, photos=[dict(id=pid, name=name) for pid, name in found.items()])


def result_rows(token, pid=None):
    if not secrets.compare_digest(session.get("verified_result", ""), token):
        abort(404, "Results expired or belong to another browser session.")
    query = """SELECT p.id,p.name FROM grants g JOIN photos p ON p.id=g.photo
               JOIN tickets t ON t.token=g.token WHERE g.token=? AND t.expires>?"""
    args = (token, int(time.time()))
    if pid is not None:
        query += " AND p.id=?"
        args += (pid,)
    with db() as conn:
        return conn.execute(query, args).fetchall()


@app.get("/result/<token>/<pid>")
def photo(token, pid):
    rows = result_rows(token, pid)
    if not rows:
        abort(404)
    path = DATA / pid
    download = request.args.get("download") == "1"
    with Image.open(path) as image:
        mimetype = PHOTO_FORMATS[image.format][0]
        if not download and image.format not in ("JPEG", "PNG", "WEBP"):
            image.thumbnail((1000, 1000))
            ImageOps.exif_transpose(image, in_place=True)
            output = io.BytesIO()
            image.convert("RGB").save(output, "JPEG", quality=85)
            output.seek(0)
            return send_file(output, mimetype="image/jpeg")
    return send_file(path, download_name=rows[0]["name"], mimetype=mimetype,
                     as_attachment=download)


@app.post("/result/<token>/zip")
def archive(token):
    selected = set(request.form.getlist("ids"))
    all_photos = request.form.get("scope") == "all"
    rows = [row for row in result_rows(token) if all_photos or row["id"] in selected]
    if not rows:
        abort(404, "Select at least one photo.")
    temp = tempfile.TemporaryFile(dir=DATA)
    try:
        with zipfile.ZipFile(temp, "w", allowZip64=True) as output:
            for row in rows:
                output.write(DATA / row["id"], row["id"] + "_" + row["name"])
        temp.seek(0)
        return send_file(temp, download_name="SnapFind-photos.zip", as_attachment=True, mimetype="application/zip")
    except Exception:
        temp.close()
        raise


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
