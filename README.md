# Restaurant Website V5.2 publishing starter

This repository is the public publishing half of the V5.2 restaurant website program. One protected contract supports both student launch routes:

- **Phone:** upload one complete, approved `website.zip` at the repository root. The phone workflow inspects it as untrusted input, validates it, stages it, and only then replaces `public/`.
- **Desktop Codex:** open the repository in Codex and edit the real website files under `public/`. Codex runs the same production policy before review.
- **Hosting:** Cloudflare Pages deploys `public/` plus the optional Pages Functions under `functions/`. It never deploys `website.zip`, `handoff/`, or the private business-assets source.

A rejected phone package never replaces the current site.

## The three boundaries

```text
private business-assets source      owner facts, originals, approvals; never commit
                |
                v
handoff/                            minimal approved public facts and decisions
                |
                v
public/                             the only deployable website tree

protected repository infrastructure
  .github/ functions/ infrastructure/ scripts/ tests/ templates/ AGENTS.md
```

The restaurant’s private business-assets folder belongs in the V5.2 program folder, outside this GitHub repository. Only approved public-ready assets belong in `public/`.

## Repository map

```text
.github/workflows/
  publish-phone-upload.yml       validate and import root website.zip
  validate-site.yml              production gates or exact reviewed starter shell
  rollback-website.yml           restore public/ from a known-good commit
  weekly-live-health-check.yml   bounded live website checks

public/                           public website files only
functions/api/                    optional Cloudflare Pages form endpoints
handoff/                          minimal approved public build context
infrastructure/                   protected V5.2 contracts and setup templates
scripts/                          validator, packager, importer, and live checker
tests/                            production and malicious-input regression tests
templates/                        implementation snippets for Codex
website.zip                       phone transport file; starter copy is nondeployable
```

## Phone publishing contract

The uploaded package must:

- be named exactly `website.zip` and be located at the repository root;
- contain the whole production website with `index.html` directly at ZIP root—never inside a wrapper folder;
- include every required root file listed in `infrastructure/importer-policy.json`;
- use manifest schema `2.1`, workflow `5.2`, and repository package spec `5.2`;
- declare each page's real `file_path` separately from its public `url_path` and `canonical_url_path`; when the public URL is not the file's natural static path, add the validator-required exact `200` rewrite in `_redirects`;
- declare every old public URL under `legacy_routes`, rebuild it at the same URL by default, and use only exact owner-approved `301` redirects when a redirect is genuinely necessary;
- include the complete declared favicon/app-icon family and the required icon links on every HTML page;
- declare every public PDF or calendar file under `public_documents` with its page, purpose, and owner approval;
- contain no symlink, hard link, nested archive, bundled video, secret, private business source, raw capture, office workbook, or protected program file;
- remain inside all compressed, expanded, file-count, file-type, and individual-file limits;
- pass the full production validator and an exact extraction check.

The included root `website.zip` is a deliberately invalid, exact course placeholder. The phone workflow recognizes only that reviewed placeholder as a clean waiting state. A student replaces it with the approved package produced by the course workflow; every changed package receives the complete import and production checks. Uploading an invalid package leaves the existing `public/` unchanged.

### Existing-site URL safety

`handoff/LEGACY_URL_PLAN.json` is the approved bridge between the old-site inventory and the public build. Production validation rejects missing rebuilt routes, redirect-to-home shortcuts, wildcard routes, unapproved redirects, loops, chains, and redirects whose exact source, destination, and `301` status do not match the manifest. This preserves ads, backlinks, bookmarks, printed QR codes, and customer expectations while allowing page files to be reorganized internally.

## Desktop Codex contract

Codex reads `AGENTS.md` and the approved files under `handoff/`, then edits `public/` directly on a branch. It must not copy the private business-assets source into GitHub. Before review, it updates the manifest and version together, runs the repository release check, inspects phone and desktop layouts, and reports owner-visible results.

## Creator setup

1. Create a new private repository from this starter and keep the default branch named `main`.
2. Enable GitHub Actions with read/write workflow permission. The phone importer needs `contents: write` to create the validated `public/` commit.
3. Keep `main` unprotected for the baseline phone course. GitHub mobile uploads cannot replace `website.zip` on a protected branch, and this workflow commits the validated extraction to `main`. A protected-branch edition needs a separately tested pull-request importer.
4. Connect Cloudflare Pages with repository root as the root directory, `exit 0` as the build command, and `public` as the build output directory.
5. Configure forms only if the website manifest enables them. Follow `infrastructure/form-environment.template.txt`; the Turnstile widget `data-action` and `TURNSTILE_EXPECTED_ACTION` must match exactly.
6. Set GitHub repository variable `SITE_URL` to the final HTTPS origin. If direct media or large downloads exist, set `LIVE_CHECK_APPROVED_MEDIA_HOSTS` to a comma-separated, creator-reviewed host allowlist.
7. Complete every step in `infrastructure/ACTIVATION_REHEARSAL.md` before distributing the repository to students.

The official checkout and Python setup actions are pinned to verified full release commit SHAs. Dependabot may propose updates, but a creator must review and rehearse them before merging.

## Local release checks

Run the complete repository-local gate:

```bash
python3 scripts/release_check.py
```

For an approved production site in `public/`:

```bash
python3 scripts/validate_site.py public --mode production --repo-root .
python3 scripts/package_site.py --source public --output website.zip --repo-root .
```

The packager creates a deterministic ZIP, imports it into a temporary directory, reruns production validation, and compares every extracted file before atomically replacing the old `website.zip`.

## Forms and Turnstile

The Pages Function accepts only bounded JSON or URL-encoded text. It rejects multipart/file uploads, compressed bodies, invalid length declarations, unapproved origins, and Turnstile results with the wrong hostname or action. Delivery goes only to creator-configured destinations through Cloudflare Email Service’s REST API or an approved HTTPS webhook.

Form activation is an external gate: verify the sender domain, recipient, least-privilege token, production hostname, widget site key, widget action, delivery, spam behavior, fallback contact route, and `/api/health` on the live site.

## Large media

Do not place video in `public/` or `website.zip`. Publish an owner-approved delivery copy through an approved host, keep an optimized local poster, declare the exact URL in `site-manifest.json`, and add only its exact host to CSP and the live-check allowlist. Private storage or file-sharing URLs are not public delivery URLs.

## Rollback

- For an immediate outage, use Cloudflare deployment history while investigating.
- For canonical source recovery, run **Roll back website files**, enter a full known-good commit SHA, and let the workflow validate and create a new commit.

Never force-push or erase history for a normal rollback.

## First safe state

The committed `public/` is a neutral, non-business starter shell. CI accepts starter mode only when every byte matches `infrastructure/starter-tree.sha256`. Any real website must use `stage: production` and pass all production gates.
