"""OpenAI Images adapter. No SDK, retries or logging of credentials.

Reference: https://developers.openai.com/api/docs/guides/image-generation
Only an explicit CLI generation action invokes this module's request function.
"""
import base64
import json
import mimetypes
from pathlib import Path
import urllib.error
import urllib.request
import uuid

DEFAULT_MODEL = "gpt-image-2.5-flare"
API_ROOT = "https://api.openai.com/v1/images/"


def build_request(key, prompt, references=(), model=DEFAULT_MODEL, quality="medium"):
    if not key or "\n" in key or "\r" in key:
        raise ValueError("Set OPENAI_API_KEY in the local .env file or environment")
    if quality not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Unsupported image quality")
    fields = dict(model=model, prompt=prompt, n="1", size="1024x1024",
                  quality=quality, background="transparent", output_format="png")
    headers = {"Authorization": "Bearer " + key}
    if not references:
        body = json.dumps(dict(fields, n=1)).encode()
        headers["Content-Type"] = "application/json"
        endpoint = "generations"
    else:
        boundary = "avian-" + uuid.uuid4().hex
        chunks = []
        for name, value in fields.items():
            chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode())
        for index, reference in enumerate(references):
            path = Path(reference)
            mime = mimetypes.guess_type(path.name)[0]
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("References must be PNG, JPEG or WebP")
            data = path.read_bytes()
            if len(data) > 20 * 1024 * 1024:
                raise ValueError("Reference exceeds this tool's 20 MB limit")
            chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="reference-{index}{path.suffix}"\r\nContent-Type: {mime}\r\n\r\n').encode())
            chunks.extend([data, b"\r\n"])
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        endpoint = "edits"
    return urllib.request.Request(API_ROOT + endpoint, data=body, headers=headers, method="POST")


def generate_png(key, prompt, references=(), model=DEFAULT_MODEL, quality="medium", opener=None):
    request = build_request(key, prompt, references, model, quality)
    try:
        with (opener or urllib.request.urlopen)(request, timeout=240) as response:
            payload = json.load(response)
    except (json.JSONDecodeError, UnicodeError):
        raise RuntimeError("OpenAI returned invalid JSON. No automatic retry was made.") from None
    except urllib.error.HTTPError as error:
        # Do not echo remote bodies: proxies may reflect request credentials.
        raise RuntimeError(f"OpenAI returned HTTP {error.code}; check account access, billing, model and key. No automatic retry was made.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("OpenAI connection failed or timed out. Check usage before retrying: the request may have completed.") from None
    try:
        image = base64.b64decode(payload["data"][0]["b64_json"], validate=True)
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError("OpenAI response did not contain a valid base64 image") from None
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("OpenAI response was not a PNG")
    return image
