# Demo deployment

A demo needs three things: the web app, the API, and a public HTTPS address for the phone line. The container recipe, the environment and the checks are in [`deploy/README.md`](../deploy/README.md); this page is the demo-day checklist on top of it. Nothing here is tied to one hosting provider.

## Shape

| Piece | Where | Notes |
| --- | --- | --- |
| Web app | any static or Node host, or the container | Build with `NEXT_PUBLIC_API_URL` set to the API's address |
| API | one container, one replica | Holds `GNANI_API_KEY`, the language model key and the Vobiz keys as secrets |
| Phone-only port (8021) | a public HTTPS address | The only port Vobiz and web-call visitors reach |

Every model is a hosted service (Gnani for speech, a hosted language model for replies and search), so the API container is small and starts in seconds. The laptop is only needed if you choose to serve the phone line from it; then it must stay awake and the tunnel must stay up.

## Who may call the API

- **Single account, for a demo with no real data:** generate a token (`openssl rand -hex 24`), set it as `VAKYA_LOCAL_TOKEN` on the API and as `NEXT_PUBLIC_VAKYA_LOCAL_TOKEN` on the web app. The token is bundled into the site, so anyone who opens the site can use the API as that one account and read everything in it. Rotate it afterwards.
- **Hosted multi-user mode:** the three Supabase settings (`backend/supabase/README.md`). Every request then needs a signed-in user. Tested against fakes only.

Without either, the API refuses requests that do not arrive directly on the machine, because traffic through a proxy cannot be told from the internet.

## Checklist before presenting

1. `GET /api/ready` answers `gnani_configured: true` and every lane `ready` (`docs/GNANI.md`).
2. Run `backend/bench/gnani_smoke.py` once with the key; fix anything it reports.
3. Open the web-call link on a phone off your network and talk to the agent; correct a digit on purpose.
4. Place one call through the Vobiz number. A trial account only calls verified numbers.
5. Watch the credit budget on Settings: a demo day spends credits, and the reserve is kept for live calls.
6. Take down any tunnel to the main API port as soon as the demo is over. Only the phone-only port is meant to face the internet.

## What to expect

- **Gnani limits.** One active request at a time and a credit budget. When Gnani is busy the caller hears a short held line and is offered a callback; this is deliberate and is logged.
- **Model limits.** The language model's provider sets rate limits per key; a long demo day can hit them. Use a paid key for a live demo.
- **No persistence without a volume.** Mount a volume on the API's data directory, or agents and files are lost when the container is replaced.
- **Not yet proven.** This path has not been run with a live Gnani key or a live Vobiz number.
