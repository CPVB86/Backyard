"""One long-lived ALSA process; raw PCM into RAM only."""
import os
import re
import subprocess
from threading import Event, Thread
import time


class ALSACapture:
    def __init__(self, config, ring, metrics, stop, fail):
        self.config, self.ring, self.metrics = config, ring, metrics
        self.stop, self.fail = stop, fail
        self.process = None
        self.threads = []
        self.format_ready = Event()
        self.last_read = time.monotonic()

    def start(self):
        env = dict(os.environ, LC_ALL="C")
        self.process = subprocess.Popen([
            "arecord", "-v", "--fatal-errors", "-D", self.config.device, "-t", "raw",
            "-f", "S16_LE", "-r", str(self.config.rate), "-c", "1",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            bufsize=0, env=env, start_new_session=True)
        for target, name in ((self._read, "pcm-capture"), (self._errors, "alsa-errors")):
            thread = Thread(target=target, name=name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def _read(self):
        remainder = b""
        # -v reports the outer PCM setup before samples. Reject unknown rate
        # instead of assigning requested-rate timestamps to negotiated audio.
        if not self.format_ready.wait(2):
            if not self.stop.is_set():
                self.fail("ALSA did not confirm the capture sample rate")
            return
        try:
            while not self.stop.is_set():
                block = self.process.stdout.read(self.config.rate // 10 * 2)
                if not block:
                    if not self.stop.is_set():
                        self.metrics.add("capture_gaps")
                        self.fail("ALSA EOF: capture stopped; stream cannot continue")
                    break
                block = remainder + block
                complete = len(block) // 2 * 2
                remainder = block[complete:]
                if complete:
                    end = self.ring.append(block[:complete])
                    self.last_read = time.monotonic()
                    self.metrics.set(samples_captured=end)
        except (OSError, ValueError) as error:
            if not self.stop.is_set():
                self.metrics.add("capture_gaps")
                self.fail(f"ALSA read failed: {error}")

    def _errors(self):
        try:
            while not self.stop.is_set():
                line = self.process.stderr.readline(512)
                if not line:
                    break
                message = line.decode(errors="replace").strip()
                if message:
                    self.metrics.set(last_alsa_message=message)
                match = re.match(r"rate\s*:\s*(\d+)", message)
                if match and not self.format_ready.is_set():
                    # First setup is the application's outer PCM. Later setup
                    # blocks may describe a plug's different native device rate.
                    if int(match[1]) != self.config.rate:
                        self.fail(f"ALSA negotiated rate {match[1]}, expected {self.config.rate}")
                        self.format_ready.set()
                        break
                    self.format_ready.set()
                if any(word in message.lower() for word in (
                        "overrun", "underrun", "error", "suspend", "attempting recover",
                        "rate is not accurate")):
                    self.metrics.add("capture_gaps")
                    self.metrics.add("alsa_overruns", int("overrun" in message.lower()))
                    self.fail(f"ALSA continuity lost: {message}")
                    break
        except (OSError, ValueError):
            if not self.stop.is_set():
                self.fail("ALSA diagnostics pipe failed")

    def close(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        for thread in self.threads:
            thread.join(timeout=1)
        self.process.stdout.close()
        self.process.stderr.close()
