"""Read-side classification also covers legacy backlog before migration."""
from observations.policy import Policy, RawCandidate, classify, evidence_summary


def category(record):
    if record.status in ("human_confirmed", "human_rejected"):
        return record.status
    if record.status == "auto_accepted":
        return "accepted"
    if "potential_otje_human_review" in record.decision.get("reasons", []):
        return "human_review"
    policy = Policy(**record.policy)
    candidates = [RawCandidate(**c.raw) for c in record.supports]
    if not candidates:
        return "unknown"
    confidence, windows, state = evidence_summary(candidates, policy)
    return classify(confidence, windows, state, policy,
                    recommended=record.status == "review_recommended")[0]
