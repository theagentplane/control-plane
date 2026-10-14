## Summary

<!-- What changes and why? One thing per pull request. -->

## Related issue

<!-- e.g. Closes #123 -->

## Compatibility

- [ ] Compatible (additive only)
- [ ] Breaking (migration noted below and in CHANGELOG.md)

<!-- If an endpoint, a response field, a setting or the schema changed: which clients (Chronicle,
     TokenOps, the web page) are affected and what must each do to migrate? -->

## Database

- [ ] No schema change
- [ ] New Alembic revision (never edit an applied one); upgrade tested on an existing database

## Checklist

- [ ] Change is under about 400 lines, or the description links the split plan or says why it is one change
- [ ] Tests added or updated
- [ ] CHANGELOG entry added and classified as compatible or breaking
- [ ] `ruff`, `mypy` and `pytest` pass locally
- [ ] Every query is tenant-scoped; no dialect-specific SQL outside the engine factory
- [ ] Primitives models are imported, not copied
- [ ] Commits are signed off for the [DCO](https://developercertificate.org/) (`git commit -s`)
