"use strict";

const el = (id) => document.getElementById(id);
const eid = document.body.dataset.event;
const eventPath = (id) => "/events/" + encodeURIComponent(id);
let organizerSignedIn = document.body.dataset.admin === "yes";
let eventReady = false;
let stream;
let cameraRun = 0;
let challenge;
let frames = [];
let blinkFrames = [];
let active = "";
let selected = [];
let uploading = false;

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Request failed. Please try again.");
  return data;
}

function json(data) {
  return {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)};
}

async function loadGuest() {
  if (!eid) return;
  try {
    const info = await api(eventPath(eid));
    el("eventName").textContent = info.name;
    eventReady = info.ready;
    if (!eventReady) el("status").textContent = "The organizer has not uploaded photos yet.";
  } catch (error) {
    el("eventName").textContent = error.message;
  }
  el("openCamera").disabled = !eventReady;
}

function stopCamera() {
  cameraRun++;
  if (stream) stream.getTracks().forEach((track) => track.stop());
  stream = null;
  el("video").srcObject = null;
  el("video").hidden = true;
  el("frame").hidden = false;
  el("capture").hidden = true;
  el("openCamera").hidden = false;
  el("openCamera").disabled = !eventReady;
  el("cancelCamera").hidden = true;
  challenge = null;
  frames = [];
  blinkFrames = [];
}

el("openCamera").addEventListener("click", async () => {
  if (!el("consent").checked) {
    el("status").textContent = "Confirm consent before opening the camera.";
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    el("status").textContent = "Open this event link over HTTPS in Safari or Chrome to use your camera.";
    return;
  }
  const run = ++cameraRun;
  el("openCamera").disabled = true;
  el("cancelCamera").hidden = false;
  el("results").hidden = true;
  el("gallery").replaceChildren();
  el("zip").removeAttribute("action");
  el("status").textContent = "Allow camera access when your browser asks.";
  try {
    const camera = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"user"}, width:{ideal:960}},audio:false});
    if (run !== cameraRun) {
      camera.getTracks().forEach((track) => track.stop());
      return;
    }
    stream = camera;
    const view = el("video");
    view.srcObject = stream;
    await view.play();
    if (run !== cameraRun) return;
    const ticket = await api(eventPath(eid) + "/challenge", {method:"POST"});
    if (run !== cameraRun) return;
    challenge = ticket;
    view.hidden = false;
    el("frame").hidden = true;
    el("openCamera").hidden = true;
    el("capture").hidden = false;
    el("capture").disabled = false;
    el("capture").textContent = "Capture front view";
    el("status").textContent = "Look straight into the camera.";
  } catch (error) {
    if (run !== cameraRun) return;
    stopCamera();
    const cameraErrors = {
      NotAllowedError:"Camera access was denied. Enable it in your browser settings and try again.",
      NotFoundError:"No camera was detected. Connect a camera or open the link on your phone.",
      NotReadableError:"The camera is busy or unavailable. Close other camera apps and try again."
    };
    el("status").textContent = cameraErrors[error.name] || error.message;
  }
});

el("cancelCamera").addEventListener("click", () => {
  stopCamera();
  el("status").textContent = "";
});
window.addEventListener("pagehide", stopCamera);

function frameFromCamera(maxSize = 960, quality = 0.9) {
  const video = el("video");
  if (!video.videoWidth) throw new Error("Wait for the camera preview, then try again.");
  const canvas = document.createElement("canvas");
  const scale = Math.min(1, maxSize / Math.max(video.videoWidth, video.videoHeight));
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) => canvas.toBlob((blob) => {
    if (blob) resolve(blob);
    else reject(new Error("Could not capture the camera frame."));
  }, "image/jpeg", quality));
}

