# Database connection pool exhaustion

## Signals

Requests wait for a database connection and fail with pool checkout timeouts. Active connections approach the pool maximum, idle connections fall to zero, and pending borrowers increase.

## Diagnostics

Compare pool checkout latency and active, idle, and waiting counts by application instance. Inspect transaction age and connection hold time. Correlate the first waits with traffic and query latency to distinguish excess concurrency from leaked connections or slow transactions.
