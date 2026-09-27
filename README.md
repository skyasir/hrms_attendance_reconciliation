# HRMS Attendance Reconciliation

Re-marks auto attendance when biometric check-ins arrive late for a day that
Frappe HR already marked **Absent**.

## The problem

A biometric device stops syncing for a day or two. Auto Attendance keeps running,
finds no check-ins and marks everyone Absent. When the device syncs again, the
missing check-ins arrive, but hrms cannot create attendance over the existing
Absent. It ticks **Skip Auto Attendance** on those check-ins and never processes
them again, so HR has to correct every record by hand.

This happens whenever **Last Sync of Checkin** moves past a day before the
device's data for it has arrived. That is usually because **Automatically update
Last Sync of Checkin** is on in the Shift Type.

## What it does

Tick **HR Settings > Shift and Attendance > Reconcile Attendance with Late
Check-ins**. Then, when a check-in is inserted:

1. If its shift has already been processed by auto attendance (the shift ended
   before the Shift Type's *Last Sync of Checkin*), and
2. that date has an **auto-marked** Absent for the employee,

the Absent is cancelled, with a comment naming the check-in. Cancelling unlinks any
check-ins the Absent had. The next auto attendance run (hourly, or **Mark
Attendance** on the Shift Type) re-marks the day from all of that date's check-ins,
using the standard hrms rules: Present, Half Day, Absent, late entry, early exit
and overtime. No attendance logic is copied.

Only auto-marked Absents are touched: no leave type, no attendance request, and
either linked check-ins or the hrms "missing Employee Checkins" comment. An Absent
entered by hand, a leave or an attendance request is never cancelled.

Frappe HR's "Unlinked logs" popup, which it shows whenever an Attendance with check-ins
is cancelled, is suppressed for these cancellations only; a manual run would otherwise
stack one popup per day. Cancelling an Attendance by hand still shows it.

If a cancellation fails, it is rolled back and written to the Error Log. The
check-in is still saved, so the biometric sync is never blocked. Cancellation
ignores the syncing user's permissions, so an API user that cannot cancel
Attendance still triggers it.

The setting is off by default; with it off, hrms behaves exactly as before.

## When it runs

- **Cancelling the Absent:** immediately, the moment the late check-in is saved.
- **Re-marking the day:** on the next auto attendance run. hrms runs it **hourly**
  (the `hourly_long` scheduler job), so the day is corrected within about an hour.
  Click **Mark Attendance** on the Shift Type to do it at once. The bench scheduler
  must be running for the hourly job.

## Manual run

Two buttons, both asking for a date range (default: the last 30 days):

- **HR Settings > Shift and Attendance > Reconcile Late Check-ins**, under the
  checkbox: every Shift Type with auto attendance enabled, or one Shift Type if you
  pick it. Shifts whose auto attendance is not fully set up (no Process Attendance
  After or Last Sync of Checkin) are skipped and listed.
- **Shift Type > Reconcile Late Check-ins** (shown when Enable Auto Attendance is
  on): that shift only.

For each shift, in one step it:

1. cancels auto-marked Absents on days in the range that have check-ins not linked
   to any attendance,
2. re-opens check-ins that hrms skipped because of that Absent, and
3. runs Mark Attendance for the shift, the same job the hourly scheduler runs, even
   when nothing was cancelled, so the day is re-marked at once. The date range only
   limits which Absents are cancelled; Mark Attendance itself covers the shift's usual
   window (Process Attendance After to Last Sync of Checkin), as the scheduler does.

This repairs days from **before** the setting was turned on, or from any time a
cancellation failed. It works whether or not the setting is ticked. Check-ins
someone skipped by hand stay skipped; only check-ins hrms itself skipped (it leaves
a "Reason for skipping auto attendance" comment) are re-opened. It needs permission
to cancel Attendance and to edit the Shift Types it covers.

## Not covered

- There is no check for payroll already processed. A day can change from Absent to
  Present after the salary slip for that period was submitted.

## Install

Works with Frappe / Frappe HR v15 and v16.

```
bench get-app https://github.com/skyasir/hrms_attendance_reconciliation
bench --site <site> install-app hrms_attendance_reconciliation
```

## Tests

Run against a live site; everything is rolled back.

```
cd <bench>/sites
../env/bin/python ../apps/hrms_attendance_reconciliation/tests/test_reconcile.py <site>
```

## License

MIT
