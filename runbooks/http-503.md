# HTTP 503 Service Unavailable

## Signals

Clients receive HTTP 503 from a gateway, load balancer, or application. The response may reflect zero healthy upstreams, overload, or a readiness check failure.

## Diagnostics

Identify which hop emits 503 using request IDs and response metadata. Compare gateway upstream error counts, healthy target counts, readiness transitions, and application saturation during the incident.
