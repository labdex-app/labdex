# Jobs and the worker

Integration actions run as jobs from a queue in the database. A dispatcher claims a
job, runs it and sends heartbeats; a job whose worker stops (no heartbeat for 90
seconds) is marked failed instead of hanging.

By default the web server runs jobs itself (`LABDEX_WORKER=inline`), which is all a
single container needs. To keep long Ansible runs and scans out of the web process, or
to run several gunicorn workers on PostgreSQL, set `LABDEX_WORKER=external` on the web
server and start workers with the same database and data folder:

```sh
labdex worker --slots 2      # runs queued jobs, the scheduler and approved plans
```

About shows who runs jobs and how many wait; the Dashboard warns when nobody does.
Job output streams live over Server-Sent Events. Each host has a *Runs* tab with the
jobs that ran for it.
