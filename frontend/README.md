# Frontend

Set `API_URL` in `frontend/.env` (see `.env.example`). It is server-only: Next rewrites `/api/*` and `/token` to that backend, so the browser never sees it. Do not rename it to `NEXT_PUBLIC_`, since that would inline the backend URL into the client bundle.

```
npm run contrast   # WCAG AA token check, both themes
npm run lint
npm run typecheck
npm run build
npm run test:e2e   # axe + keyboard checks (needs Chrome)
```
