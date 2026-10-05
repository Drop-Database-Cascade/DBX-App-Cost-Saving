# Idle auto-stop for Databricks Apps

A Databricks App bills for its compute for as long as it is running, whether or
not anyone is using it. This snippet makes an app stop itself after a period
with no user activity, so an app left running overnight or over a weekend
stops costing money once people stop using it.

## How it works

1. A small ASGI middleware records the time of every request (and every
   incoming WebSocket message).
2. A background task checks once a minute how long it has been since the last
   activity.
3. When the idle limit is reached, the app calls the Databricks Apps API to stop
   itself, using its own service principal. No extra credentials are needed:
   the Apps runtime injects them.

An optional hard cap (`AUTO_STOP_MAX_HOURS`) stops the app after a fixed time
regardless of activity.

## Files

| File | Purpose |
| --- | --- |
| `auto_stop.py` | The reusable module: tracker, middleware and stop logic |
| `main.py` | Minimal FastAPI example showing how to wire it in |
| `app.yaml` | Databricks Apps config with the two settings |
| `requirements.txt` | Python dependencies |
| `notebooks/start_app.py` | Notebook that starts the app and waits until it is ready |
| `notebooks/stop_app.py` | Notebook that stops the app on demand |
| `databricks.yml.snippet` | Asset Bundle config: permissions plus the start/stop jobs |

## Setup

1. Copy `auto_stop.py` into your app and wire it in as shown in `main.py`.
2. Set `AUTO_STOP_IDLE_MINUTES` in `app.yaml` (or your bundle's app config).
3. Give the app's own service principal **CAN MANAGE** on the app, either on
   the app's Permissions page or in your bundle. Without it, the stop call is
   rejected.
4. Deploy. The startup logs should show
   `Auto-stop: '<app>' will stop after 60 min idle`.

To test, set `AUTO_STOP_IDLE_MINUTES` to `"2"`, redeploy, open the app once and
leave it. The app should stop roughly two minutes later.

## Things to know

- **Background traffic keeps the app awake.** Health checks, or a page that
  polls an endpoint while a tab is open, look like activity. Add those paths
  to `ignore_path_prefixes`.
- **Use a single worker.** Each process keeps its own idle clock, so with
  several workers an idle one could stop the app while another is serving
  users.
- **The SIGTERM fallback is a last resort.** If the API call fails three times,
  the process sends itself SIGTERM. That only stops this process; if your app
  runs a separate frontend process, it keeps running. The usual cause of the
  API call failing is a missing CAN MANAGE permission.
- **Changing the setting needs a redeploy.** Starting a stopped app reuses its
  last deployment, so edit `app.yaml` and redeploy for a new value to apply.
- **Other frameworks.** The middleware is ASGI. For Flask or Dash apps, call
  `tracker.touch()` from a `before_request` hook and run the idle check in a
  background thread instead of an asyncio task.

## Starting and stopping on demand

Once the app has stopped itself, someone has to start it again. The two
notebooks in `notebooks/` make that a one-click job, so non-technical users can
start or stop the app without needing access to the Apps page.

- **`start_app.py`** starts the app and waits until its compute is ACTIVE and
  the app process reports RUNNING. If your app takes a while to load after
  that, set `warmup_seconds` to hold the job open a little longer. The app URL
  is printed only once it is ready, and the run fails if the app crashes on
  startup.
- **`stop_app.py`** stops the app and waits until it reaches STOPPED.

Both skip the call if the app is already in the requested state, and both take
`app_name` as a widget or job parameter.

To set them up as jobs:

1. Import both notebooks into your workspace (they are in Databricks source
   format), or deploy them with the bundle snippet.
2. Create one job per notebook with no schedule, and pass `app_name` as a job
   parameter.
3. Give the job's run-as identity **CAN MANAGE** on the app, and give your
   users **CAN MANAGE RUN** on the jobs.
4. Optionally add an "on success" notification to the start job, so users are
   told when the app is ready rather than opening it while it is still loading.

The `wait_get_app_active` / `wait_get_app_stopped` helpers need a recent
`databricks-sdk`. If you see an `AttributeError`, add a first cell with
`%pip install --upgrade databricks-sdk` followed by
`dbutils.library.restartPython()`.

You can also start an app from the Apps page in the workspace, or with
`databricks apps start <app-name>` from the CLI.
