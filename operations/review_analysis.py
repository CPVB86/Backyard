"""Read-only backlog diagnosis. Provisional simulations, never policy or DB updates."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import os
from pathlib import Path
import sqlite3

from app.core.config import Settings
from operations.environment import read_environment


def band(confidence):
    for upper, label in ((.60,"<60%"),(.65,"60-65%"),(.70,"65-70%"),(.75,"70-75%"),
                         (.80,"75-80%"),(.85,"80-85%"),(.90,"85-90%")):
        if confidence < upper:
            return label
    return ">=90%"


def simulate(row, thresholds=None):
    from observations.policy import classify, Policy, EVIDENCE_THRESHOLDS
    if thresholds is not None and tuple(thresholds) != EVIDENCE_THRESHOLDS:
        raise ValueError("Use the shared production evidence thresholds")
    category, enough = classify(row["confidence"], row["supports"], row["plausibility"],
                               Policy(**row.get("original_policy", {})),
                               recommended=row.get("original_status") == "review_recommended")
    if category == "discarded":
        raise ValueError("Unusual record below existing review thresholds: cannot classify safely")
    reasons = ["plausibility_unusual"] if category == "human_review" else []
    if category == "human_review" and row.get("original_status") == "review_recommended":
        reasons.append("review_recommended")
    return enough, category, reasons


def clusters(rows, seconds=300):
    result, active = [], {}
    for row in sorted(rows,key=lambda r:(r["start_at"],r["id"])):
        key = (row["domain"],row["scientific_name"])
        at = datetime.fromisoformat(row["start_at"])
        previous = active.get(key)
        if previous is None or (at-datetime.fromisoformat(previous["first"])).total_seconds() > seconds:
            previous = {"domain":key[0],"scientific_name":key[1],"first":row["start_at"],
                        "last":row["start_at"],"detections":0,"highest_confidence":0,"max_supports":0,
                        "reasons":[],"best_audio_observation_id":None,"_rank":(-1,-1)}
            result.append(previous); active[key] = previous
        previous["last"] = max(previous["last"],row["end_at"])
        previous["detections"] += 1
        previous["highest_confidence"] = max(previous["highest_confidence"],row["confidence"])
        previous["max_supports"] = max(previous["max_supports"],row["supports"])
        previous["reasons"] = sorted(set(previous["reasons"]) | set(row["proposed_reasons"]))
        rank = (row["confidence"],row["supports"])
        if row["audio_available"] and rank > previous["_rank"]:
            previous["best_audio_observation_id"] = row["id"]; previous["_rank"] = rank
    return [{k:v for k,v in row.items() if k != "_rank"} for row in result]


def analyze(path, *, domain="bird", cluster_seconds=300, thresholds=(.85,.75,.65)):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        statuses = {row[0]:row[1] for row in db.execute(
            "SELECT status,count(*) FROM observations WHERE domain=? GROUP BY status",(domain,))}
        history = {r[0]:dict(first=r[1],accepted=r[2],human_confirmed=r[3]) for r in db.execute(
            "SELECT scientific_name,min(start_at),sum(status IN ('auto_accepted','human_confirmed')),"
            "sum(status='human_confirmed') FROM observations WHERE domain=? GROUP BY scientific_name",(domain,))}
        candidates = defaultdict(list)
        for r in db.execute("SELECT c.observation_id,c.raw FROM observation_candidates c JOIN observations o "
                            "ON o.id=c.observation_id WHERE o.domain=? AND o.status IN ('pending_review','review_recommended')",(domain,)):
            candidates[r[0]].append(json.loads(r[1]))
        source = db.execute("SELECT o.*, s.rarity AS catalog_rarity,s.status AS catalog_status, "
                            "s.common_name_nl AS catalog_name,s.taxon_type AS catalog_type "
                            "FROM observations o LEFT JOIN species_catalog s ON o.domain=s.domain "
                            "AND o.scientific_name=s.scientific_name WHERE o.domain=? "
                            "AND o.status IN ('pending_review','review_recommended') ORDER BY o.start_at,o.id",(domain,)).fetchall()
    rows = []
    for r in source:
        decision, policy = json.loads(r["decision"]), json.loads(r["policy"])
        support = candidates[r["id"]]
        states = {c.get("plausibility",{}).get("state","unknown") for c in support}
        state = "unusual" if "unusual" in states else "unknown" if "unknown" in states or not states else "normal"
        stored = decision.get("plausibility", "unknown")
        audio = json.loads(r["audio"]) if r["audio"] else None
        row = {"id":r["id"],"domain":r["domain"],"scientific_name":r["scientific_name"],
               "common_name_nl":r["catalog_name"],"confidence":r["best_confidence"],
               "raw_support_count":len(support),
               "supports":len({c["window_id"] for c in support if c.get("confidence",0) >= policy.get("review_lower",.60)}),
               "plausibility":state,"stored_plausibility":stored,"signal_conflict":({"normal", "unusual"}.issubset(states) or
                   state in ("normal", "unusual") and stored in ("normal", "unusual") and state != stored),
               "original_status":r["status"],
               "rarity":r["catalog_rarity"],"catalog_status":r["catalog_status"],"taxon_type":r["catalog_type"],
               "start_at":r["start_at"],"end_at":r["end_at"],"original_reasons":decision.get("reasons",[]),
               "original_policy":policy,"history":history[r["scientific_name"]],
               "audio_available":bool(audio and audio.get("status")=="available" and r["storage_key"]),
               "geo_signals":[c.get("plausibility", {}) for c in support],
               "geo_providers":sorted({c.get("plausibility",{}).get("provider","unknown") for c in support})}
        qualifies, category, reasons = simulate(row,thresholds)
        row.update(basis_threshold_pass=qualifies,category=category,proposed_reasons=reasons)
        rows.append(row)
    report = summarize(rows, statuses, cluster_seconds)
    report.update(domain=domain, evidence_thresholds=list(thresholds), cluster_seconds=cluster_seconds)
    return report


def local_day(value):
    at = datetime.fromisoformat(value)
    if at.tzinfo is None: at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(ZoneInfo("Europe/Amsterdam")).date().isoformat()


def summarize(rows, statuses, cluster_seconds=300):
    categories = ("accepted", "low_evidence", "human_review", "unknown")
    subsets = {key:[r for r in rows if r["category"] == key] for key in categories}
    grouped = clusters(subsets["human_review"], cluster_seconds)
    days = {}
    for row in rows:
        day = local_day(row["start_at"])
        entry = days.setdefault(day, dict(date=day, detections=0, accepted=0, low_evidence=0,
                                         human_review=0, unknown=0, reviewclusters=0))
        entry["detections"] += 1
        entry[row["category"]] += 1
    for group in grouped:
        days[local_day(group["first"])]["reviewclusters"] += 1
    def distribution(subset):
        species = Counter(r["scientific_name"] for r in subset)
        names = {r["scientific_name"]:r["common_name_nl"] for r in subset}
        return {
            "count":len(subset),
            "plausibility":dict(Counter(r["plausibility"] for r in subset)),
            "confidence_bands":dict(Counter(band(r["confidence"]) for r in subset)),
            "supports":dict(Counter(str(r["supports"]) for r in subset)),
            "confidence_support_joint":dict(Counter(band(r["confidence"])+" / supports="+str(r["supports"]) for r in subset)),
            "top_species":[dict(scientific_name=s, common_name_nl=names[s], count=n) for s,n in species.most_common(20)],
            "rarity_context_only":dict(Counter(r["rarity"] or "missing" for r in subset))}
    def diverse(subset, limit=10):
        buckets = defaultdict(list)
        for r in sorted(subset, key=lambda r:(-r["confidence"],r["id"])):
            buckets[r["scientific_name"]].append(r)
        result = []
        while buckets and len(result) < limit:
            for name in list(buckets):
                result.append(buckets[name].pop(0))
                if not buckets[name]: del buckets[name]
                if len(result) >= limit: break
        return result
    strong_unknown = [r for r in subsets["unknown"] if r["basis_threshold_pass"] and r["plausibility"] == "unknown"]
    def cause(row):
        providers = set(row["geo_providers"])
        if providers == {"disabled"}: return "disabled"
        if providers - {"disabled", "unknown", ""}: return "provider_present_no_usable_result"
        return "missing_or_other"
    for row in subsets["unknown"]:
        row["unknown_cause"] = cause(row)

    return {
        "read_only":True, "simulation_only":True, "version":3,
        "snapshot_utc":datetime.now(timezone.utc).isoformat(),
        "scope":"Current selected-domain pending_review plus review_recommended backlog, not all historical detections",
        "all_status_counts":statuses, "total_analyzed":len(rows),
        "totals":{k:len(v) for k,v in subsets.items()}, "human_review_clusters":len(grouped),
        "human_review_reasons_nonexclusive":dict(Counter(reason for r in subsets["human_review"] for reason in r["proposed_reasons"])),
        "plausibility":dict(Counter(r["plausibility"] for r in rows)),
        "by_category":{k:distribution(v) for k,v in subsets.items()},
        "daily_timezone":"Europe/Amsterdam", "daily":[days[d] for d in sorted(days)],
        "strong_unknown":{
            **distribution(strong_unknown),
            "species":dict(Counter(r["scientific_name"] for r in strong_unknown)),
            "provider_causes":dict(Counter(cause(r) for r in strong_unknown)),
            "records":strong_unknown},
        "unknown_causes":dict(Counter(r["unknown_cause"] for r in subsets["unknown"])),
        "pending_review_no_longer_human_review":sum(r["original_status"] == "pending_review" and r["category"] != "human_review" for r in rows),
        "review_recommended_remaining_human_review":sum(r["original_status"] == "review_recommended" and r["category"] == "human_review" for r in rows),
        "strong_unknown_geo_providers":dict(Counter(p for r in strong_unknown for p in r["geo_providers"])),
        "original_reasons_inventory":dict(Counter(reason for r in rows for reason in r["original_reasons"])),
        "samples":{
            "accepted":diverse(subsets["accepted"]), "low_evidence":diverse(subsets["low_evidence"]),
            "human_review":subsets["human_review"],
            "all_unusual":[r for r in rows if "unusual" in (r["plausibility"],r["stored_plausibility"])],
            "all_review_recommended":[r for r in rows if r["original_status"] == "review_recommended"],
            "unknown":sorted(subsets["unknown"],key=lambda r:(-r["confidence"],r["id"]))[:20]},
        "clusters":grouped,
        "records":rows,
        "data_quality":{
            "stored_raw_plausibility_mismatches":[r["id"] for r in rows if r["plausibility"] != r["stored_plausibility"]],
            "known_signal_conflicts":[r["id"] for r in rows if r["signal_conflict"]]},
        "limits":[
            "Unknown is never accepted or human review; strong_unknown is diagnostic only",
            "Rarity and missing catalog entries never trigger review",
            "No provider calls, no historical geography recomputation, no database or audio writes",
            "Supports count qualifying overlapping windows, not independent calls",
            "Daily totals describe surviving current backlog, not complete historical workload; edge days may be partial",
            "Clusters use domain/species, anchored first start, maximum start span equals cluster_seconds, attributed to first local calendar day",
            "Confidence and support maxima from different records are never combined for acceptance",
            "History fields are context only and may include later observations; not used for decisions",
            "All human review, unusual and review_recommended records are included",
            "Unrecognized original reasons are inventoried, not invented as anomaly signals"
        ]}


def main():
    parser = argparse.ArgumentParser(description="Read-only four-outcome backlog simulation; no apply mode")
    parser.add_argument("--environment-file", type=Path)
    args = parser.parse_args()
    if args.environment_file: os.environ.update(read_environment(args.environment_file))
    print(json.dumps(analyze(Settings().resolved_database_path), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
