# Repository instructions

- Keep the repository free of credentials, account identifiers, real resumes, and candidate PII.
- Preserve manual approval for Xiaohongshu publishing, candidate rejection, interviews, background checks, offers, and job-status changes.
- Use only Python standard-library dependencies in runtime scripts unless a change is explicitly approved.
- Run `python3 plugins/chubby-recruitment-ops/scripts/verify_acceptance.py` and the unit tests before publishing. Treat this as local synthetic validation, not live integration acceptance.
- Update both Codex and Claude Code manifests when plugin identity or capabilities change.
