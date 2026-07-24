# Recruitment business rules

## North-star outcome

Optimize for qualified candidates who can enter a structured interview, not post count, likes, raw applications, or AI scores.

## Job gates

| Job state | Public action | Candidate action |
|---|---|---|
| open | Formal recruitment content | May be Top 1 and enter screening |
| conditional | Talent-mapping content with no offer promise | May be a talent-map suggestion only |
| frozen | No solicitation | Do not present as an active match |
| closed | No solicitation | Do not present as an active match |
| unknown | Stop for review | Do not guess |

The workflow may read status but must never change it.

## Xiaohongshu content structure

Prepare content from job outcomes, not by pasting a formal JD.

Each post should include:

1. a three-second hook;
2. why the opportunity exists now;
3. real responsibilities and first-stage outcomes;
4. evidence or portfolio required;
5. who is not suitable;
6. location and work mode;
7. the configured public-disclosure policy;
8. application email;
9. subject convention;
10. job ID and attribution code;
11. a short candidate-data notice.

Useful angles are project opportunity, first-stage outcomes, and case challenge.

## Mail processing and deduplication

Deduplicate in this order:

1. stable Gmail `message_id`;
2. attachment SHA-256;
3. privacy-preserving normalized contact hash when the department has approved it;
4. name plus current company plus current role as a weak duplicate hint;
5. human confirmation before merging records.

Same name is never enough to overwrite or merge.

Atomically claim each message before downloading or parsing. Keep the returned fencing token and require it on completion or failure. Use a short lease so an interrupted `processing` record can be reclaimed after expiry. Allow bounded retries for `failed` records; never reclaim terminal `success`, `duplicate`, or `manual_review` records automatically. A stale worker must not complete after another worker receives a new token.

## Matching output

When the explicit target job is missing or unsuitable, return up to three jobs with:

- stable job ID;
- match confidence;
- matched hard requirements;
- missing or conflicting requirements;
- evidence from the resume or portfolio;
- explanation for lower-ranked alternatives.

Rank active jobs first. Conditional jobs must be labeled talent mapping. Never turn a frozen or closed job into an active match.

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

A high score does not override missing work samples or conflicting facts. When information is incomplete, prefer `request_more_info` over forcing a low score.

## Protected attributes

Do not extract or score age, date of birth, sex, gender, photo or appearance, marital or fertility status, ethnicity, nationality, native place, household registration, disability, religion, political affiliation, or family information.

Contact details may be used only for authorized recruiting communication, not scoring.

## Channel metrics

Use only real values:

- publications;
- views or reads when available;
- likes, saves, and comments;
- engagement rate = `(likes + saves + comments) / reads`;
- attributed applications;
- qualified candidates;
- application and qualified-candidate conversion when the denominator exists.

When reads are unavailable, do not calculate click-through or conversion. Display unavailable.

## Human approval points

Require a human before:

- opening or closing a job;
- publishing, editing, or deleting a post;
- merging suspected duplicate candidates;
- rejecting or inviting a candidate;
- sending a background check or offer;
- changing compensation or job gate;
- forwarding a resume outside authorized recruiting access.

## Daily schedule

Default operating window is configurable. A common pattern is inbox processing every two hours between 09:00 and 21:00 and one digest at 19:00 in the department timezone.

Do not create or activate a schedule until connector probes and synthetic acceptance pass.
