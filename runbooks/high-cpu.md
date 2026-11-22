# High CPU usage

## Signals

CPU utilization, CPU quota throttling, or runnable queue length increases while request latency rises. Spikes can come from hot loops, increased traffic, or background work.

## Diagnostics

Compare CPU and request rates per replica. Inspect throttling, time spent in user and system code, and an existing CPU profile. Correlate hot functions with traffic shape or a newly scheduled workload.
