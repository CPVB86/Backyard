"""Central species lookup and read-only composed detail view model."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.species_names import localized_name
from app.modules.birds.storage import audio_path
from app.modules.observations.models import Observation
from app.modules.species.models import Species
from generator.store import public_status

ACCEPTED = ("auto_accepted", "human_confirmed")
LOCALES = ("nl", "en", "de")


def lookup(engine, domain, scientific_name):
    with Session(engine) as session:
        return session.scalar(select(Species).where(
            Species.domain == domain, Species.scientific_name == scientific_name.strip()))


def _observation_names(session, domain, scientific_name):
    return list(session.scalars(select(Observation.common_name).where(
        Observation.domain == domain, Observation.scientific_name == scientific_name,
        Observation.status.in_(ACCEPTED)).order_by(Observation.start_at.desc())))


def detail(engine, settings, generator, domain, scientific_name, locale="nl", now=None,
           identity_override=None):
    if domain not in ("bird", "bat") or locale not in LOCALES:
        raise ValueError("Unsupported domain or locale")
    scientific_name = scientific_name.strip()
    if not scientific_name:
        raise ValueError("scientific_name is required")
    current = now or datetime.now(timezone.utc)
    start_today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    with Session(engine) as session:
        catalog = session.scalar(select(Species).where(
            Species.domain == domain, Species.scientific_name == scientific_name))
        filters = (Observation.domain == domain, Observation.scientific_name == scientific_name,
                   Observation.status.in_(ACCEPTED))
        if identity_override is not None:
            filters += (Observation.review["identity_override"].as_string() == identity_override,)
        totals = session.execute(select(
            func.count(Observation.id), func.min(Observation.start_at), func.max(Observation.start_at),
            func.max(Observation.best_confidence)).where(*filters)).one()
        today = session.scalar(select(func.count(Observation.id)).where(*filters, Observation.start_at >= start_today))
        week = session.scalar(select(func.count(Observation.id)).where(*filters, Observation.start_at >= current - timedelta(days=7)))
        names = _observation_names(session, domain, scientific_name)
        audio_record = session.scalar(select(Observation).where(
            *filters, Observation.audio.is_not(None), Observation.storage_key.is_not(None),
            Observation.evidence_kind == "permanent").order_by(
                Observation.best_confidence.desc(), Observation.start_at.desc(), Observation.id.asc()))
    if catalog is None and not totals[0]:
        return None
    english = next((name.strip() for name in names if name and name.strip()), None)
    common_nl = catalog.common_name_nl if catalog else localized_name(scientific_name, english, "nl")
    common_de = localized_name(scientific_name, english, "de")
    selected = common_nl if locale == "nl" else common_de if locale == "de" else english
    selected = selected or english or scientific_name
    generated = public_status(generator.lookup(domain, scientific_name))
    audio = None
    if audio_record:
        available = bool(audio_record.audio and audio_record.audio.get("status") == "available"
                         and audio_path(settings.resolved_storage_root, audio_record.storage_key).is_file())
        audio = {"observation_id": audio_record.id, "timestamp": audio_record.start_at,
                 "confidence": audio_record.best_confidence, "availability": available,
                 "url": f"/api/observations/{audio_record.id}/audio" if available else None}
    return {
        "identity": {"domain": domain, "scientific_name": scientific_name, "common_name": selected,
                     "common_name_nl": common_nl, "common_name_en": english,
                     "common_name_de": common_de, "authority": catalog.authority if catalog else None,
                     "family": catalog.family if catalog else None,
                     "taxon_type": catalog.taxon_type if catalog else None,
                     "taxon_group": catalog.taxon_group if catalog else None,
                     "parent_source_species_id": catalog.parent_source_species_id if catalog else None},
        "waarneming": None if catalog is None else {
            "species_id": catalog.source_species_id, "url": catalog.source_url,
            "rarity": catalog.rarity, "status": catalog.status, "obscurity": catalog.obscurity,
            "source": catalog.source, "imported_at": catalog.imported_at, "updated_at": catalog.updated_at},
        "observations": {"total": totals[0], "today": today, "last_7_days": week,
                         "first_observed_at": totals[1], "last_observed_at": totals[2],
                         "highest_confidence": totals[3]},
        "audio": audio,
        "generator": {"status": generated["status"], "generation": generated.get("generation"),
                      "missing_assets": generated["missing_assets"], "assets": generated["assets"]},
        "encyclopedia": {"wikipedia_nl_url": catalog.wikipedia_nl_url if catalog else None,
                         "wikipedia_title_nl": catalog.wikipedia_title_nl if catalog else None,
                         "wikipedia_match_status": catalog.wikipedia_match_status if catalog else None,
                         "wikipedia_match_note": catalog.wikipedia_match_note if catalog else None,
                         "wikipedia_en_url": catalog.wikipedia_en_url if catalog else None,
                         "wikipedia_title_en": catalog.wikipedia_title_en if catalog else None,
                         "summary_nl": catalog.summary_nl if catalog else None,
                         "fact_nl": catalog.fact_nl if catalog else None,
                         "source": catalog.encyclopedia_source if catalog else None},
    }
