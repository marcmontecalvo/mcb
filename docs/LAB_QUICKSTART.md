# Start and use the local lab

Run these commands in PowerShell from the repository root (D:\mcb on this machine).
Use the existing two entry points: **mclab.ps1** controls Minecraft and
**lab-ui.ps1** opens the dashboard.

## Daily startup

If Minecraft is stopped:

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server start
```

Check readiness; repeat until the state is **running**:

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server status
```

Do not issue start again if status is already running. A **starting** response
is only supervisor acknowledgement. An **unreachable** response needs diagnosis,
not another launch. Startup commonly needs tens of seconds.

Open the dashboard in a second PowerShell terminal:

```powershell
rtk proxy powershell -NoProfile -File scripts/lab-ui.ps1
```

The launcher opens **http://127.0.0.1:8770/** and reuses an existing dashboard.
For a new dashboard process, keep its terminal open. Add **--no-browser** to
print the URL instead or **--port 8771** to select a port.
The dashboard launcher does not itself start Minecraft.

## Interact

- **Dashboard:** choose a catalog test, configure its seed/settings, preview
  the resolved configuration and enqueue/start the suite. Inspect live progress
  and history there. Start with a single development run; qualification and
  model trials have separate prerequisites.
- **Minecraft:** join **127.0.0.1:25575** from an authenticated Java **26.3**
  client on this desktop. The initial course spawn is near **1000,80,1000**;
  natural/build runs use their own worlds/sites. Keep humans out of automated
  fixture/reset regions. No client mods are needed for ordinary observation.
- **CLI:** use the wrapper's help to find the supported operations:

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 --help
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server status
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 doctor
```

For build/gather commands, checkpoints and inspection, see
[BUILD_DEBUGGING.md](BUILD_DEBUGGING.md) and the companion examples in
[README.md](../README.md). The fixed replay launcher was removed;
use run-build.py --resume with the checkpoint you actually intend to inspect.

## Stop and reopen

Use **Stop after batch** to finish the current batch and skip queued work, or
**Cancel current run** to request cancellation and cleanup. Wait for the worker
to finish before stopping Minecraft:

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server stop
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server status
```

**Shut down dashboard** stops the dashboard only. Closing its browser tab does
not cancel the durable worker or stop Minecraft. To reload changed dashboard
code, use its shutdown action and relaunch, or:

```powershell
rtk proxy powershell -NoProfile -File scripts/lab-ui.ps1 -Restart
```

Restart affects only the verified dashboard process; workers and Minecraft
continue. See [LAB_UI.md](LAB_UI.md) for lifecycle details.

## First setup or missing assets

Prerequisites: PowerShell, Git, **rtk**, **uv**, and Node/npm for the React build.
These PowerShell wrappers invoke rtk themselves. Setup downloads pinned local
toolchains/dependencies; it does not install a model service.

Only with the lab stopped, run initial setup:

```powershell
rtk proxy powershell -NoProfile -File scripts/setup.ps1
rtk proxy npm --prefix ui ci
rtk proxy npm --prefix ui run build
```

Setup generates C1 and builds/installs mods. It is not daily startup: do not
rerun it against an active run. It does not build the frontend, which is why
the npm steps are separate. If ui/dist is absent, the root page reports the missing build (HTTP 503);
build the React app to get the current interface. The older interface remains
available explicitly at http://127.0.0.1:8770/legacy.

First Minecraft launch requires EULA acceptance. Read the EULA linked from
[README.md](../README.md); if you accept it, use server start --accept-eula.
The current installation already has operator authorization; acceptance persists
in ignored local runtime files.

After changing Java mod source, stop Minecraft, run scripts/build-mod.ps1,
then scripts/mclab.ps1 mod-install, and start again. A dashboard restart alone
does not install Java changes.

## Troubleshooting and evidence

- **Not running:** inspect runtime/server-console.log and server status.
  Do not kill arbitrary Java/Python processes or delete lock files blindly.
- **Dashboard unavailable:** inspect its terminal, rerun lab-ui.ps1 to reopen
  the recorded port, or use the verified restart above.
- **Run evidence:** dashboard worker records are in results/sessions/;
  build/gather evidence is in results/companion/<run>/; other families retain
  their own results directories. Inspect summary.json and error.txt when present.
- **Scripts:** [the complete audit](SCRIPT_AUDIT_2026-11-15.md) separates normal
  launchers, maintained runners, specialist tools and removed historical diagnostics.
- **Scope:** current server protocol readiness and existing UI build were checked
  on 2026-11-15. This guide was checked against current entry points; a fresh
  end-to-end startup/browser/run/shutdown was not performed.