async function recordBlinks(run) {
  const {frames: count, interval, blinks} = challenge;
  const captured = [];
  el("status").textContent = "Look straight and keep your eyes open. Wait for the blink prompt.";
  const started = performance.now();
  for (let index = 0; index < count; index++) {
    const wait = Math.max(0, started + index * interval - performance.now());
    await new Promise((resolve) => setTimeout(resolve, wait));
    if (run !== cameraRun) return null;
    if (index === 12) el("status").textContent = blinks === 1 ? "Blink once now." : "Blink twice now.";
    if (index === count - 12) el("status").textContent = "Keep your eyes open and look straight.";
    captured.push(await frameFromCamera(400, 0.7));
  }
  return captured;
}

el("capture").addEventListener("click", async () => {
  const run = cameraRun;
  el("capture").disabled = true;
  try {
    const frame = await frameFromCamera();
    if (run !== cameraRun) return;
    frames.push(frame);
    if (frames.length === 1) {
      blinkFrames = await recordBlinks(run);
      if (run !== cameraRun) return;
      el("status").textContent = "Keep your device still. Turn your head to your " + challenge.direction + ".";
      el("capture").textContent = "Capture turned view";
    } else if (frames.length === 2) {
      el("status").textContent = "Look straight into the camera again.";
      el("capture").textContent = "Capture selfie and find photos";
    } else {
      const data = new FormData();
      ["front", "turn", "return"].forEach((name, index) => data.append(name, frames[index], name + ".jpg"));
      blinkFrames.forEach((frame) => data.append("blink", frame, "blink.jpg"));
      data.append("consent", el("consent").checked ? "yes" : "no");
      data.append("challenge", challenge.challenge);
      stopCamera();
      const searchRun = cameraRun;
      el("openCamera").disabled = true;
      el("status").textContent = "Finding photos from this event…";
      try {
        const result = await api(eventPath(eid) + "/search", {method:"POST", body:data});
        if (searchRun === cameraRun) renderResults(result);
      } catch (error) {
        if (searchRun === cameraRun) el("status").textContent = error.message;
      } finally {
        if (searchRun === cameraRun) el("openCamera").disabled = !eventReady;
      }
    }
  } catch (error) {
    if (run === cameraRun) {
      stopCamera();
      el("status").textContent = error.message;
    }
  } finally {
    if (run === cameraRun) el("capture").disabled = false;
  }
});

function renderResults(result) {
  el("gallery").replaceChildren();
  el("zip").action = "/result/" + result.token + "/zip";
  result.photos.forEach((photo) => {
    const path = "/result/" + result.token + "/" + photo.id;
    const tile = document.createElement("article");
    tile.className = "tile";
    const image = document.createElement("img");
    image.src = path;
    image.alt = photo.name;
    image.loading = "lazy";
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "ids";
    checkbox.checked = true;
    checkbox.value = photo.id;
    label.append(checkbox, " Select photo");
    const link = document.createElement("a");
    link.href = path + "?download=1";
    link.textContent = "Download original";
    tile.append(image, label, link);
    el("gallery").append(tile);
  });
  const count = result.photos.length;
  el("count").textContent = count ? count + (count === 1 ? " possible match" : " possible matches") : "No matches found";
  el("downloadActions").hidden = !count;
  el("results").hidden = false;
  el("status").textContent = count ? "" : "Try again with brighter lighting and a clear view of your face.";
}

el("zip").addEventListener("submit", (event) => {
  if (event.submitter?.value === "selected" && !el("gallery").querySelector("input:checked")) {
    event.preventDefault();
    el("status").textContent = "Select at least one photo first.";
  }
});

async function refreshEvents() {
  const info = await api("/events");
  el("events").replaceChildren(new Option("Select an event", ""));
  info.events.forEach((event) => el("events").add(new Option(event.name + " · " + event.total + " photos", event.id)));
  el("events").value = active;
}

function setEvent(id) {
  active = id;
  el("share").hidden = !id;
  el("upload").disabled = !id || !selected.length || uploading;
  if (id) {
    const url = new URL("/", location.origin);
    url.searchParams.set("event", id);
    el("link").value = url.href;
  }
}

