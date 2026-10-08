"""Domain-neutral species assets and single-writer, retry-safe generation."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from generator.jobs import Jobs


class GenerationConflict(RuntimeError):
    pass


class UnsupportedDomain(ValueError):
    pass


def species_key(scientific_name):
    if not isinstance(scientific_name, str) or not scientific_name.strip() or len(scientific_name) > 255:
        raise ValueError("A scientific_name of 1..255 characters is required")
    return hashlib.sha256(scientific_name.strip().encode()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    temporary.replace(path)


class AssetStore:
    def __init__(self, root, adapters=None):
        self.root = Path(root).resolve()
        self.jobs = Jobs(self.root / "jobs.sqlite3")
        if adapters is None:
            from generator.domains.birds.adapter import Birds
            adapters = {"bird": Birds(), "bat": None}
        self.adapters = adapters

    def configuration_reason(self, domain):
        adapter = self.adapter(domain)
        if adapter is None:
            return "domain_not_configured"
        check = getattr(adapter, "configuration_reason", None)
        return check() if check else None

    def adapter(self, domain):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", domain) or domain not in self.adapters:
            raise UnsupportedDomain("Unsupported generator domain")
        return self.adapters[domain]

    def directory(self, domain, scientific_name):
        self.adapter(domain)
        path = (self.root / domain / species_key(scientific_name)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Asset path escapes generator storage")
        return path

    def manifest(self, domain, scientific_name):
        path = self.directory(domain, scientific_name) / "manifest.json"
        if not path.exists():
            return {"domain": domain, "scientific_name": scientific_name, "assets": {}}
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("domain") != domain or value.get("scientific_name") != scientific_name:
            raise ValueError("Generator manifest identity mismatch")
        return value

    def lookup(self, domain, scientific_name):
        scientific_name = scientific_name.strip()
        adapter = self.adapter(domain)
        directory = self.directory(domain, scientific_name)
        manifest = self.manifest(domain, scientific_name)
        assets = dict(adapter.bundled(scientific_name)) if adapter else {}
        for asset_id, metadata in manifest["assets"].items():
            path = (directory / metadata["file"]).resolve()
            if not path.is_relative_to(directory):
                raise ValueError("Asset path escapes species storage")
            if adapter and adapter.valid(path, metadata):
                assets[asset_id] = dict(metadata, path=path)
        required = adapter.required_assets if adapter else ()
        missing = [asset_id for asset_id in required if asset_id not in assets]
        status = "not_configured" if adapter is None else "ready" if not missing else "partial" if assets else "missing"
        state_path = directory / "state.json"
        attempts = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        generation = None if status == "ready" else self.jobs.get(domain, scientific_name)
        if status == "ready":
            generation = {"status": "complete", "reason": None}
        elif generation is None or generation["status"] == "complete":
            if any(key in attempts for key in missing):
                generation = {"status": "generation_failed", "reason": "previous_attempt_requires_review"}
            else:
                reason = self.configuration_reason(domain)
                generation = {"status": "generation_not_configured" if reason else "incomplete", "reason": reason}
        return {"generation": generation, "domain": domain, "scientific_name": scientific_name, "species_key": species_key(scientific_name),
                "status": status, "assets": assets, "missing_assets": missing, "attempts": attempts}

    def resolve_key(self, domain, key):
        adapter = self.adapter(domain)
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid species asset key")
        if adapter:
            for name in adapter.identities():
                if species_key(name) == key:
                    return self.lookup(domain, name)
        directory = (self.root / domain / key).resolve()
        if not directory.is_relative_to(self.root):
            raise ValueError("Asset path escapes storage")
        manifest = directory / "manifest.json"
        if manifest.exists():
            name = json.loads(manifest.read_text(encoding="utf-8"))["scientific_name"]
            if species_key(name) != key:
                raise ValueError("Species key mismatch")
            return self.lookup(domain, name)
        return None

    def register_existing(self, domain, scientific_name, asset_id, filename, pose):
        scientific_name = scientific_name.strip()
        adapter = self.adapter(domain)
        if adapter is None or not hasattr(adapter, "register_existing"):
            raise UnsupportedDomain("This domain cannot register existing assets")
        adapter.validate_species(scientific_name)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", asset_id):
            raise ValueError("Invalid asset id")
        if Path(filename).name != filename:
            raise ValueError("Asset filename must not contain a path")
        directory = self.directory(domain, scientific_name)
        path = (directory / filename).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Asset path escapes species storage")
        manifest = self.manifest(domain, scientific_name)
        current = manifest["assets"].get(asset_id)
        if current is not None and current.get("file") != filename:
            raise GenerationConflict("Asset id is already registered to another file")
        manifest["assets"][asset_id] = adapter.register_existing(path, pose)
        write_json(directory / "manifest.json", manifest)
        return self.lookup(domain, scientific_name)

    @contextmanager
    def generation_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / "generation.lock"
        try:
            handle = lock.open("x")
        except FileExistsError:
            raise GenerationConflict("Another generation holds generation.lock; inspect stale locks before manual removal") from None
        try:
            import os
            handle.write(str(os.getpid())); handle.flush()
            yield
        finally:
            handle.close()
            lock.unlink(missing_ok=True)

    def ensure(self, domain, scientific_name, common_name, *, retry_failed=False, options=None):
        scientific_name = scientific_name.strip()
        adapter = self.adapter(domain)
        if adapter is None:
            raise UnsupportedDomain("This domain has no generator configured yet")
        adapter.validate_species(scientific_name)
        if not self.lookup(domain, scientific_name)["missing_assets"]:
            return self.lookup(domain, scientific_name)
        with self.generation_lock():
            current = self.lookup(domain, scientific_name)
            directory = self.directory(domain, scientific_name)
            directory.mkdir(parents=True, exist_ok=True)
            manifest = self.manifest(domain, scientific_name)
            attempts = current["attempts"]
            for asset_id in current["missing_assets"]:
                if asset_id in attempts and not retry_failed:
                    raise GenerationConflict("Previous attempt exists; inspect provider usage/raw output before explicit retry")
                attempts[asset_id] = {"status": "running", "at": datetime.now(timezone.utc).isoformat()}
                write_json(directory / "state.json", attempts)
                try:
                    metadata = adapter.generate(scientific_name, common_name, asset_id, directory, options or {})
                    manifest["assets"][asset_id] = metadata
                    write_json(directory / "manifest.json", manifest)
                except Exception:
                    attempts[asset_id]["status"] = "failed"
                    write_json(directory / "state.json", attempts)
                    raise
                attempts[asset_id]["status"] = "ready"
                write_json(directory / "state.json", attempts)
            return self.lookup(domain, scientific_name)


def public_status(result):
    value = dict(result)
    value["assets"] = {}
    for asset_id, asset in result["assets"].items():
        value["assets"][asset_id] = {key: item for key, item in asset.items() if key not in ("path", "file")}
        value["assets"][asset_id]["url"] = f"/api/generator/{result['domain']}/assets/{result['species_key']}/{asset_id}"
    return value
