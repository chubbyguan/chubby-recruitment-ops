# Integration contracts

## Configuration ownership

Create one `.recruitment-ops/` directory per business workspace. It contains local configuration, state, generated drafts, and reports. It must not be committed.

Use `<plugin-root>/assets/config.example.json` as the schema source. Keep secrets in the connector or operating system credential store, never in JSON.

## Gmail capability contract

The selected adapter must support:

- verify the active account;
- search by query, time, label, unread state, or message ID;
- read message headers and text body;
- list and download attachments;
- return stable Gmail message and thread IDs;
- optionally label a successfully processed message.

Do not mark mail processed before candidate and log writes succeed.

## Feishu capability contract

The selected adapter must support:

- inspect Base tables and fields;
- search records by stable keys;
- create and update records;
- upload an attachment when policy allows;
- send a message to a configured private chat or restricted group;
- read back the written record and sent message for acceptance.

Logical table names must be mapped in config. Never bake a department's App Token, Table ID, Chat ID, or Open ID into the plugin.

## Interview capability contract

The selected Feishu adapters must support:

- search Calendar events by participant and time window;
- return stable event IDs and planned start/end;
- inspect the linked video meeting and return stable meeting IDs plus actual start/end;
- fetch the original intelligent transcript when available;
- search Minutes by time and title when the meeting does not expose a transcript link;
- distinguish missing, processing, permission-denied, insufficient, and usable artifacts.

Base authorization alone does not prove Calendar, video-meeting, or Minutes access. Probe each capability separately.

## Social channel capability contract

The default adapter is human operation:

- the plugin prepares channel-native copy, card or video scripts, tags, and attribution codes;
- a human performs final review and publication;
- the human or an approved read-only integration records URL and metrics;
- missing analytics remain explicitly unavailable.

Do not use browser automation to publish or mass-edit posts on any social platform.

## Job source contract

Every job must expose:

- stable job ID;
- source record ID, source revision, modified time, and live readback time;
- title and department;
- status or gate;
- JD version;
- location and work mode;
- hiring owner and business reviewer;
- hard requirements;
- 30/60/90-day outcomes or equivalent first-stage results;
- evidence or portfolio requirements;
- red lines;
- approved public company name and legal employing entity;
- approved public salary range, approval flag, and approval reference;
- salary approval actor and approval time;
- whether formal recruiting or talent mapping is allowed.

Unknown status blocks publication and formal matching.

Every matching run must use a current eligible-job snapshot containing job ID, job version, and gate. A stale version, a frozen or closed gate, or a formal/talent-mapping mode conflict blocks the assessment.

## Base minimum model

Use `<plugin-root>/assets/feishu-base-schema.json` as the logical model.

The candidate table remains the sole candidate master. Applications are separate job-level facts. Interview sessions preserve the role actually interviewed. Artifact and evaluation tables store references and structured scores, not full transcripts. Campaign, content, publication, metric, attribution, mail, decision, and run tables contain process facts only.

## Acceptance evidence

Keep:

- job-source record, revision, modified time, readback time, and canonical hash;
- content fingerprint, human approval record ID, and approval readback result;
- synthetic Gmail message ID and attachment hash;
- assessment JSON and validation result;
- created candidate record ID;
- duplicate replay result;
- synthetic Calendar event, meeting, and transcript readback result;
- interview reconciliation and validated evaluation result;
- completed interview without usable transcript remaining unevaluated;
- run log record ID;
- digest message ID and readback result.

Delete the synthetic candidate and attachment after acceptance unless the department explicitly keeps test records in a clearly marked test area.
