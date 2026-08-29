# Webhooks runbook — event-driven triggers (spec 023)

One authenticated route: `POST /webhooks/github`. Events WAKE the existing
machinery (a trigger-named goal-log line + an in-process poke); grading runs
on issue opened/edited. The heartbeat stays the complete fallback — with
webhooks broken, everything still happens on tick cadence.

## Enable on the instance

1. Generate a secret: `openssl rand -hex 32`.
2. Add `DEVCLAW_WEBHOOK_SECRET=<secret>` to the compose env file
   (`/srv/devclaw/.env`) and recreate the devclaw project (or ride the next
   deploy). Unset secret ⇒ the route answers 404 — the feature is off.
3. Expose ONLY the webhook path publicly via Tailscale Funnel (ruled
   2026-08-29 — the rest of the HTTP surface stays tailnet-internal):

   ```bash
   tailscale funnel --bg --set-path /webhooks/github http://127.0.0.1:18791/webhooks/github
   tailscale funnel status   # note the public https URL
   ```

## Wire each repo (all four lifekit repos)

```bash
gh api repos/lifekit-hq/<repo>/hooks -f name=web -F active=true \
  -f "events[]=issues" -f "events[]=pull_request" \
  -f "events[]=check_run" -f "events[]=check_suite" \
  -f config[url]="https://<funnel-host>/webhooks/github" \
  -f config[content_type]=json -f config[secret]="<secret>"
```

GitHub's ping delivery should answer 200; a bad secret shows as 401 in the
repo's webhook delivery log.

## Verify

- Merge any PR on a registered repo → the affected goal's log gains
  `event: pull_request/closed … — advancing now (webhook)` within seconds and
  the tick fires immediately (instead of up to 15 minutes later).
- Open a deliberately thin issue → the readiness label + gap comment land
  without any manual `regrade_intake` call.

## Failure posture

- Webhook infra down ⇒ behavior degrades to tick latency only (FR-002); no
  correctness change.
- Unregistered-repo deliveries are dropped with a logged reason; bad
  signatures are counted 401s; a crashing payload never reaches the tick loop.
