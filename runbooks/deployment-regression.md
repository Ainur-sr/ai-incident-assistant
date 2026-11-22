# Deployment regression

## Signals

Errors or latency increase after a release, configuration change, or feature flag update. Affected requests may cluster on the new version while the previous version is healthy.

## Diagnostics

Compare error rates, latency, and traffic mix by version and canary cohort. Align release timestamps with first symptoms, then inspect recent changes and matching request traces. Check for concurrent dependency incidents before attributing causality to the rollout.
