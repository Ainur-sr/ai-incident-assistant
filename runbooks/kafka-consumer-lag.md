# Kafka consumer lag

## Signals

Consumer group lag increases when the gap between partition end offsets and committed offsets grows. A skewed partition, rebalance, or slow downstream dependency can limit throughput.

## Diagnostics

Compare production and consumption rates, lag per partition, and offset commit progress. Inspect rebalance frequency, partition assignments, handler latency, and downstream errors during the same window.
