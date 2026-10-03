"""One background worker; no provider work in observation requests."""
import logging
from threading import Event, Thread
from generator.store import GenerationConflict
from generator.errors import GenerationNotConfigured

logger = logging.getLogger("backyard.generator")


class Scheduler:
    def __init__(self, store):
        self.store = store
        self.jobs = store.jobs
        self.wake = Event()
        self.stopping = Event()
        self.thread = None

    def start(self):
        try:
            self.jobs.initialize()
            self.jobs.recover()
            self.thread = Thread(target=self.run, name="backyard-generator", daemon=True)
            self.thread.start()
        except Exception:
            # Do not expose filesystem/provider/credential-bearing exception text.
            logger.error("Generator scheduler unavailable; observations remain enabled")

    def stop(self):
        self.stopping.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=1)

    def accepted(self, observation):
        if observation.get("status") not in {"auto_accepted", "human_confirmed"}:
            return
        try:
            domain, name = observation["domain"], observation["scientific_name"]
            current = self.store.lookup(domain, name)
            if current["status"] == "ready":
                return
            self.jobs.enqueue(domain, name, observation["common_name"])
            self.wake.set()
        except Exception:
            logger.error("Generator scheduling failed; accepted observation retained")

    def run_one(self):
        job = self.jobs.claim()
        if not job:
            return False
        domain, name = job["domain"], job["scientific_name"]
        try:
            current = self.store.lookup(domain, name)
            if current["status"] == "ready":
                self.jobs.set(domain, name, "complete")
                return True
            reason = self.store.configuration_reason(domain)
            if reason:
                self.jobs.set(domain, name, "generation_not_configured", reason)
                return True
            self.store.ensure(domain, name, job["common_name"])
            self.jobs.set(domain, name, "complete")
        except GenerationNotConfigured:
            self.jobs.set(domain, name, "generation_not_configured", "provider_credentials_rejected")
        except GenerationConflict:
            # CLI may own the existing generation lock. Safe to check again later;
            # prior paid attempts still cannot be repeated by ensure().
            state = self.store.lookup(domain, name)
            if any(key in state["attempts"] for key in state["missing_assets"]):
                self.jobs.set(domain, name, "generation_failed", "previous_attempt_requires_review")
            else:
                self.jobs.set(domain, name, "generation_pending", "generator_busy")
        except Exception:
            self.jobs.set(domain, name, "generation_failed", "generation_error")
            logger.warning("Generator job failed; accepted observations retained")
        return True

    def run(self):
        while not self.stopping.is_set():
            self.wake.clear()
            try:
                worked = self.run_one()
            except Exception:
                worked = False
                logger.error("Generator queue unavailable; observations remain enabled")
            # No busy loop on lock contention; bounded memory, one provider call at a time.
            self.wake.wait(0.1 if worked else 1)
