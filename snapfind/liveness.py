import io
import threading
from pathlib import Path

import mediapipe as mp
import numpy as np
from PIL import Image

MODEL = Path(__file__).resolve().parent / "models" / "face_landmarker.task"
FRAME_COUNT = 64
FRAME_INTERVAL_MS = 67
MAX_FRAME_BYTES = 60_000
LIVENESS_ERROR = "Live selfie could not be verified. Do not show a photo or another phone to the camera. Look directly at the camera and blink when prompted."
_lock = threading.Lock()
_detector = None


def blink_states(frames):
    global _detector
    with _lock:
        if _detector is None:
            options = mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL)),
                num_faces=2, output_face_blendshapes=True,
            )
            _detector = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        for frame in frames:
            with Image.open(io.BytesIO(frame)) as image:
                if image.format != "JPEG" or max(image.size) > 400:
                    raise ValueError("Invalid camera frame")
                pixels = np.asarray(image.convert("RGB"))
            result = _detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=pixels))
            if len(result.face_blendshapes) != 1:
                yield "missing"
                continue
            scores = {item.category_name: item.score for item in result.face_blendshapes[0]}
            eyes = scores["eyeBlinkLeft"], scores["eyeBlinkRight"]
            if min(eyes) >= 0.57:
                yield "closed"
            elif max(eyes) <= 0.32:
                yield "open"
            else:
                yield "transition"


def confirmed_blinks(states, expected):
    if (states.count("missing") > 3 or
            states[:10].count("open") < 4 or states[-10:].count("open") < 4):
        return []
    closed_frames = []
    opened = False
    last_closed = None
    for index, state in enumerate(states):
        if state == "missing":
            opened = False
            last_closed = None
        elif state == "open":
            if opened and last_closed is not None:
                closed_frames.append(last_closed)
            last_closed = None
            opened = True
        elif state == "closed" and opened and last_closed is None:
            last_closed = index
    if len(closed_frames) != expected or any(i < 10 or i >= FRAME_COUNT - 7 for i in closed_frames):
        return []
    return closed_frames
