## Owner-visible result

Describe what changed in plain language.

## Business facts changed

- None, or list each fact that changed and its source.

## Visual evidence

- Desktop screenshot:
- Phone screenshot:
- Important interaction recording or notes:

## Quality checks

- [ ] `python3 scripts/release_check.py`
- [ ] `python3 scripts/validate_site.py public --mode production --repo-root .`
- [ ] Pages Functions pass `node --check`
- [ ] No private business-assets files, owner interview notes, or raw evidence were added
- [ ] `site-manifest.json` and `version.json` use the synchronized V5.2 contract
- [ ] Every old public URL is rebuilt at the same path or has one exact approved `301` redirect
- [ ] The favicon family, webmanifest, and required HTML head links pass validation
- [ ] No large video file was bundled in `public/`
- [ ] Links, forms, hours, phone, address, order/reservation actions, and menu were checked
- [ ] Owner approved the result shown in the screenshots

## Rollback note

Record the previous known-good commit or deployment if this change is high risk.
