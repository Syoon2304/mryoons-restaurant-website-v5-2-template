# Approved public build handoff

`handoff/` is a narrow bridge from the private V5.2 business-assets source to the public GitHub build. It is not a backup or mirror of the owner’s assets.

Only record the minimum facts and decisions that the owner has approved for public website use:

1. Complete the owner interview and readiness checks in the V5.2 program folder.
2. Resolve conflicts and obtain explicit approval for imported facts.
3. Obtain approval for one website direction and its Design Lock.
4. Populate `APPROVED_BUILD_BRIEF.md`, `DESIGN_LOCK.json`, `PUBLIC_BUSINESS_FACTS.json`, and `LEGACY_URL_PLAN.json` with public-ready information only. For an existing site, record every old public URL and its approved same-URL rebuild or exact redirect.
5. Let desktop Codex read those files under `AGENTS.md`, edit `public/`, and run production gates.

Never place originals, raw captures, private records, interview notes, credentials, customer data, or the full business-assets source here.

Phone students do not edit `handoff/`; their approved `website.zip` already contains only the finished public website.
