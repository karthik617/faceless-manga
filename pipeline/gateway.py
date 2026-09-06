#!/usr/bin/env python3
"""gateway.py — shared access to the local Athena OpenAI-compatible gateway.

Three modes over the same endpoint (http://localhost:5001/v1/chat/completions):
  * llm_text()   — text model (recap scripting, packaging metadata)
  * llm_vision() — multimodal READ (OCR / panel understanding): sends base64
                   image_url parts + a text instruction, returns TEXT.
  * IMAGE_MODEL  — image-edit model id (used by clean_bubbles.py for inpaint)

Token hygiene: never hardcode a JWT. Resolution order: env ATHENA_TOKEN, then
pipeline/athena_token.txt. (faceless-studio hardcodes a token in source; we
deliberately do NOT.)
"""
import base64
import os
import time
from pathlib import Path

import requests

ATHENA_URL = os.environ.get("ATHENA_URL", "http://localhost:5001/v1/chat/completions")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "saas-anthropic-claude-sonnet-4-5")
# The Gemini flash-image model is multimodal and reads images well; use it for
# both vision-read and image-edit. Override per-mode with the env vars below.
VISION_MODEL = os.environ.get("VISION_MODEL", "saas-anthropic-claude-sonnet-4-5")
IMAGE_MODEL = os.environ.get("ATHENA_IMAGE_MODEL", "saas-vertex-gemini-2.5-flash-image")

# common image extensions -> data-URI mime
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".gif": "image/gif"}


def athena_token():
    """Return the gateway bearer token from env or pipeline/athena_token.txt."""
    tok = os.environ.get("ATHENA_TOKEN")
    if tok:
        return tok.strip()
    tok_file = Path(__file__).parent / "athena_token.txt"
    if tok_file.exists():
        return tok_file.read_text().strip()
    return None


def _headers(token):
    return {"accept": "application/json", "Authorization": token,
            "Content-Type": "application/json"}


def _data_uri(path):
    path = Path(path)
    mime = _MIME.get(path.suffix.lower(), "image/png")
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def llm_text(prompt, max_tokens=16000, retries=3, model=None):
    """Plain text completion via the gateway. Raises on final failure.

    Backoff shape mirrors make_video.py's network helpers (5*(attempt+1)).
    """
    token = athena_token()
    if not token:
        raise RuntimeError("no Athena token (set ATHENA_TOKEN or pipeline/athena_token.txt)")
    body = {"model": model or TEXT_MODEL, "stream": False,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(ATHENA_URL, json=body, headers=_headers(token),
                              timeout=600)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except (requests.RequestException, KeyError, IndexError, TypeError,
                ValueError) as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"llm_text failed after {retries} tries: {last}")


def llm_vision(images, instruction, retries=4, max_tokens=8000, model=None):
    """Multimodal READ: send image(s) + a text instruction, return the text reply.

    Same request body shape as make_video.py::fetch_keyframe_gemini, but WITHOUT
    modalities:["image"] so the gateway returns text (OCR / panel description /
    speaker attribution) instead of a generated image.

    images: a single path or a list of paths (batched into one message).
    """
    token = athena_token()
    if not token:
        raise RuntimeError("no Athena token (set ATHENA_TOKEN or pipeline/athena_token.txt)")
    if isinstance(images, (str, Path)):
        images = [images]
    content = [{"type": "image_url", "image_url": {"url": _data_uri(p)}}
               for p in images]
    content.append({"type": "text", "text": instruction})
    body = {"model": model or VISION_MODEL, "stream": False,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}]}
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(ATHENA_URL, json=body, headers=_headers(token),
                              timeout=300)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except (requests.RequestException, KeyError, IndexError, TypeError,
                ValueError) as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"llm_vision failed after {retries} tries: {last}")


def image_edit(image_path, instruction, out_path, aspect="16:9", retries=4,
               model=None):
    """Image-edit via the gateway (Gemini flash-image): edit image_path per the
    instruction, write the returned image to out_path. Used by clean_bubbles.

    Mirrors make_video.py::fetch_keyframe_gemini exactly (keeps modalities:image).
    """
    token = athena_token()
    if not token:
        raise RuntimeError("no Athena token (set ATHENA_TOKEN or pipeline/athena_token.txt)")
    body = {
        "model": model or IMAGE_MODEL, "stream": False,
        "modalities": ["image"],
        "imageConfig": {"aspectRatio": aspect},
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": _data_uri(image_path)}},
            {"type": "text", "text": instruction},
        ]}],
    }
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(ATHENA_URL, json=body, headers=_headers(token),
                              timeout=180)
            if r.status_code == 200:
                data = r.json()
                url = data["choices"][0]["message"]["images"][0]["image_url"]["url"]
                Path(out_path).write_bytes(base64.b64decode(url.split(",", 1)[1]))
                return
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except (requests.RequestException, KeyError, IndexError, TypeError,
                ValueError) as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"image_edit failed after {retries} tries: {last}")
