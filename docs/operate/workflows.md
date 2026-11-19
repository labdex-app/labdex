# Workflows and maintenance windows

A workflow runs steps in order: job templates, plugin actions, waits and health checks. Run it
with one click, or on a schedule, inside a maintenance window if you give it one. Use one for
routine work that takes several steps, such as patching the home VMs every week:

1. Snapshot a VM (the Proxmox plugin's *Take a snapshot*).
2. Install updates (the Patching plugin's *Install updates* plan).
3. Wait a few minutes for the reboots.
4. Check that the hosts answer again.

## Steps

```yaml
name: home vms
window: weekend nights            # a change window by name; empty = any time
schedule: weekly                  # '', every (with every_minutes) or weekly
days: [sat]
at: "02:00"
timezone: Europe/Zurich
steps:
  - {kind: action, integration: pve, action: snapshot, host: srv-vm-dns-01, params: {name: before-patch}}
  - {kind: template, template: patch home vms}
  - {kind: wait, minutes: 5}
  - {kind: check, timeout_minutes: 15}
  - {kind: action, integration: home-mon, action: sync, on_failure: continue}
```

| Kind | What it does |
| --- | --- |
| `template` | Runs a [job template](templates.md) with its saved parameters. |
| `action` | Runs a plugin action: `integration`, `action`, `params`, and `host` for an action on one host. |
| `wait` | Waits `minutes` (1 to 1440). |
| `check` | Waits until the hosts answer: `hosts`, or when empty the hosts of the steps before it. Up means the host status from your monitoring plugins (see [Monitoring](../plugins/monitoring.md)) says up. Fails after `timeout_minutes` (default 15). |

Every step can have a `name` and `on_failure`: `stop` (the default) ends the run there and marks
the steps after it as skipped; `continue` goes on to the next step.

## Plans and approvals

A step that is a plan works like any other plan. It shows up on the Changes page with its
workflow and step, waits for the approvals its [policy](../guide/plans.md) asks for, and the run
waits with it. An action with `approval: always` (Patching, Compose stacks, Proxmox guest changes)
always needs a person. Approve the plan and the run goes on; discard it and the step fails. A plan
with nothing to change counts as done.

## Maintenance windows

A workflow's window is one of the change windows that approval policies use (for example Tue
and Thu 20:00 to 22:00 Europe/Berlin). A step only starts while the window is open. A run that
starts outside the window waits for it, and a run still going when the window closes pauses
before its next step and continues at the next opening. A step that has already started runs to
its end. A schedule with a window starts at the first open moment at or after the scheduled time.

## Runs

One run of a workflow at a time. A run copies the workflow's steps when it starts, so editing
the workflow doesn't change a run under way. *Cancel* stops a run and discards a plan it is
waiting on.

Notifications: `workflow.started`, `workflow.waiting` (a step waits for an approval),
`workflow.succeeded` and `workflow.failed`.

## Rights

Running a workflow needs the right to apply on every integration its steps use (and on the host
of a step for one host). Saving one with a schedule needs admin rights on those integrations,
because it runs unattended. Each plan still needs its own approvals.

## API

| | |
| --- | --- |
| `GET/POST /api/workflows`, `GET/PUT/DELETE /api/workflows/<id>` | workflows (`window` by name or `window_id`) |
| `POST /api/workflows/<id>/run` | start a run |
| `GET /api/workflow-runs?status=open` | runs, newest first |
| `GET /api/workflow-runs/<id>` | a run with its steps: `status`, `job_id`, `note`, `started_at`, `finished_at` |
| `POST /api/workflow-runs/<id>/cancel` | cancel a run |

A job that belongs to a run carries `workflow: {run_id, name, step, of, status}` in `/api/jobs`.
