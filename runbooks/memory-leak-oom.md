# Memory leak and out-of-memory termination

## Signals

Resident memory or heap usage grows over time, or containers restart with OOMKilled. Increasing retained heap after garbage collection suggests a leak. A stable baseline with a sharp peak suggests transient pressure.

## Diagnostics

Compare RSS, post-collection heap, container memory limits, and termination timestamps. Review existing heap profiles or allocation metrics for growing object types. Check workload size and concurrency around each termination.
