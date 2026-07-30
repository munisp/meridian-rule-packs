# ci/ — dual-path CI mirror (HARDENING H6)

`ci/workflows/validate.yml` validates every rule pack against
`schemas/rulepack.schema.json` (`tools/validate.py`) and checks ed25519
signature presence/integrity on published packs.

H6 push rule: attempt the push to `.github/workflows/validate.yml` first; if
the GitHub API rejects it with a workflow-scope error (403/422), the workflow
lives here and a maintainer with the `workflow` scope moves it:

```sh
mkdir -p .github/workflows && cp ci/workflows/validate.yml .github/workflows/validate.yml
```
