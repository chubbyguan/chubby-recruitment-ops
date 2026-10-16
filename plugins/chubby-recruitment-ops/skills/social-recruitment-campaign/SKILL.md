---
name: social-recruitment-campaign
description: Build, review, or optimize a traceable multi-channel recruitment campaign from an approved job, including company and salary provenance, channel-native Xiaohongshu, WeChat, video, or professional-network content, attribution codes, manual publication queues, and real metric windows. Use when a user asks to turn a JD into recruitment posts, prepare content for social channels, create a campaign, review a publication package, or improve recruiting-channel conversion.
---

# Social Recruitment Campaign

Create one campaign from one approved job version. The campaign is the unit of planning, attribution, publication, and quality feedback.

## Load context

1. Locate `.recruitment-ops/config.json`; otherwise use `$recruitment-ops-setup`.
2. Read `../../assets/channel-playbook.md` and `../../assets/social-campaign-template.md` completely.
3. Read the live job source. Never use an old copied JD when a newer approved version exists.

## Build the campaign

1. Confirm the job gate:
   - `open`: formal recruitment;
   - `conditional`: talent mapping only;
   - `frozen`, `closed`, or unknown: stop.
2. Confirm these public facts from the same job version:
   - public company name equals config;
   - legal employing entity is present;
   - public salary range is present;
   - salary publication approval is exactly `true`;
   - approval reference, actor, and time are present;
   - source record ID, revision, modified time, and live readback time are present.
3. Assign an explicit recruitment cycle ID. Reusing the same cycle ID is idempotent; a new cycle must use a new ID. Export the job as structured JSON and run:

   ```bash
   python3 <plugin-root>/scripts/build_campaign.py --job <job.json> --campaign-key <cycle-id> --config <workspace>/.recruitment-ops/config.json --output <campaign.json>
   ```

4. Draft channel-native title, body, and asset script into each `content_variants` record. Never edit generated IDs or provenance fields. Each `publication_queue` item references exactly one content version.
5. For every version include exact `公司：批准名称` and `薪资：批准范围` markers, location/work mode, hiring reason, first-stage outcomes, hard requirements, required evidence, unsuitable profiles, privacy notice, job ID, approved application email, and attribution code. The validator treats title, body, and every asset-script line as one public payload.
6. Do not reuse identical copy across all channels. Adapt the expression, length, and asset script without changing facts.
7. Send the content fingerprint to the authorized human review record. Record approval ID, reviewer, review time, job version, salary approval reference, and approved content SHA-256 on that content version. Then change the linked publication to `ready_to_publish` and run:

   ```bash
   python3 <plugin-root>/scripts/validate_campaign.py --campaign <campaign.json> --job <current-job.json> --config <workspace>/.recruitment-ops/config.json --ready
   ```

## Publication and feedback

- A human publishes each item and records the real URL, time, and publisher.
- Never use browser automation to publish, edit, or delete.
- Capture real metrics at configured windows. Keep missing data as unavailable.
- Attribute applications from the explicit subject code or Plus Alias first.
- Compare variants using attributed applications, qualified candidates, and interviews.
- Feed quality findings back into the next content angle; never optimize for likes alone.

## Hard stops

- Do not estimate, normalize, or derive salary.
- Do not promise a formal opening for a conditional job.
- Do not expose internal budget, employer cost, approvals, candidate data, or unpublished business facts.
- Do not mark content published without a real URL and timestamp.
- The local validator detects drift and verifies the current readback envelope; it cannot authenticate the collaboration connector by itself. Production approval still requires a live connector readback of the source and approval records.
