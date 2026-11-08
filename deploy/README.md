# Deploying Vakya in containers

Vakya is two containers and one public HTTPS address. Any container host will do: a VM with Docker, a managed container service, or a Kubernetes cluster. Nothing here depends on one provider.

| Container | Image | Port | What it serves |
| --- | --- | --- | --- |
| API | `backend/Dockerfile` | 8020 | The REST API the web app calls. Keep it private or behind your sign-in. |
| Phone-only port | the same image | 8021 | Only the call stream, the answer document, the hangup callback, the missed-call link and the web-call page. This is the one Vobiz reaches. |
| Web app | `frontend/Dockerfile` | 3010 | The site, the desk and `/verify`. |

No model is downloaded and no accelerator is needed. Speech goes to Gnani, replies go to a hosted language model and document search to hosted embeddings, and calls go through Vobiz, all over HTTPS from the API container.

## 1. Build and run

```bash
docker compose up --build
```

`docker-compose.yml` at the repository root starts the API, the web app and a database. To use your own host instead, build the two images and run them with the environment below:

```bash
docker build -t vakya-api ./backend
docker build -t vakya-web --build-arg NEXT_PUBLIC_API_URL=https://api.example.in ./frontend
docker run -d --name vakya-api -p 8020:8020 -p 8021:8021 --env-file .env -v vakya-data:/app/data vakya-api
docker run -d --name vakya-web -p 3010:3010 vakya-web
```

`NEXT_PUBLIC_API_URL` is baked into the web app at build time: set it to the address the browser will use for the API.

## 2. Environment

Copy `.env.example` to `.env` and fill it. The settings that matter:

| Setting | Purpose |
| --- | --- |
| `GNANI_API_KEY` | Gnani Prisma, Timbre and voice cloning. Without it nothing can hear or speak. |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | The hosted language model that writes the replies (an OpenAI-compatible endpoint). |
| `EMBEDDINGS_API_KEY`, `EMBEDDINGS_BASE_URL`, `EMBEDDINGS_MODEL` | Hosted embeddings for document search. |
| `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_CALLER_ID` | Real phone calls. |
| `PUBLIC_BASE_URL` | The public HTTPS address of the phone-only port, for example `https://phone.example.in`. |
| `CORS_ORIGINS` | The origin of the web app, no trailing slash. |
| `VAKYA_LOCAL_TOKEN` or the three Supabase settings | Who may call the API. Without one of them the API refuses requests that do not come from the same machine. |

Keys are secrets: set them as secrets in your host, never in an image.

## 3. A public HTTPS address for the phone line

Vobiz opens a WebSocket to your server during a call, so the phone-only port must be reachable from the internet over HTTPS (`wss://`). Put a reverse proxy or your host's load balancer in front of port 8021 with a certificate, and set `PUBLIC_BASE_URL` to that address. Expose only 8021 this way. Never put port 8020 on the internet without sign-in in front of it.

Check it by opening the web-call link from the desk on a phone that is not on your network. In the Vobiz console, the Answer URL of the number is the link shown in the desk under Front desk, Phone number.

## 4. State

The API keeps agents, documents, call records, caller profiles, voices and receipts under `/app/data`. Mount a volume there, or the data is lost when the container is replaced. Back up the voice vault key with the voices (`docs/VOICE_VAULT.md`). For several users, use hosted mode with Supabase (`backend/supabase/README.md`); it is tested against fakes only.

## 5. What to expect

- **Cold start.** The first request after a deploy is a little slower while the API opens its connections to Gnani and the hosted models. Place one test call before presenting.
- **Rate limits.** Gnani allows one active request at a time and the language model has its own limits. When Gnani is busy the caller hears a short held line and is offered a callback (`docs/GNANI.md`).
- **One replica.** The gateway's one-request-at-a-time rule and the in-memory live-call log live in one process. Run one API replica.
- **Untested.** This deployment path has not been run against a live Gnani key or a live Vobiz number.
