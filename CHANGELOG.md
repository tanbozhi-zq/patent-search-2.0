# Changelog

All notable service changes are recorded in this file.

## [0.2.0] - 2026-07-23

### Changed

- Adapted search DSL generation to the OpenSearch v2 mapping.
- Removed the public `index_analyzer_mode` request parameter and console control.
- Made entity queries use phrase matching and keyword fields use exact terms.
- Changed IPC list matching to the direct `IPCList` keyword field.
- Removed obsolete analyzer compatibility scripts and tests.

### Upgrade Notes

- Clients must stop sending `index_analyzer_mode`.
- Deploy only after v2 read-only validation. The production read target remains
  unchanged until an alias cutover is separately approved.

### Rollback

- Redeploy the previous verified Git tag and retain `patent_index` as the read
  target until the v2 alias cutover is validated.
