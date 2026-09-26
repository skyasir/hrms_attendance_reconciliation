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
from frappe.utils import get_datetime, getdate

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
	except Exception:
		frappe.db.rollback(save_point="attendance_reconciliation")
		frappe.log_error(title=f"Attendance Reconciliation failed for {attendance}")
