from pathlib import Path
from urllib.request import urlretrieve

models = Path(__file__).resolve().parent / "models"
models.mkdir(exist_ok=True)
opencv = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
sources = {
    "face_detection_yunet_2023mar.onnx": opencv + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": opencv + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}
for name, url in sources.items():
    model = models / name
    if model.is_file():
        continue
    print("Downloading", name)
    pending = model.with_suffix(".part")
    urlretrieve(url, pending)
    pending.replace(model)