el("switch").addEventListener("click", async () => {
  const showOrganizer = el("organizer").hidden;
  stopCamera();
  el("guest").hidden = showOrganizer;
  el("organizer").hidden = !showOrganizer;
  el("switch").textContent = showOrganizer ? "Guest view" : "Organizer dashboard";
  if (showOrganizer && organizerSignedIn) {
    try { await refreshEvents(); }
    catch (error) { el("adminStatus").textContent = error.message; }
  }
});

el("login").hidden = organizerSignedIn;
el("manage").hidden = !organizerSignedIn;

el("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/login", json({password:el("password").value}));
    organizerSignedIn = true;
    el("password").value = "";
    el("login").hidden = true;
    el("manage").hidden = false;
    el("adminStatus").textContent = "";
    await refreshEvents();
  } catch (error) {
    el("adminStatus").textContent = error.message;
  }
});

el("create").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const result = await api("/events", json({name:el("ename").value}));
    setEvent(result.id);
    el("ename").value = "";
    el("adminStatus").textContent = "Event created.";
    await refreshEvents();
  } catch (error) {
    el("adminStatus").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

el("events").addEventListener("change", () => setEvent(el("events").value));
el("copyLink").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(el("link").value);
    el("adminStatus").textContent = "Guest link copied.";
  } catch {
    el("link").select();
    el("adminStatus").textContent = "Copy the selected guest link.";
  }
});

["folder", "files"].forEach((id) => el(id).addEventListener("change", (event) => {
  selected = Array.from(event.target.files).filter((file) => file.type.startsWith("image/") || /\.(jpe?g|jfif|png|webp|hei[cf]|avif|tiff?|bmp|gif)$/i.test(file.name));
  const bytes = selected.reduce((total, file) => total + file.size, 0);
  const size = bytes >= 1e9 ? (bytes / 1e9).toFixed(2) + " GB" : (bytes / 1e6).toFixed(1) + " MB";
  el("selection").textContent = selected.length ? selected.length + " photos · " + size : "No supported photos selected.";
  el("upload").textContent = "Upload and index photos";
  el("upload").disabled = !active || !selected.length || uploading;
  event.target.value = "";
}));

el("upload").addEventListener("click", async () => {
  if (!active || !selected.length || uploading) return;
  const destination = active;
  const files = selected;
  const failed = [];
  let uploaded = 0;
  let duplicates = 0;
  let withoutFaces = 0;
  uploading = true;
  const disabled = [el("upload"), el("events"), el("folder"), el("files"), el("create").querySelector("button")];
  disabled.forEach((control) => control.disabled = true);
  el("progress").value = 0;
  el("failures").hidden = true;
  el("failures").replaceChildren();
  for (const [index, file] of files.entries()) {
    el("uploadStatus").textContent = "Processing " + (index + 1) + "/" + files.length + " · " + file.name;
    const body = new FormData();
    body.append("photo", file);
    try {
      const result = await api(eventPath(destination) + "/photos", {method:"POST", body});
      if (result.duplicate) duplicates++;
      else {
        uploaded++;
        if (!result.faces) withoutFaces++;
      }
    } catch (error) {
      failed.push(file);
      if (failed.length <= 20) {
        const item = document.createElement("li");
        item.textContent = file.name + " · " + error.message;
        el("failures").append(item);
      }
    }
    el("progress").value = ((index + 1) / files.length) * 100;
  }
  el("uploadStatus").textContent = uploaded + " saved · " + duplicates + " duplicates · " + failed.length + " failed" +
    (withoutFaces ? " · " + withoutFaces + " saved without a detected face" : "");
  el("failures").hidden = !failed.length;
  selected = failed;
  el("selection").textContent = failed.length ? failed.length + " photos available to retry." : "Upload finished.";
  uploading = false;
  disabled.forEach((control) => control.disabled = false);
  el("upload").disabled = !failed.length;
  el("upload").textContent = failed.length ? "Retry failed photos" : "Upload and index photos";
  try { await refreshEvents(); }
  catch (error) { el("adminStatus").textContent = error.message; }
});

loadGuest();
