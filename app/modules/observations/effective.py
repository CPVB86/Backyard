"""Effective species projection; original BirdNET columns are never overwritten."""
from sqlalchemy import case, func
from app.modules.observations.models import Observation


def scientific_name(record):
    return (record.review or {}).get("scientific_name_override") or record.scientific_name


def species_expression():
    return func.coalesce(Observation.review["scientific_name_override"].as_string(),
                         Observation.scientific_name)


def name_expression():
    # Never attach the original species' English label to a corrected species.
    return case((Observation.review["scientific_name_override"].as_string().is_not(None),
                 species_expression()), else_=Observation.common_name)
