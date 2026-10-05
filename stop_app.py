# Databricks notebook source
# MAGIC %md
# MAGIC # Stop a Databricks App
# MAGIC Stops one Databricks App and (optionally) waits until its compute is STOPPED.
# MAGIC A stopped app does not bill for compute.
# MAGIC
# MAGIC Set the widgets, or pass the same names as job parameters.

# COMMAND ----------

# DBTITLE 1,Parameters
from datetime import timedelta

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import ComputeState

dbutils.widgets.text("app_name", "my-app", "App name")
dbutils.widgets.dropdown("wait_for_completion", "true", ["true", "false"], "Wait until STOPPED")

APP_NAME = dbutils.widgets.get("app_name").strip()
WAIT = dbutils.widgets.get("wait_for_completion") == "true"
TIMEOUT = timedelta(minutes=20)

if not APP_NAME:
    raise ValueError("app_name is empty. Set the widget or pass it as a job parameter.")

w = WorkspaceClient()

# COMMAND ----------

# DBTITLE 1,Stop the app
app = w.apps.get(name=APP_NAME)
state = app.compute_status.state if app.compute_status else None
print(f"Current compute state of '{APP_NAME}': {state}")

if state == ComputeState.STOPPED:
    print("App is already stopped. Nothing to do.")
elif state == ComputeState.STOPPING:
    print("App is already stopping.")
    if WAIT:
        w.apps.wait_get_app_stopped(name=APP_NAME, timeout=TIMEOUT)
else:
    waiter = w.apps.stop(name=APP_NAME)
    print("Stop request accepted by the API.")
    if WAIT:
        print(f"Waiting up to {TIMEOUT} for the app to reach STOPPED...")
        waiter.result(timeout=TIMEOUT)

# COMMAND ----------

# DBTITLE 1,Final status
app = w.apps.get(name=APP_NAME)
print(f"Compute state: {app.compute_status.state if app.compute_status else None}")
print(f"App state:     {app.app_status.state if app.app_status else None}")
