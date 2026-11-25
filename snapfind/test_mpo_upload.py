import io
import os
import tempfile

from PIL import Image


def test_mpo_upload():
    with tempfile.TemporaryDirectory() as data:
        os.environ.update(SNAPFIND_DATA=data, ADMIN_PASSWORD="local-test-only")
        from app import DATA, app, db

        primary = Image.new("RGB", (6000, 4000), "red")
        extra = Image.new("RGB", (320, 220), "blue")
        fixture = io.BytesIO()
        primary.save(fixture, format="MPO", save_all=True, append_images=[extra])
        del primary, extra
        raw = fixture.getvalue()
        with Image.open(io.BytesIO(raw)) as original:
            assert original.format == "MPO" and original.n_frames == 2

        with app.test_client() as organizer:
            assert organizer.post("/login", json={"password": "local-test-only"}).status_code == 200
            event_id = organizer.post("/events", json={"name": "MPO upload test"}).json["id"]
            route = f"/events/{event_id}/photos"
            result = organizer.post(route, data={"photo": (io.BytesIO(raw), "DSC_5558.JPG")})
            assert result.status_code == 200, result.json
            assert organizer.get(f"/events/{event_id}").json["ready"]
            duplicate = organizer.post(route, data={"photo": (io.BytesIO(raw), "DSC_5558.JPG")})
            assert duplicate.json["duplicate"]

        with db() as connection:
            photo = connection.execute("SELECT id,name FROM photos").fetchone()
            assert photo["name"] == "DSC_5558.JPG"
        with Image.open(DATA / photo["id"]) as saved:
            assert saved.format == "JPEG" and getattr(saved, "n_frames", 1) == 1
            assert saved.size == (1500, 1000)
            red, _, blue = saved.getpixel((160, 110))
            assert red > 240 and blue < 15
        print("MPO regression passed: camera .JPG indexed, primary JPEG stored, duplicate skipped", flush=True)


if __name__ == "__main__":
    test_mpo_upload()
