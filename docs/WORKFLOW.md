# Development workflow / Przepływ prac

**Operator:** decides on deployments, credentials, and publication. GitHub authentication is managed from the designated primary workstation.

**Vexi:** planning, code and security review, documentation, PR coordination and CI verification.

**Codex:** small, scoped implementation issues, feature branches and PRs; never given infrastructure secrets or direct production access.

**GitHub Actions:** ephemeral GitHub-hosted runners for public code checks only. No secrets, no self-hosted runners, and no access to a personal computer or private data.

**PR gate:** scope, validation, risks, rollback, review. Workflows do not deploy.

**Host gate:** complete the read-only host preflight, validate existing services and a recovery point, then obtain operator approval before any isolated deployment.
