"""Decide how to frame a horizontal clip in a 9:16 vertical video.

Modes:
    auto     - follow the speaking face; screen recordings (no faces) use fit-blur
    track    - always follow faces
    center   - static center crop
    fit-blur - whole frame, fitted to width, on a blurred background
    split    - two people stacked top/bottom (falls back to track)

The output is a plan dict consumed by render.py; no video is written here.
"""
import os

import numpy as np

from Components import config

MODES = ["auto", "track", "center", "fit-blur", "split"]
SAMPLE_FPS = 4.0
MIN_FACE_RATIO = 0.25  # fraction of samples that need a face for "auto" to track
DETECT_HEIGHT = 360

_NET = None


def _even(value):
    return int(value) // 2 * 2


def _load_net():
    """OpenCV DNN face detector, or a Haar cascade if DNN/Caffe is unavailable."""
    global _NET
    if _NET is None:
        import cv2
        try:
            _NET = cv2.dnn.readNetFromCaffe(
                os.path.join(config.MODELS_DIR, "deploy.prototxt"),
                os.path.join(config.MODELS_DIR, "res10_300x300_ssd_iter_140000_fp16.caffemodel"))
        except (AttributeError, cv2.error) as e:
            print(f"DNN face detector unavailable ({e}); using Haar cascade")
            _NET = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _NET


