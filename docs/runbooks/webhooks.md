# GitHub webhooks — waking the tick early

Set `DEVCLAW_WEBHOOK_SECRET` and point a repository webhook (any event) at
`POST /webhooks/github`. A delivery whose `X-Hub-Signature-256` verifies wakes
the goal tick at once; the world is then read fresh, so a comment mentioning
the bot, a check run or a push is acted on within seconds instead of at the
next 15-minute tick. Unset secret ⇒ the route answers 404. The heartbeat is
the fallback; nothing depends on the webhook.
