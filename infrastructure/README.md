# V5.3 course publishing infrastructure (V5.2-compatible contract)

This directory is the protected, nonvisual Website Publishing System. It does not define a restaurant's layout, page count, colors, fonts, graphics, or style. The V5.3 course keeps the proven V5.2 repository/package contract for compatibility; this documentation change does not authorize an individual site to change schema or package versions.

These files are protected course infrastructure:

- `importer-policy.json` is the one shared public-tree and ZIP policy used by the validator, deterministic packager, and phone importer.
- `site-manifest.schema.json` is the closed V5.2 public manifest contract. Unknown fields are rejected.
- `legacy-url-plan.schema.json` closes the safe owner-approved handoff for old public URL paths, page purposes, and replacement handling.
- `starter-tree.sha256` allows starter-mode CI only for the byte-exact reviewed neutral safety shell. That shell is disposable and must be completely replaced by a custom production `public/` tree.
- `form-environment.template.txt` lists form and Turnstile variables without containing a credential.
- `infrastructure-version.json` identifies the infrastructure, workflow, manifest, policy, and business-assets contract versions.
- `ACTIVATION_REHEARSAL.md` covers account-dependent checks that local tests cannot prove.

Do not weaken a file, media, CSP, approval, privacy, or size rule to make one student package pass. Fix the public output, optimize the asset, or move approved heavy media to a proper delivery host.

The contract intentionally allows original static HTML, arbitrary truthful page structures, local CSS, JavaScript, SVG graphics, images, icons, and local font files. Validation checks safety, evidence, accessibility basics, SEO contracts, route preservation, and deployment readiness—not visual sameness. The creative-freedom regression fixture must remain structurally and stylistically unrelated to the waiting shell.

GitHub `main` is canonical and Cloudflare Pages is the final deployment target. ChatGPT Sites may be used only for a temporary owner preview and must never replace or be described as the final GitHub-to-Cloudflare launch.

The GitHub workflows pin official actions to full release commit SHAs verified for V5.2. Treat dependency update pull requests as infrastructure changes: review the upstream release, rerun local tests, and repeat the activation rehearsal before merging.
