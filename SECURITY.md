# Security policy

This is a public **code-only** repository. Running application instances, data, credentials, logs, secrets, backups and infrastructure remain private.

- Please avoid disclosing sensitive incident details in public issues; use GitHub private vulnerability reporting if enabled by the owner.
- Do not include real access tokens, hostnames, internal IP addresses or personally identifying material in examples, tests or workflow outputs.
- Workflows must use GitHub-hosted runners with `permissions: contents: read`. Never use `pull_request_target`, production secrets or self-hosted runners for untrusted PRs.
- Dependencies require a code, licensing and supply-chain review before adoption.
- If credentials are published, revoke and rotate them immediately. Deleting a commit cannot undo exposure.
- No automated deployment to personal computers, VPS or production services.
