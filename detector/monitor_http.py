"""Existing POST JSON + PUT WAV contract; bounded retries, no database imports."""
import http.client
import json
import socket
import time
from urllib.parse import urlsplit
from uuid import UUID


class HTTPFailure(RuntimeError):
    def __init__(self, status):
        super().__init__(f"Backyard HTTP {status}")
        self.retryable = status in (408, 429) or status >= 500


class HTTPTransport:
    def __init__(self, base_url, timeout):
        self.origin = urlsplit(base_url)
        self.timeout = timeout

    def request(self, method, path, body, content_type):
        cls = http.client.HTTPSConnection if self.origin.scheme == "https" else http.client.HTTPConnection
        connection = cls(self.origin.hostname, self.origin.port, timeout=self.timeout)
        try:
            started = time.monotonic()
            connection.request(method, path, body=body, headers={"Content-Type": content_type})
            response = connection.getresponse()
            # Bounded body and wall-clock checked between reads. No redirects.
            data = bytearray()
            while len(data) <= 65536:
                if time.monotonic() - started > self.timeout:
                    raise TimeoutError("HTTP response deadline exceeded")
                part = response.read1(min(8192, 65537 - len(data)))
                if not part:
                    break
                data.extend(part)
            if len(data) > 65536:
                raise ValueError("Oversized Backyard response")
            if response.status not in (200, 201):
                raise HTTPFailure(response.status)
            return json.loads(data)
        finally:
            connection.close()


class Uploader:
    def __init__(self, config, metrics, stop, transport=None):
        self.config, self.metrics, self.stop = config, metrics, stop
        self.transport = transport or HTTPTransport(config.api_url, config.http_timeout)

    def send(self, upload):
        body = json.dumps(upload.payload, allow_nan=False, separators=(",", ":")).encode()
        detection_id = None
        for attempt in range(self.config.attempts):
            if self.stop.is_set():
                return False
            try:
                if detection_id is None:
                    reply = self.transport.request("POST", "/api/birds/detections",
                                                   body, "application/json")
                    detection_id = str(UUID(reply["id"]))
                    self.metrics.add("detections_uploaded")
                self.transport.request("PUT", f"/api/birds/detections/{detection_id}/audio",
                                       upload.wav, "audio/wav")
                self.metrics.add("uploads_ok")
                return True
            except (HTTPFailure, OSError, socket.timeout, http.client.HTTPException,
                    ValueError, KeyError, TypeError) as error:
                self.metrics.add("http_failures")
                self.metrics.set(last_http_error=str(error)[:200])
                retryable = not isinstance(error, (ValueError, KeyError, TypeError))
                if isinstance(error, HTTPFailure):
                    retryable = error.retryable
                if not retryable or attempt + 1 == self.config.attempts:
                    break
                self.metrics.add("http_retries")
                if self.stop.wait(min(0.5 * 2 ** attempt, 2.0)):
                    return False
        self.metrics.add("uploads_failed")
        return False
