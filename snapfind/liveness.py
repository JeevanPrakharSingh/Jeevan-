import io
import threading
from pathlib import Path

import numpy as np
from PIL import Image

MODEL = Path(__file__).resolve().parent / "models" / "face_landmarker.task"
FRAME_COUNT = 20
FRAME_INTERVAL_MS = 55
MAX_FRAME_BYTES = 60_000
LIVENESS_ERROR = "Live selfie could not be verified. Do not show a photo or another phone to the camera. Look directly at the camera and blink when prompted."
_lock = threading.Lock()
_detector = None


def blink_states(frames):
    import mediapipe as mp

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
            if min(eyes) >= 0.50:
                yield "closed"
            elif max(eyes) <= 0.37:
                yield "open"
            else:
                yield "transition"


def confirmed_blink(states):
    before = closed = None
    for index, state in enumerate(states):
        if state == "missing":
            before = closed = None
        elif state == "open":
            if before is not None and closed is not None and index - closed <= 9:
                return before, closed, index
            before, closed = index, None
        elif state == "closed" and before is not None and closed is None:
            if index - before <= 7:
                closed = index
    return None
