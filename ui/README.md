# MCB Lab UI

React and TypeScript, built with Vite and Tailwind. Production is static files
served by the existing Python dashboard; it does not need a Node server.

```powershell
cd ui
npm ci
npm run build
cd ..
uv run --locked python -m mclab.dashboard
```

The dashboard serves `ui/dist` at `/`, injects its local UI token, and serves the
bundled `/assets` files. `/legacy` keeps the existing dashboard available. Without
a frontend build, `/` explains the build step and `/legacy` remains available.
The dashboard's loopback,
Host, Origin and token rules apply to the new app as well.

Run `npm test` for form/state tests and `npm run build` for TypeScript and bundle
validation. Run `npm run test:e2e` for desktop and 390px browser flows. Install its
browser once with `npx playwright install chromium` if needed. The
[fixture](e2e/server.py) serves real static assets, APIs and SSE with temporary
suite/session files and simulated workers. It never launches Minecraft or calls
model providers. Browser traces/screenshots stay in ignored `test-results/`.

## Change the UI

- Set the app's semantic colors, spacing and appearance in `src/style.css`;
  use HomeNet tokens and components rather than local component color literals.
- `src/main.tsx` owns the picker, configuration, suites, queue and activity views.
  Schema and API types/helpers live alongside it under `src/`.
- Configuration controls come from `/api/schema`. A new dataclass field reaches
  the form through that schema, with family capability and catalog identity
  restrictions respected by the renderer. Keep backend validation authoritative.
- Catalog entries and presets come from Python, not a private frontend registry.
  Suites save references and explicit overrides; launches receive resolved
  immutable snapshots. Do not turn a saved suite into a copied settings registry.

`vendor/homenet-ui-0.3.2.tgz` is a pinned portable package, so a sibling HomeNet
checkout is unnecessary. Its revision, build and font notices are recorded in
[PROVENANCE.md](vendor/PROVENANCE.md).

Live state arrives through authenticated server-sent snapshots. Browser reconnects
receive current durable state; closing a tab does not own or stop a worker. Keep
graceful batch stop, current-run cancellation and closing the dashboard distinct.
