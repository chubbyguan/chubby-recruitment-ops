---
name: recruitment-interview-review
description: Reconcile recruitment interviews across Calendar, video meetings, intelligent minutes, candidate records, and current job gates; prepare evidence-based interview briefs, validate original-transcript evaluations, surface missing artifacts and overdue human decisions, and sync factual interview status without treating a completed meeting as a pass. Use when a user asks who has been interviewed, requests interview advice or candidate feedback, wants calendar and minutes checked, or needs interview progress updated.
---

# Recruitment Interview Review

Use this skill only inside an initialized recruitment workspace. It supplements `$recruitment-ops-run`; it never changes a job gate or makes a final hiring decision.

## Load the rules

1. Read [interview-rules.md](references/interview-rules.md) completely.
2. Load `.recruitment-ops/config.json` and the current eligible-job readback.
3. Resolve the plugin root from this skill location and use scripts under `<plugin-root>/scripts/`.

## Choose a mode

- `brief`: prepare an interview brief from the current JD, verified resume facts, existing screening assessment, and unresolved evidence.
- `reconcile`: compare candidate records with Calendar, video meetings, and minutes.
- `evaluate`: score a completed interview from an original usable transcript.
- `backlog`: list completed interviews awaiting evaluation or a human decision.

## Reconcile mode

1. Verify the connector identity and access scope.
2. Read current candidate/application records. Do not trust chat messages as the sole status source.
3. Search the relevant Calendar window and capture stable event IDs, planned times, and status.
4. Resolve the linked video-meeting ID and actual start/end facts.
5. Fetch the original intelligent transcript. If the meeting detail has no transcript, search Minutes by meeting time and title as a fallback.
6. If no usable transcript exists, record `no_artifact`, `permission_denied`, `processing`, or `insufficient_transcript`; do not invent an evaluation.
7. Build an input matching `assets/interview-reconciliation.example.json` and run:

   ```bash
   python3 <plugin-root>/scripts/reconcile_interviews.py <input.json> --output <result.json>
   ```

8. Upsert interview facts, artifact references, and decision-queue actions by stable IDs. Store links and structured evidence references, not the full transcript.
9. Preserve `interviewed_job_id` separately from `current_matched_job_id`.
10. Treat name or alias overlap only as a merge-review signal. A human must approve a merge.

## Brief mode

Prepare:

- the interview role and current JD version;
- the candidate's strongest verified evidence;
- unresolved claims and missing work samples;
- five to eight questions tied to first-stage job outcomes;
- a role case and an AI live-demo prompt when relevant;
- prohibited protected-attribute topics.

Use `assets/interview-scorecard-template.md`.

## Evaluate mode

1. Confirm the meeting is completed and the artifact is usable.
2. Read the original transcript, not an AI-generated summary.
3. Evaluate only the role interviewed in this meeting. If the current matched role differs, produce a separate rematch review.
4. Cite transcript timestamps for every non-zero score.
5. Build an evaluation matching `assets/interview-evaluation.example.json` and run:

   ```bash
   python3 <plugin-root>/scripts/validate_interview_evaluation.py <evaluation.json>
   ```

6. Write the validated AI evaluation as `pending` human review.
7. Never write `passed`, `rejected`, `hire`, or `offer` from AI output.

## Backlog mode

Report separately:

- scheduled interviews;
- completed interviews with usable transcripts awaiting evaluation;
- completed interviews without usable artifacts;
- evaluations awaiting a human decision;
- decisions beyond the configured SLA;
- suspected duplicate candidates awaiting merge review.

The daily digest must show these counts even when there are no new resumes.
