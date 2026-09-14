# Contract: the token counter on `/metrics` (P3)

Appended to the existing exposition (`devclaw/server/routes/metrics.py`), computed on scrape from
`StateStore.usage_totals()`:

```
# HELP devclaw_tokens_total Tokens the sandbox sessions reported, summed over all recorded sessions.
# TYPE devclaw_tokens_total counter
devclaw_tokens_total{kind="input"} 0
devclaw_tokens_total{kind="output"} 0
devclaw_tokens_total{kind="cache_read"} 0
devclaw_tokens_total{kind="cache_creation"} 0
# HELP devclaw_sessions_total Recorded sessions.
# TYPE devclaw_sessions_total gauge
devclaw_sessions_total 0
# HELP devclaw_sessions_reported_usage Recorded sessions that carried a usage block.
# TYPE devclaw_sessions_reported_usage gauge
devclaw_sessions_reported_usage 0
```

Tasks are never pruned, so the counter is monotonic; a database restore reads as a counter reset,
which Prometheus handles. The Grafana panel (lifekit-stack) is `increase(devclaw_tokens_total[1d])`
by `kind` — that is the per-day history the spec moved out of the console.
