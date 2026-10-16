# Recruitment business rules

## North-star outcome

Optimize for qualified candidates who can enter a structured interview, not post count, likes, raw applications, or AI scores. Track `campaign -> publication -> attribution -> application -> qualified candidate` as one chain.

Continue the chain through `interview scheduled -> meeting completed -> original transcript available -> AI advisory evaluation -> human decision`. These are separate states.

## Job gates

| Job state | Public action | Candidate action |
|---|---|---|
| open | Formal recruitment content | May be Top 1 and enter screening |
| conditional | Talent-mapping content with no offer promise | May be a talent-map suggestion only |
| frozen | No solicitation | Do not present as an active match |
| closed | No solicitation | Do not present as an active match |
| unknown | Stop for review | Do not guess |

The workflow may read status but must never change it.

## Public company and compensation facts

- The approved public brand for this installation is read from `company.public_name`; the current default is `诺特兰德集团`.
- The legal employing entity must come from the current job source. Never infer it from the public brand.
- Public salary must come verbatim from the job's approved public salary field and carry an approval reference.
- Do not divide employer cost, copy a neighboring role, convert annual budget, or ask the model to estimate a range.
- Missing salary range, explicit approval, or approval reference blocks `ready_to_publish`.

## Campaign and channel content

Prepare content from job outcomes, not by pasting a formal JD.

Each post should include:

1. a three-second hook;
2. why the opportunity exists now;
3. real responsibilities and first-stage outcomes;
4. evidence or portfolio required;
5. who is not suitable;
6. location and work mode;
7. the approved company name and salary range;
8. application email;
9. subject convention;
10. job ID and attribution code;
11. a short candidate-data notice.

Useful angles are project opportunity, first-stage outcomes, and case challenge. Adapt each approved fact set to the channel:

- Xiaohongshu: scene-led image post and card script;
- WeChat groups or Moments: concise referral copy and clear forwarding reason;
- WeChat Channels, Douyin, or Bilibili: spoken script and shot list;
- Zhihu, Maimai, or LinkedIn: professional context, role impact, and evidence standard.

Every Campaign needs an explicit recruitment cycle ID. Every content variant needs a stable ID, channel-native asset script, and human approval bound to its content SHA-256. Every publication needs a stable publication ID, attribution code, manual status, and real URL after publication.

Before `ready_to_publish`, retain a human approval record tied to the current job version, salary approval reference, and exact content fingerprint. The local hash detects source drift but does not authenticate the collaboration connector; live connector readback supplies that trust boundary.

## Mail processing and deduplication

Deduplicate in this order:

1. stable Gmail `message_id`;
2. attachment SHA-256;
3. privacy-preserving normalized contact hash when the workspace has approved it;
4. name plus current company plus current role as a weak duplicate hint;
5. human confirmation before merging records.

Same name is never enough to overwrite or merge.

Aliases, a reused email, or a chat nickname may create a duplicate-review action, but only an authorized human may merge candidate records.

Atomically claim each message before downloading or parsing. Keep the returned fencing token and require it on completion or failure. Use a short lease so an interrupted `processing` record can be reclaimed after expiry. Allow bounded retries for `failed` records; never reclaim terminal `success`, `duplicate`, or `manual_review` records automatically. A stale worker must not complete after another worker receives a new token.

## Matching output

When the explicit target job is missing or unsuitable, return up to three jobs from the current eligible-job readback with:

- stable job ID;
- current job version and gate;
- match confidence;
- matched hard-requirement IDs;
- missing or conflicting requirement IDs;
- evidence reference IDs pointing to authorized source records;
- explanation for lower-ranked alternatives.

Rank active jobs first. Conditional jobs must be labeled talent mapping. Never turn a frozen or closed job into an active match. Validate the assessment against the current eligible-job snapshot; a stale version or mismatched formal/talent-mapping mode blocks the result.

## Screening rubric

Score only job-relevant evidence:

