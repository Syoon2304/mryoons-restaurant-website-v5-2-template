# V5.2 creator activation rehearsal

Complete this in a disposable private repository before releasing the starter. Local tests cannot prove GitHub permissions, Cloudflare account configuration, DNS, email delivery, Turnstile, or the public network.

## 1. Repository and starter gate

- Create the repository from the final starter and keep `main` as the default branch.
- Confirm Actions workflow permission is read/write.
- Run **Validate website source** and confirm the exact starter digest passes.
- Change one harmless byte under `public/` without changing the digest file; confirm CI rejects the modified starter. Revert that rehearsal commit normally.

## 2. Phone workflow

- In a disposable copy, replace `public/` with the known-good production fixture and create `website.zip` with `scripts/package_site.py`.
- Restore the reviewed starter `public/`, upload only the generated root `website.zip` through the same mobile-browser steps taught to students, and commit it.
- Confirm the phone workflow validates, atomically replaces `public/`, and creates the bot commit.
- Upload a wrapper-folder ZIP and a traversal/symlink malicious fixture; confirm each fails and the last good public site stays unchanged.

## 3. Desktop Codex workflow

- Open the repository as the desktop Codex project.
- Confirm Codex reads `AGENTS.md`, edits only `public/` and approved `handoff/` files, uses a branch, and runs production checks.
- Confirm the resulting pull request shows real website files rather than a binary-only change.

## 4. Cloudflare Pages

- Connect `main`, use repository root, build command `exit 0`, and output directory `public`.
- Confirm both static pages and `functions/api/health.js` deploy.
- Verify the custom HTTPS domain, redirects, headers, CSP, 404 behavior, canonical URLs, robots file, and sitemap on cellular data.

## 5. Forms and Turnstile, when enabled

- Onboard the sender domain in Cloudflare Email Service and verify the fixed recipient.
- Create a least-privilege Email Sending token; store it and the Turnstile secret only as encrypted deployment secrets.
- Set all variables from `form-environment.template.txt`.
- Replace the Turnstile site-key placeholder with the real public site key.
- Confirm the widget `data-action` exactly matches both the manifest `turnstile_action` and `TURNSTILE_EXPECTED_ACTION`; confirm `TURNSTILE_EXPECTED_HOSTNAME` is the exact production hostname.
- Submit a real test, confirm delivery to the fixed recipient, and confirm a wrong origin, wrong action, wrong hostname, missing consent, file upload, and oversized body fail safely.
- Confirm the visible phone/email fallback works and `/api/health` reports configured booleans without exposing values.

## 6. Live monitoring and rollback

- Set `SITE_URL` to the final HTTPS origin.
- Set `LIVE_CHECK_APPROVED_MEDIA_HOSTS` only when direct video or large downloads use creator-approved hosts.
- Run **Weekly live website health check** manually and confirm every required check passes.
- Run the rollback workflow with a known-good full commit SHA and confirm it creates a new validated commit without rewriting history.

## Release evidence

Record the rehearsal repository, date, tested action SHAs, Cloudflare project, production hostname, form-delivery result, live-check result, rollback result, and any intentionally disabled optional feature. Do not place secret values in the record.
