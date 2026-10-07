"""Explicit transactional backlog migration. Default is strictly read-only."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3

from app.core.config import Settings
from observations.policy import Policy, RawCandidate, decision, VERSION, classify, evidence_summary
from operations.environment import read_environment


def migrate(path, *, apply=False):
    mode = "rw" if apply else "ro"
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=" + mode, uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not apply:
            db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        transitions, outcomes = Counter(), Counter()
        changed = 0
        candidates = defaultdict(list)
        for row in db.execute("SELECT c.observation_id,c.raw FROM observation_candidates c JOIN observations o ON o.id=c.observation_id WHERE o.status IN ('pending_review','review_recommended') ORDER BY c.ordinal"):
            candidates[row[0]].append(RawCandidate(**json.loads(row[1])))
        for row in db.execute("SELECT * FROM observations WHERE status IN ('pending_review','review_recommended') ORDER BY id").fetchall():
            old = json.loads(row["decision"])
            if old.get("policy_version") == VERSION:
                continue
            support = candidates[row["id"]]
            if not support:
                raise ValueError("Missing candidate evidence for observation " + row["id"])
            policy = Policy(**json.loads(row["policy"]))
            outcome = decision(support, policy, new_observation=False)
            confidence, windows, state = evidence_summary(support, policy)
            classification, enough = classify(confidence, windows, state, policy,
                                               recommended=row["status"] == "review_recommended")
            if outcome["status"] == "discarded" or classification == "discarded":
                raise ValueError("Migration refuses to discard existing observation " + row["id"])
            outcome.update(classification=classification, evidence_qualified=enough)
            if classification == "human_review" and row["status"] == "review_recommended":
                outcome.update(status="review_recommended", evidence="permanent")
            transitions[row["status"] + " -> " + outcome["status"] + " / " + classification] += 1
            outcomes[classification] += 1
            changed += 1
            if apply:
                # Keep file/key in place: permanent is a retention contract, not a path.
                # No audio copying/deleting or provider calls inside the transaction.
                evidence = "permanent" if outcome["evidence"] == "permanent" else row["evidence_kind"]
                outcome["migration_previous_decision"] = old
                db.execute("UPDATE observations SET status=?,decision=?,policy=?,evidence_kind=?,review_due_at=? WHERE id=?",
                           (outcome["status"], json.dumps(outcome), json.dumps(asdict(policy)), evidence,
                            row["review_due_at"] if classification == "human_review" else None, row["id"]))
        if apply:
            db.commit()
        else:
            db.rollback()
        return {"apply": apply, "policy_version": VERSION, "would_change" if not apply else "changed": changed,
                "transitions": dict(transitions), "classifications": dict(outcomes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.environment_file:
        os.environ.update(read_environment(args.environment_file))
    print(json.dumps(migrate(Settings().resolved_database_path, apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
