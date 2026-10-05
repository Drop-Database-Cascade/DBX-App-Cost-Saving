# Databricks notebook source
# MAGIC %md
# MAGIC # Start a Databricks App
# MAGIC Starts one Databricks App and waits until it is ready for users:
# MAGIC 1. the app's compute is ACTIVE,
# MAGIC 2. the app process reports RUNNING,
# MAGIC 3. an optional warm-up period has passed (for apps that take a while to load).
# MAGIC
# MAGIC The app URL is printed only once the app is ready. Run it as a job and add an
# MAGIC "on success" notification to tell users when the app is available.
# MAGIC
# MAGIC Set the widgets, or pass the same names as job parameters.

# COMMAND ----------

# DBTITLE 1,Parameters
import time
from datetime import timedelta

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import ApplicationState, ComputeState

dbutils.widgets.text("app_name", "my-app", "App name")
dbutils.widgets.dropdown("wait_for_completion", "true", ["true", "false"], "Wait until ready")
dbutils.widgets.text("warmup_seconds", "0", "Warm-up after RUNNING (s)")

APP_NAME = dbutils.widgets.get("app_name").strip()
WAIT = dbutils.widgets.get("wait_for_completion") == "true"
WARMUP_SECONDS = int(dbutils.widgets.get("warmup_seconds") or 0)

COMPUTE_TIMEOUT = timedelta(minutes=20)
READY_TIMEOUT_S = 10 * 60
POLL_S = 10

if not APP_NAME:
    raise ValueError("app_name is empty. Set the widget or pass it as a job parameter.")

w = WorkspaceClient()

# COMMAND ----------

# DBTITLE 1,Start the app
app = w.apps.get(name=APP_NAME)
state = app.compute_status.state if app.compute_status else None
print(f"Current compute state of '{APP_NAME}': {state}")

started_now = False  # only apply the warm-up if this run actually started the app

if state == ComputeState.ACTIVE:
    print("Compute is already ACTIVE.")
elif state == ComputeState.STOPPING:
    raise RuntimeError(f"'{APP_NAME}' is currently stopping. Wait for it to finish, then run again.")
elif state == ComputeState.STARTING:
    print("App is already starting.")
    started_now = True
    if WAIT:
        w.apps.wait_get_app_active(name=APP_NAME, timeout=COMPUTE_TIMEOUT)
else:
    waiter = w.apps.start(name=APP_NAME)
    print("Start request accepted by the API.")
    started_now = True
    if WAIT:
        print(f"Waiting up to {COMPUTE_TIMEOUT} for compute to become ACTIVE...")
        waiter.result(timeout=COMPUTE_TIMEOUT)

# COMMAND ----------

# DBTITLE 1,Wait until the app is ready for users
def wait_for_app_running():
    """Poll until the app process (not just its compute) reports RUNNING."""
    deadline = time.time() + READY_TIMEOUT_S
    while True:
        a = w.apps.get(name=APP_NAME)
        s = a.app_status.state if a.app_status else None
        if s == ApplicationState.RUNNING:
            print("App process is RUNNING.")
            return a
        if s == ApplicationState.CRASHED:
            msg = a.app_status.message if a.app_status else ""
            raise RuntimeError(f"'{APP_NAME}' crashed during startup: {msg}")
        if time.time() > deadline:
            raise TimeoutError(f"'{APP_NAME}' did not reach RUNNING within {READY_TIMEOUT_S}s (last state: {s}).")
        print(f"  App state: {s}, waiting...")
        time.sleep(POLL_S)


if WAIT:
    wait_for_app_running()

    if started_now and WARMUP_SECONDS > 0:
        print(f"Warm-up: waiting {WARMUP_SECONDS}s for the app to finish loading...")
        time.sleep(WARMUP_SECONDS)

# COMMAND ----------

# DBTITLE 1,Final status
app = w.apps.get(name=APP_NAME)
print(f"Compute state: {app.compute_status.state if app.compute_status else None}")
print(f"App state:     {app.app_status.state if app.app_status else None}")

if WAIT:
    print("\n" + "=" * 60)
    print(f"  '{APP_NAME}' is READY")
    print(f"  URL: {app.url}")
    print("=" * 60)
else:
    print("\nStart requested without waiting; readiness was not verified.")