| Dimension | Weight |
|---|---:|
| Direct relevance to first-stage outcomes | 30 |
| Hard skills and work samples | 25 |
| Business judgment and ownership | 15 |
| AI collaboration, fact-checking, and correction | 15 |
| Collaboration, writing, and learning speed | 10 |
| Credibility and information completeness | 5 |

Recommendations:

- `hr_screen`: normally total >= 75, sufficient evidence, no confirmed red line;
- `request_more_info`: evidence is missing or cannot be verified;
- `manual_review`: conflicting facts, uncertain red line, or 60–74 range;
- `talent_pool`: low current fit after human review, or a conditional opportunity;

Do not output `reject`, `hire`, `offer`, or automatic interview actions.

The validated assessment is a structured decision index, not a narrative. It may contain only stable requirement/evidence reference IDs and the approved evidence, red-flag, and missing-material codes defined by `validate_assessment.py`. Keep original resume excerpts in the authorized source system, never in the assessment or digest payload.

A high score does not override missing work samples or conflicting facts. When information is incomplete, prefer `request_more_info` over forcing a low score.

## High-score candidate interview recommendation

Run this stage after inbox processing and again when producing the decision digest.

- Default recommendation threshold is 80, but score alone is never sufficient.
- Require an open current job version, a traceable candidate ID, a usable resume, complete evidence references, `hr_screen`, no unresolved missing materials, and no pending duplicate review.
- Exclude candidates with a formal interview record or a non-canceled Calendar Event.
- A screening-import row is not proof of an interview.
- When the candidate stage says interview but interview and Calendar facts are both absent, create a fact-confirmation warning before any invitation.
- Conditional jobs are talent mapping only. Frozen, closed, filled, or unknown jobs cannot generate interview recommendations.
- Build the recommendation idempotency key from candidate ID, job/version, score, action, evidence, risks, and missing-material codes.
- Send only new or changed keys. Create a pending human decision action and store the IM message ID.
- Never auto-invite, schedule, reject, or mark the candidate passed.

## Protected attributes

Do not extract or score age, date of birth, sex, gender, photo or appearance, marital or fertility status, ethnicity, nationality, native place, household registration, disability, religion, political affiliation, or family information.

Contact details may be used only for authorized recruiting communication, not scoring.

## Interview facts and evaluation

Use `$recruitment-interview-review` for interview work.

- Calendar proves what was planned; video-meeting facts prove whether a meeting happened.
- Search Minutes as a fallback when meeting details do not link a transcript.
- Use the original transcript for scoring. An AI summary is insufficient.
- Record `interviewed_job_id` separately from the latest `current_matched_job_id`.
- `completed` means the meeting happened, never that the candidate passed.
- If the transcript is absent, inaccessible, processing, or too short, record the reason and request a human interviewer note.
- Store stable evidence references and timestamps, not complete transcript text, in Base.
- Generate the advisory evaluation within the configured SLA and keep the item visible until a human decision is recorded.

## Channel metrics

Use only real, non-negative, finite values:

- publications;
- views or reads when available;
- likes, saves, and comments;
- engagement rate = `(likes + saves + comments) / reads`;
- attributed applications;
- qualified candidates;
- application and qualified-candidate conversion when the denominator exists.

When reads or views are unavailable, do not calculate click-through or application conversion. Display unavailable. Do not treat likes as clicks or exposures.

## Digest privacy model

The digest may contain candidate aliases, stable job/publication IDs, numbers, and approved enum codes only. Candidate evidence, risks, interview suggestions, and anomalies must be mapped to the fixed labels in `render_digest.py`. Never send resume excerpts, locations, names, addresses, account handles, or other candidate free text to the digest renderer.

## Human approval points

Require a human before:

- opening or closing a job;
- publishing, editing, or deleting a post;
- merging suspected duplicate candidates;
- rejecting or inviting a candidate;
- marking an interview passed or closed;
- sending a background check or offer;
- changing compensation or job gate;
- forwarding a resume outside authorized recruiting access.

## Daily schedule

Default operating window is configurable. A common pattern is inbox processing every two hours between 09:00 and 21:00 and one digest at 19:00 in the workspace timezone.

Do not create or activate a schedule until connector probes and synthetic acceptance pass.
