## Summary

<!-- 1-3 bullet points describing what changed and why. -->

-

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (existing behaviour changes)
- [ ] Documentation / comments only
- [ ] Refactor (no functional change)
- [ ] Chore / dependency update

## Affected packages

<!-- Check every package this PR touches. -->

- [ ] qx-core
- [ ] qx-di
- [ ] qx-cqrs
- [ ] qx-db
- [ ] qx-http
- [ ] qx-observability
- [ ] qx-events
- [ ] qx-worker
- [ ] qx-cache
- [ ] qx-auth
- [ ] qx-grpc
- [ ] qx-search
- [ ] qx-testing
- [ ] qx-cli
- [ ] qx-devtools
- [ ] examples/identity-service

## Test plan

- [ ] `uv run pytest packages/ examples/ -q` passes locally
- [ ] `./scripts/check.sh` (ruff + mypy + tests) passes
- [ ] New behaviour is covered by a test
- [ ] Integration tests pass (if persistence / HTTP layer is touched)

## Breaking change checklist

<!-- Complete only if "Breaking change" is checked above. -->

- [ ] CHANGELOG updated
- [ ] Migration path documented in PR description
- [ ] Version bump(s) noted

## Notes for reviewers

<!-- Anything that needs extra attention, context, or a follow-up. -->
