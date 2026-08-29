# Infrastructure changelog

## restaurant-v5.2-infrastructure-2.1.0 - 2026-08-29

- Upgraded the public manifest to `2.1` and the workflow/repository package contract to `5.2`.
- Separated each page's internal `file_path` from its public and canonical URL paths.
- Added closed, typed old-URL handoff and public-manifest contracts.
- Required every old route to be rebuilt at the same URL or covered by one exact owner-approved permanent redirect.
- Rejected wildcard-to-home rules, undeclared redirects, route collisions, redirect chains, and redirect loops.
- Added a complete SVG/ICO/Apple/PWA favicon family plus file, dimension, HTML-head, and webmanifest validation.

## restaurant-v5.1-infrastructure-2.0.0 - 2026-08-28

- Unified the phone `website.zip` route and desktop `public/` route behind one V5.1 manifest, version, and public-file policy.
- Rejected symbolic links, hard links, special files, resolution escapes, noncanonical paths, portability collisions, private-source names, and protected-folder content.
- Added deterministic package creation with an importer round trip before atomically replacing the prior `website.zip`.
- Made production validation strict: schema and version parity, same-origin page/canonical/sitemap rules, exact CSP hosts, public-boundary scanning, JavaScript syntax, approvals, and Turnstile widget actions.
- Hardened contact-body limits and Turnstile hostname/action verification.
- Hardened live checks against SSRF, DNS rebinding, unsafe redirects, private addresses, oversized responses, and unapproved media hosts.
- Pinned official GitHub actions to verified release commit SHAs and added an exact digest exception for the neutral starter shell.
- Added activation rehearsal instructions and adversarial regression tests.

## Legacy restaurant-v3-infrastructure-1.0.0 - 2026-08-27

- Added safe phone `website.zip` importer with atomic replacement.
- Added production website validator and deterministic package builder.
- Added real-file Codex workflow through `AGENTS.md`, handoff contracts, validation, and pull requests.
- Added GitHub Actions for phone import, source validation, rollback, and weekly live checks.
- Added Cloudflare Pages Functions for fixed-recipient forms with Turnstile and Email Service API/webhook delivery routes.
- Added starter shell and known-good restaurant production fixture.
- Added Python and Node tests plus malicious-package fixture matrix.
- Added strict external-media manifest/CSP validation for direct video, Stream, YouTube, Vimeo, and large downloads.
- Added actual compressed-size, expanded-size, single-file-size, and file-count enforcement tests.
- Added multipart rejection for the contact endpoint so file uploads cannot bypass the bounded form contract.
- Added Cloudflare Pages, forms, media, domain, monitoring, and support documentation.
