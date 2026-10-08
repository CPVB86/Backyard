"""Samsung setters may emit unsolicited events rather than matching UUID replies.

Send setters once, verify their effect through getters, and bound D2D waits
even if unrelated TV events keep arriving. Never change upload matte settings.
"""
import time
from samsungtvws.art import SamsungTVArt
from samsungtvws.exceptions import ConnectionFailure, ResponseError


class RetryableUploadError(RuntimeError):
    """Proven failure before any image bytes were attempted."""


class UploadSocket:
    # samsungtvws 3.0.6 sends length header, JSON header, then image bytes.
    def __init__(self, socket, art):
        self.socket = socket
        self.art = art
        self.writes = 0

    def sendall(self, data):
        self.writes += 1
        if self.writes >= 3:
            # Mark before send: a partial write may already have reached the TV.
            self.art._upload_started = True
        return self.socket.sendall(data)

    def __getattr__(self, name):
        return getattr(self.socket, name)


class BoundedArt(SamsungTVArt):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_deadline = None
        self._setter_requests = {}
        self._upload_started = False

    def upload(self, file, **kwargs):
        self._upload_started = False
        try:
            return super().upload(file, **kwargs)
        except Exception as exc:
            if not self._upload_started:
                raise RetryableUploadError(f"No image bytes sent: {type(exc).__name__}: {exc}") from exc
            raise

    def _open_d2d_socket(self, conn_info):
        return UploadSocket(super()._open_d2d_socket(conn_info), self)

    def _upload_ws_binary_send_image(self, **kwargs):
        self._upload_started = True
        return super()._upload_ws_binary_send_image(**kwargs)

    def _send_setter(self, request, **params):
        request_id = self._new_request_uuid()
        self._setter_requests[request_id] = request
        self._send_art_request({"request": request, **params},
                               request_uuid=request_id, wait_for_event=None)

    def set_artmode(self, mode):
        self._send_setter("set_artmode_status", value=self._to_on_off(mode))

    def select_image(self, content_id, category=None, show=True):
        params = {"content_id": content_id, "show": show}
        if category is not None:
            params["category_id"] = category
        self._send_setter("select_image", **params)

    def _wait_for_d2d(self, *, request_uuid, wait_for_sub_event):
        previous = self._request_deadline
        self._request_deadline = time.monotonic() + (self.timeout or 60)
        try:
            return super()._wait_for_d2d(request_uuid=request_uuid,
                                         wait_for_sub_event=wait_for_sub_event)
        finally:
            self._request_deadline = previous

    def _recv_frame(self):
        socket = self.connection
        old_timeout = socket.gettimeout() if socket else None
        if self._request_deadline is not None and socket:
            remaining = self._request_deadline - time.monotonic()
            if remaining <= 0:
                raise ConnectionFailure("Art request deadline exceeded while receiving TV events")
            socket.settimeout(min(remaining, self.timeout or 60))
        try:
            event, frame = super()._recv_frame()
            payload = self._decode_d2d_payload(frame)
            if payload:
                request_id = payload.get("request_id", payload.get("id"))
                request = self._setter_requests.get(request_id)
                if request and payload.get("event") == "error":
                    raise ResponseError(f"`{request}` request failed with error number {payload.get('error_code', 'unknown')}")
            return event, frame
        finally:
            if socket:
                socket.settimeout(old_timeout)
