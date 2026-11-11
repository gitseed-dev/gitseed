## Summary

Briefly describe the problem and the changes introduced in this pull request.

## Verification

- [ ] `uv run python -m pytest` passes
- [ ] `uv run ruff check .` and `uv run mypy packages/common apps/core apps/cli apps/mcp apps/runner` pass
- [ ] `pnpm --filter web lint`, `pnpm --filter web test`, and `pnpm --filter web build` pass
- [ ] Database migrations in `apps/core/src/gitseed_core/migrations/versions/` match SQLModel changes (if applicable)

> *First-time external contributors: our CLA bot will post a 1-line comment on your PR to sign [`CLA.md`](../CLA.md).*


