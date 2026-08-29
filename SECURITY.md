# Security and privacy boundary

This repository must contain only public-ready website assets and the minimal approved handoff needed to build them.

Do not commit passwords, API tokens, Turnstile secret keys, webhook secrets, private keys, customer submissions, the private business-assets source, raw business evidence, employee records, permit scans, invoices, or unpublished material.

If a secret is committed:

1. Disable or rotate it immediately in the service that issued it.
2. Stop publishing until the affected integration is confirmed safe.
3. Preserve the incident details and identify which commits/deployments were affected.
4. Remove the value from the current repository state. History cleanup is a separate expert operation and does not replace rotation.
5. Run the repository checks and complete the incident playbook before resuming.

Website availability problems use the rollback workflow and Cloudflare deployment history. Never send credentials through course support.
