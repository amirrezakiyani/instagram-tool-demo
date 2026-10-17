# Downloadino

Live site: https://downloadino.pages.dev/

Downloadino is a lightweight Instagram-link utility built with HTML, CSS, JavaScript, and a Cloudflare Pages Worker.

## Repository layout

- `index.html` — website UI, format selector, and client-side download flow.
- `_worker.js` — Cloudflare Pages Worker handling `/api/*` requests and best-effort public-post metadata retrieval.
- `_routes.json` — routes API requests to the Worker.
- `favicon.svg` — site icon.
- `robots.txt` and `sitemap.xml` — basic search-engine discovery files.
- `googledcd2bf3c6141b7b7.html` — Google Search Console verification file; keep it while the site uses this verification token.
- `downloader/` — optional FastAPI/yt-dlp backend source. It is not deployed by Cloudflare Pages itself and requires a separate compatible host plus configuration of the `DOWNLOADER_API_URL` Worker environment variable before the Worker can use it.

## Deployment

The production site is deployed on Cloudflare Pages from the `demo/initial-site` branch. Pushing changes to that branch triggers a Pages deployment.

## Current limitations

- Download support depends on Instagram's public access behavior and the source media response; public links are not guaranteed to work.
- Audio extraction requires the optional backend and FFmpeg. It is unavailable until that backend is deployed and connected.
- The optional backend configuration is not part of the Cloudflare Pages deployment.
- Only download content you own or have permission to save, and respect Instagram's terms.

## Local preview

Open `index.html` for a basic UI preview. Worker/API functionality requires the Cloudflare Pages development environment or a deployed Pages site.
