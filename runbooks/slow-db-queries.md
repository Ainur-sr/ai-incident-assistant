# Slow database queries

## Signals

Query execution latency rises and time accumulates in database spans. Possible hypotheses include lock contention, a changed query plan, or growing scanned row counts.

## Diagnostics

Identify affected query fingerprints from existing telemetry. Compare wait events, execution counts, rows scanned, and plans against an earlier baseline. Review lock-wait and transaction age metrics before attributing the issue to a plan change.
