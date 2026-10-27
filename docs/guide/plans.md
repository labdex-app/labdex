# Plans and approvals

Anything that changes another system is a **plan** first: the plugin computes the
changes (each with before and after) and stops. You review it on the job page or the
Dashboard and approve or discard it.

The job page groups the changes by kind. An update shows only the fields that change,
before and after. While you may still approve, you can **untick** changes you don't want:
the approval applies the rest, the job log says who left out what, and the plan keeps
them marked. (An Ansible run is one change and can't be applied in part.)

- A plan goes **stale** when the inventory changes before it's applied; it is planned
  again when it's approved. If the new plan has the same changes, approvals carry over.
- A newer plan for the same action replaces the older one (approvals carry over the
  same way).
- **Hooks** re-plan after inventory changes; **schedules** and
  [job templates](../operate/templates.md) plan on a timer. All of them still wait for
  review.

## Approval policies

Admin → Approvals & windows. Policies are matched top to bottom by integration name,
the zones of the hosts a plan touches and how it was started (by someone, a hook, a
schedule). A policy sets:

| | |
| --- | --- |
| approvals | how many are needed; `0` applies by itself |
| approvers | users and `@groups`; empty = anyone who may apply the integration |
| self approval | whether whoever started the plan may approve it |
| delay | applies no sooner than this after planning |
| window | a change window it must wait for |

Without a matching policy you approve your own plans.

## Change windows

Weekly times in a time zone, e.g. Tue and Thu 20:00–22:00 Europe/Zurich (a window may
run past midnight). An approved plan waits as *approved* until its delay has passed and
its window is open; the worker applies it then.

On the command line `labdex action <integration> <action> --apply` applies at once.
