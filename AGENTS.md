# Codex operating instructions — Restaurant Website V5.2

These instructions apply to every Codex task in this repository.

## Mission and authority

Build and maintain a truthful, distinctive, accessible, fast restaurant website. Keep implementation complexity away from the owner, but show every owner decision, uncertainty, failed gate, and publishing risk.

Use evidence in this order:

1. Explicit owner approval in the current task.
2. `handoff/APPROVED_BUILD_BRIEF.md` and `handoff/DESIGN_LOCK.json`.
3. Approved public facts in `handoff/PUBLIC_BUSINESS_FACTS.json`.
4. The current production website in `public/`.
5. The connected V5.2 Restaurant Website Starter Pack and private business-assets source, when available in the current context.

Never invent a business fact. A missing or conflicting fact is a blocker for that claim, not permission to guess.

## Path boundaries

Ordinary website work may edit:

- `public/`;
- the four `handoff/` files, but only when the owner has approved the public facts, old URL plan, or decisions being recorded.

Do not edit these protected paths unless the task explicitly requests course-infrastructure maintenance:

- `.github/`
- `functions/`
- `infrastructure/`
- `scripts/`
- `templates/`
- `tests/`
- `website.zip`
- `AGENTS.md`

Never commit the private business-assets source, raw review evidence, owner interview notes, originals, licenses, permits, invoices, staff or customer data, unpublished menus, source-of-truth records, credentials, or private keys.

## Build contract

- Work on a branch and request review. Do not push ordinary desktop website work directly to `main`.
- Edit real files in `public/`; the root ZIP is only the approved phone transport route.
- Preserve the owner-approved Design Lock unless the owner approves a redesign.
- When replacing an existing website, use `handoff/LEGACY_URL_PLAN.json` and preserve every old public URL at the same path by default. Use a permanent redirect only when its exact source and destination are owner-approved. Never send a collection of old pages to the homepage.
- Treat menu items, prices, hours, locations, phone numbers, service modes, links, awards, dietary claims, and testimonials as evidence-sensitive.
- Keep the primary action obvious at phone and desktop sizes.
- Use semantic HTML, keyboard access, visible focus, readable contrast, useful alt text, reduced-motion behavior, responsive layouts, and one clear `h1` per page.
- Keep menus readable as HTML. A PDF may be secondary, never the only menu.
- Declare every public PDF or calendar file in the manifest `public_documents` list with an existing link, purpose, and owner approval.
- Keep video external and declare every exact external media URL, accessibility treatment, owner approval, poster, page, and CSP host.
- Do not add analytics, advertising pixels, chat widgets, embeds, or marketing forms without owner approval and the necessary privacy decision.
- Keep form recipients and secrets in the deployment environment. A Turnstile widget action must exactly match `TURNSTILE_EXPECTED_ACTION`.

For every production change, keep `site-manifest.json` and `version.json` synchronized. Schema version is `2.1`, workflow version is `5.2`, and repository package spec is `5.2`. Each manifest page uses a canonical relative `file_path` plus root-relative `url_path` and `canonical_url_path`. Keep the complete generated favicon family declared and linked from every HTML page.

## Required gate before review

From the repository root, run:

```bash
python3 scripts/release_check.py
python3 scripts/validate_site.py public --mode production --repo-root .
```

Also inspect the site at common phone and desktop widths. Test navigation, click-to-call, directions, ordering or reservations, menu, forms, keyboard use, reduced motion, and the 404 page. If forms or external media are enabled, state that live delivery/reachability remains pending until its activation rehearsal passes.

## Review handoff

Before asking the owner to merge:

1. Summarize the visible change in plain language.
2. List every changed business fact and its approval source.
3. Provide current phone and desktop screenshots of changed page types.
4. Report exact automated and interaction test results.
5. Identify deferred optional work and every external live gate.
6. Include a rollback note for risky changes.

Do not say the website is complete while any required approval, production check, or live activation gate is unresolved.
