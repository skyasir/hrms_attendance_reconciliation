"""Reconcile auto attendance with biometric check-ins that arrive late.

When a device stops syncing, auto attendance marks the missing days Absent. Once the
check-ins arrive, hrms cannot create attendance over the existing Absent, so it sets
`skip_auto_attendance` on them and they are never processed.

This hook runs when such a check-in is inserted. It cancels the auto-marked Absent for
that shift date. Attendance.on_cancel unlinks its check-ins, so the next auto attendance
run re-marks the day from every check-in with the standard hrms rules.
"""

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, getdate

SETTING = "reconcile_attendance_with_late_checkins"

# hrms comments this (translated) on every Absent it marks for a day with no check-ins
MISSING_CHECKINS_COMMENT = "Employee was marked Absent due to missing Employee Checkins."


def reconcile_late_checkin(doc, method=None):
	if not frappe.db.get_single_value("HR Settings", SETTING):
		return

	if not doc.shift or not doc.shift_start or not doc.shift_actual_end:
		return

	if doc.skip_auto_attendance or doc.get("offshift") or doc.attendance:
		return

	if not shift_already_processed(doc.shift, doc.shift_actual_end):
		return

	for attendance in get_auto_marked_absents(doc.employee, getdate(doc.shift_start)):
		cancel_absent(attendance, doc.name)


def shift_already_processed(shift_type, shift_actual_end):
	"""True when auto attendance has already covered the shift this check-in belongs to."""
	shift = frappe.db.get_value(
		"Shift Type", shift_type, ["enable_auto_attendance", "last_sync_of_checkin"], as_dict=True
	)
	return bool(
		shift
		and shift.enable_auto_attendance
		and shift.last_sync_of_checkin
		and get_datetime(shift_actual_end) < get_datetime(shift.last_sync_of_checkin)
	)


def get_auto_marked_absents(employee, attendance_date):
	"""Submitted Absents for the date that auto attendance created.

	Leaves and attendance requests are excluded. Of the rest, only records with linked
	check-ins (below the working hours threshold) or with the hrms "missing Employee
	Checkins" comment count as auto-marked, so an Absent entered by hand is never touched.
	"""
	absents = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": attendance_date,
			"docstatus": 1,
			"status": "Absent",
			"leave_type": ["is", "not set"],
			"attendance_request": ["is", "not set"],
		},
		pluck="name",
	)
	return [name for name in absents if is_auto_marked(name)]


def is_auto_marked(attendance):
	if frappe.db.exists("Employee Checkin", {"attendance": attendance}):
		return True

	return bool(
		frappe.db.exists(
			"Comment",
			{
				"reference_doctype": "Attendance",
				"reference_name": attendance,
				"comment_type": "Comment",
				"content": ["in", list({MISSING_CHECKINS_COMMENT, _(MISSING_CHECKINS_COMMENT)})],
			},
		)
	)


def cancel_absent(attendance, checkin):
	# a failure here must never block the biometric sync that inserted the check-in
	frappe.db.savepoint("attendance_reconciliation")
	try:
		doc = frappe.get_doc("Attendance", attendance)
		doc.flags.ignore_permissions = True
		doc.cancel()
		doc.add_comment(
			"Comment",
			_(
				"Cancelled by Attendance Reconciliation: late check-in {0} received. "
				"Attendance will be re-marked from check-ins on the next auto attendance run."
			).format(checkin),
		)
		return True
	except Exception:
		frappe.db.rollback(save_point="attendance_reconciliation")
		frappe.log_error(title=f"Attendance Reconciliation failed for {attendance}")
		return False


@frappe.whitelist()
def reconcile_shifts(from_date, to_date, shift_type=None):
	"""Manual run: reconcile every processed date in the range that has an auto-marked
	Absent and check-ins not linked to any attendance, then mark attendance.

	Covers one Shift Type, or every Shift Type with auto attendance when none is given
	(shifts whose auto attendance is not fully set up are skipped). Unlike the automatic
	hook it also re-opens check-ins that hrms already skipped because of the Absent, so
	it repairs days from before the setting was turned on. Check-ins someone skipped by
	hand (no hrms skip comment) stay skipped.
	"""
	frappe.has_permission("Attendance", "cancel", throw=True)

	from_date, to_date = getdate(from_date), getdate(to_date)
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))

	names = [shift_type] if shift_type else frappe.get_all(
		"Shift Type", filters={"enable_auto_attendance": 1}, pluck="name", order_by="name"
	)

	shifts, not_set_up = [], []
	for name in names:
		shift = frappe.get_doc("Shift Type", name)
		shift.check_permission("write")
		if shift.enable_auto_attendance and shift.process_attendance_after and shift.last_sync_of_checkin:
			shifts.append(shift)
		elif shift_type:
			frappe.throw(
				_("Enable Auto Attendance and set Process Attendance After and Last Sync of Checkin first.")
			)
		else:
			not_set_up.append(name)

	results = [reconcile_shift(shift, from_date, to_date) for shift in shifts]
	return {
		"shifts": len(shifts),
		"not_set_up": not_set_up,
		"cancelled": sum(r["cancelled"] for r in results),
		"reopened": sum(r["reopened"] for r in results),
		"results": [r["result"] for r in results if r["result"]],
	}


def reconcile_shift(shift, from_date, to_date):
	checkins = frappe.get_all(
		"Employee Checkin",
		filters={
			"shift": shift.name,
			"attendance": ["is", "not set"],
			"offshift": 0,
			"shift_start": ["between", [from_date, add_days(to_date, 1)]],
			"shift_actual_end": ["<", shift.last_sync_of_checkin],
		},
		fields=["name", "employee", "shift_start", "skip_auto_attendance"],
	)

	days = {}
	for checkin in checkins:
		attendance_date = getdate(checkin.shift_start)
		if from_date <= attendance_date <= to_date:
			days.setdefault((checkin.employee, attendance_date), []).append(checkin)

	cancelled = reopened = 0
	for (employee, attendance_date), day_checkins in days.items():
		to_reopen = [c.name for c in day_checkins if c.skip_auto_attendance and skipped_by_hrms(c.name)]
		if len(to_reopen) + sum(not c.skip_auto_attendance for c in day_checkins) == 0:
			continue  # every check-in was skipped by hand, nothing to re-mark from

		absents = get_auto_marked_absents(employee, attendance_date)
		if not absents or not all(cancel_absent(a, day_checkins[0].name) for a in absents):
			continue

		cancelled += len(absents)
		if to_reopen:
			frappe.db.set_value("Employee Checkin", {"name": ["in", to_reopen]}, "skip_auto_attendance", 0)
			reopened += len(to_reopen)

	result = shift.process_auto_attendance(is_manually_triggered=True) if cancelled else None
	return {"cancelled": cancelled, "reopened": reopened, "result": result}


def skipped_by_hrms(checkin):
	"""True when hrms set skip_auto_attendance itself, which it records in a comment."""
	reasons = list({"Reason for skipping auto attendance:", _("Reason for skipping auto attendance:")})
	return any(
		frappe.db.exists(
			"Comment",
			{
				"reference_doctype": "Employee Checkin",
				"reference_name": checkin,
				"comment_type": "Comment",
				"content": ["like", f"%{reason}%"],
			},
		)
		for reason in reasons
	)
