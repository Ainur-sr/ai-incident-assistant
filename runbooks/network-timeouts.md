# Network timeouts

## Signals

Requests fail with connection timeout, TLS handshake timeout, or deadline exceeded. A timeout can occur during DNS resolution, TCP connect, TLS negotiation, or response wait.

## Diagnostics

Use request traces to locate the phase consuming the timeout budget. Compare failures across clients, zones, and destinations. Inspect existing DNS timing, retransmission, packet loss, and upstream response metrics.