def detect_faces(frame, min_confidence=0.5):
    """Return faces as (cx, cy, w, h, confidence) in pixel coordinates."""
    import cv2
    h, w = frame.shape[:2]
    net = _load_net()
    if isinstance(net, cv2.CascadeClassifier):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found = net.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=8, minSize=(h // 20, h // 20))
        return [(x + fw / 2, y + fh / 2, fw, fh, 1.0) for x, y, fw, fh in found]
    # The SSD is fully convolutional: keep the aspect ratio so small faces in wide frames survive.
    size = (max(32, int(w * DETECT_HEIGHT / h)), DETECT_HEIGHT)
    net.setInput(cv2.dnn.blobFromImage(cv2.resize(frame, size), 1.0, size, (104.0, 177.0, 123.0)))
    detections = net.forward()
    faces = []
    for i in range(detections.shape[2]):
        conf = float(detections[0, 0, i, 2])
        if conf < min_confidence:
            continue
        x0, y0, x1, y1 = (detections[0, 0, i, 3:7] * np.array([w, h, w, h])).astype(int)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 - x0 < w * 0.02 or y1 - y0 < h * 0.03:
            continue
        faces.append(((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, conf))
    return faces


def _mouth_patch(gray, face):
    import cv2
    cx, cy, fw, fh, _ = face
    x0, x1 = int(cx - fw * 0.3), int(cx + fw * 0.3)
    y0, y1 = int(cy + fh * 0.1), int(cy + fh * 0.45)
    patch = gray[max(0, y0):max(1, y1), max(0, x0):max(1, x1)]
    if patch.size == 0:
        return None
    return cv2.resize(patch, (32, 16)).astype(np.float32)


def sample_faces(video_path, start, end, sample_fps=SAMPLE_FPS):
    """Detect faces at `sample_fps` between start and end.

    Returns (samples, (width, height)) where samples is a list of
    {"t": seconds-from-clip-start, "faces": [(cx, cy, w, h, conf, mouth_activity)]}.
    """
    import cv2
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    step = max(1, int(round(fps / sample_fps)))
    samples, previous, frame_index = [], [], 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if t > end:
            break
        if frame_index % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = []
            for face in detect_faces(frame):
                patch = _mouth_patch(gray, face)
                activity = 0.0
                if patch is not None and previous:
                    # Compare with the nearest face in the previous sample.
                    prev = min(previous, key=lambda p: abs(p[0][0] - face[0]) + abs(p[0][1] - face[1]))
                    if abs(prev[0][0] - face[0]) < face[2] and prev[1] is not None:
                        activity = float(np.mean(np.abs(patch - prev[1])))
                faces.append((face, patch, activity))
            previous = faces
            samples.append({"t": max(0.0, t - start),
                            "faces": [f[0] + (f[2],) for f in faces]})
        frame_index += 1
    cap.release()
    return samples, size


def plan_track(samples, src_w, crop_w, min_hold=1.2, dead_zone=0.12, activity_window=4):
    """Turn face samples into camera keyframes [(t, x_left)] that only change by cuts.

    The target is the face whose mouth moved most recently (active speaker),
    weighted by face size. The camera holds its position while the target stays
    inside a dead zone and only cuts after the new position is held `min_hold` seconds.
    """
    max_x = max(0, src_w - crop_w)

    def left_for(cx):
        return int(min(max_x, max(0, cx - crop_w / 2)))

    targets = []
    history = []
    for sample in samples:
        faces = sample["faces"]
        if not faces:
            targets.append(None)
            history.append([])
            continue
        history.append(faces)
        recent = history[-activity_window:]

        def score(face):
            # Average mouth activity of nearby faces over the last few samples.
            acts = [f[5] for past in recent for f in past if abs(f[0] - face[0]) < face[2]]
            return (np.mean(acts) if acts else 0.0) + 0.02 * face[2] / max(1, src_w) * 100

        targets.append(max(faces, key=score)[0])

    # Fill gaps with the previous (or next) known target.
    known = [x for x in targets if x is not None]
    if not known:
        return [(0.0, left_for(src_w / 2))]
    last = known[0]
    filled = []
    for x in targets:
        last = x if x is not None else last
        filled.append(last)

    keyframes = [(0.0, left_for(filled[0]))]
    current = filled[0]
    pending_since = None
    for sample, x in zip(samples, filled):
        if abs(x - current) <= dead_zone * crop_w:
            pending_since = None
            continue
        if pending_since is None:
            pending_since = sample["t"]
        if sample["t"] - pending_since >= min_hold:
            current = x
            cut_time = round(pending_since, 2)
            if cut_time <= keyframes[-1][0]:
                keyframes[-1] = (keyframes[-1][0], left_for(x))
            else:
                keyframes.append((cut_time, left_for(x)))
            pending_since = None
    return keyframes


def _two_speakers(samples, src_w):
    """Return the x centers of two consistently present, well separated faces, or None."""
    faces = sorted((f[0], f[1]) for s in samples for f in s["faces"])
    multi = sum(1 for s in samples if len(s["faces"]) >= 2)
    if len(samples) == 0 or multi < 0.5 * len(samples) or len(faces) < 2:
        return None
    xs = np.array([f[0] for f in faces])
    split = int(np.argmax(np.diff(xs))) + 1
    left, right = faces[:split], faces[split:]
    if np.median(xs[split:]) - np.median(xs[:split]) < src_w * 0.2:
        return None
    return [(float(np.median([f[0] for f in g])), float(np.median([f[1] for f in g]))) for g in (left, right)]


def plan_framing(video_path, start, end, mode="auto", src_size=None):
    """Analyse the clip and return a framing plan for render.py."""
    if mode not in MODES:
        raise ValueError(f"Unknown framing mode '{mode}'. Choose one of: {', '.join(MODES)}")
    src_w, src_h = src_size or (0, 0)
    samples = []
    if mode in ("auto", "track", "split") or not src_w:
        samples, (src_w, src_h) = sample_faces(video_path, start, end)

    # Already vertical (or square-ish narrower than 9:16): just fit it.
    if src_w / max(1, src_h) <= 9 / 16 + 0.01:
        return {"mode": "fit-blur", "src_w": src_w, "src_h": src_h}

    crop_w, crop_h = _even(src_h * 9 / 16), _even(src_h)
    plan = {"mode": mode, "src_w": src_w, "src_h": src_h, "crop_w": crop_w, "crop_h": crop_h}

    with_faces = sum(1 for s in samples if s["faces"])
    if mode == "auto":
        if not samples or with_faces < MIN_FACE_RATIO * len(samples):
            return {**plan, "mode": "fit-blur"}
        plan["mode"] = mode = "track"

    if mode == "split":
        pair = _two_speakers(samples, src_w)
        if pair:
            # Each person fills one 9:8 half of the 9:16 frame.
            half_w = _even(min(src_w / 2, src_h * 9 / 8))
            half_h = _even(half_w * 8 / 9)
            boxes = [(int(min(src_w - half_w, max(0, cx - half_w / 2))),
                      int(min(src_h - half_h, max(0, cy - half_h * 0.45)))) for cx, cy in pair]
            return {**plan, "half_w": half_w, "half_h": half_h, "boxes": boxes}
        plan["mode"] = mode = "track"

    if mode == "center":
        plan["keyframes"] = [(0.0, (src_w - crop_w) // 2)]
    elif mode == "track":
        plan["keyframes"] = plan_track(samples, src_w, crop_w)
    return plan
