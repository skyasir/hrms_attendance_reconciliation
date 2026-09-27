"""Tests for HRMS Attendance Reconciliation, run against a live site. Nothing is saved.

Creates a throwaway shift, holiday list and check-ins, simulates a biometric outage and
late sync, and rolls everything back. hrms commits inside auto attendance on v15, so
`frappe.db.commit` is stubbed out for the whole run.

    cd <bench>/sites
    ../env/bin/python ../apps/hrms_attendance_reconciliation/tests/test_reconcile.py <site>
"""

import pathlib
import sys
import unittest
from unittest.mock import patch

import frappe


def _default_site():
	current = pathlib.Path("currentsite.txt")
	if current.is_file():
		return current.read_text().strip()
	sites = sorted(p.name for p in pathlib.Path(".").iterdir() if (p / "site_config.json").is_file())
	if len(sites) == 1:
		return sites[0]
	sys.exit(f"Pass the site name as an argument. Found {len(sites)}: {', '.join(sites) or 'none'}")


SITE = sys.argv[1] if len(sys.argv) > 1 else _default_site()
SETTING = "reconcile_attendance_with_late_checkins"
SHIFT = "_Test Reconcile Shift"
_commit = None


def setUpModule():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	global _commit
	_commit = patch.object(frappe.db, "commit", lambda *args, **kwargs: None)
	_commit.start()
	frappe.flags.in_test = True


def tearDownModule():
	frappe.db.rollback()
	_commit.stop()
	frappe.destroy()


class TestAttendanceReconciliation(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all(
			"Company", pluck="name", limit=1
		)[0]
		cls.emp, cls.manual_emp, cls.run_emp = [
			make_employee(company, n) for n in ("Late Sync", "Manual Absent", "Manual Run")
		]
		holiday_list = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": "_Test Reconcile Holidays",
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
			}
		).insert()
		frappe.db.set_value("Company", company, "default_holiday_list", holiday_list.name)
		if frappe.db.exists("DocType", "Holiday List Assignment"):  # hrms v16
			assignment = frappe.get_doc(
				{
					"doctype": "Holiday List Assignment",
					"applicable_for": "Company",
					"assigned_to": company,
					"holiday_list": holiday_list.name,
					"from_date": "2026-01-01",
				}
			).insert()
			if assignment.meta.is_submittable:
				assignment.submit()

		cls.shift = frappe.get_doc(
			{
				"doctype": "Shift Type",
				"__newname": SHIFT,
				"start_time": "09:00:00",
				"end_time": "17:00:00",
				"enable_auto_attendance": 1,
				"determine_check_in_and_check_out": "Alternating entries as IN and OUT during the same shift",
				"working_hours_calculation_based_on": "First Check-in and Last Check-out",
				"begin_check_in_before_shift_start_time": 60,
				"allow_check_out_after_shift_end_time": 60,
				"process_attendance_after": "2026-09-20",
				"last_sync_of_checkin": "2026-09-23 08:00:00",
				"working_hours_threshold_for_absent": 4,
			}
		).insert()
		for employee in (cls.emp, cls.manual_emp, cls.run_emp):
			frappe.db.set_value("Employee", employee, "default_shift", SHIFT)

		# the outage: no check-ins at all, so 20th-22nd are auto-marked Absent
		run_auto_attendance()

	def setUp(self):
		frappe.db.set_single_value("HR Settings", SETTING, 1)

	def test_setting_off_keeps_hrms_behaviour(self):
		frappe.db.set_single_value("HR Settings", SETTING, 0)
		absent = get_attendance(self.emp, "2026-09-20")
		checkin(self.emp, "2026-09-20 09:02:00", "IN")
		checkin(self.emp, "2026-09-20 17:05:00", "OUT")
		self.assertEqual(frappe.db.get_value("Attendance", absent.name, "docstatus"), 1)
		run_auto_attendance()
		self.assertEqual(get_attendance(self.emp, "2026-09-20").status, "Absent")

	def test_late_checkins_re_mark_the_day(self):
		absent = get_attendance(self.emp, "2026-09-21")
		self.assertEqual(absent.status, "Absent")

		checkin(self.emp, "2026-09-21 08:55:00", "IN")
		self.assertEqual(frappe.db.get_value("Attendance", absent.name, "docstatus"), 2)

		checkin(self.emp, "2026-09-21 17:10:00", "OUT")
		run_auto_attendance()
		attendance = get_attendance(self.emp, "2026-09-21")
		self.assertEqual(attendance.status, "Present")
		self.assertAlmostEqual(attendance.working_hours, 8.25, places=2)

	def test_partial_sync_is_re_marked_again(self):
		checkin(self.emp, "2026-09-22 09:00:00", "IN")
		run_auto_attendance()
		self.assertEqual(get_attendance(self.emp, "2026-09-22").status, "Absent")  # below threshold

		checkin(self.emp, "2026-09-22 17:00:00", "OUT")
		run_auto_attendance()
		self.assertEqual(get_attendance(self.emp, "2026-09-22").status, "Present")

	def test_manual_absent_is_never_cancelled(self):
		absent = get_attendance(self.manual_emp, "2026-09-21")
		frappe.db.delete("Comment", {"reference_doctype": "Attendance", "reference_name": absent.name})
		checkin(self.manual_emp, "2026-09-21 09:00:00", "IN")
		self.assertEqual(frappe.db.get_value("Attendance", absent.name, "docstatus"), 1)

	def test_unprocessed_shift_is_ignored(self):
		checkin(self.emp, "2026-09-23 09:00:00", "IN")
		self.assertIsNone(get_attendance(self.emp, "2026-09-23"))

	def test_manual_run_repairs_days_skipped_before_setting(self):
		from hrms_attendance_reconciliation.attendance_reconciliation.reconcile import reconcile_shifts

		# setting off: hrms skips the late check-ins because the Absent exists
		frappe.db.set_single_value("HR Settings", SETTING, 0)
		skipped = [
			checkin(self.run_emp, "2026-09-20 09:00:00", "IN"),
			checkin(self.run_emp, "2026-09-20 17:30:00", "OUT"),
		]
		run_auto_attendance()
		for c in skipped:
			self.assertEqual(frappe.db.get_value("Employee Checkin", c.name, "skip_auto_attendance"), 1)

		# skipped by hand on the 21st: must stay skipped, and the Absent must stay
		by_hand = checkin(self.run_emp, "2026-09-21 09:00:00", "IN")
		frappe.db.set_value("Employee Checkin", by_hand.name, "skip_auto_attendance", 1)
		absent_21 = get_attendance(self.run_emp, "2026-09-21")

		# HR Settings button: no Shift Type given, so every auto attendance shift is covered
		result = reconcile_shifts("2026-09-19", "2026-09-23")
		self.assertEqual((result["cancelled"], result["reopened"]), (1, 2))
		self.assertEqual(len(result["results"]), 1)

		attendance = get_attendance(self.run_emp, "2026-09-20")
		self.assertEqual(attendance.status, "Present")
		self.assertAlmostEqual(attendance.working_hours, 8.5, places=2)
		self.assertEqual(frappe.db.get_value("Employee Checkin", by_hand.name, "skip_auto_attendance"), 1)
		self.assertEqual(frappe.db.get_value("Attendance", absent_21.name, "docstatus"), 1)

	def test_user_without_cancel_permission(self):
		absent = get_attendance(self.manual_emp, "2026-09-20")
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "_test_reconcile_sync@example.com",
				"first_name": "Sync",
				"send_welcome_email": 0,
				"roles": [{"role": "Employee"}],
			}
		).insert()
		frappe.set_user(user.name)
		try:
			self.assertFalse(frappe.has_permission("Attendance", "cancel"))
			from hrms_attendance_reconciliation.attendance_reconciliation.reconcile import reconcile_shifts

			self.assertRaises(frappe.PermissionError, reconcile_shifts, "2026-09-19", "2026-09-23")
			frappe.get_doc(
				{
					"doctype": "Employee Checkin",
					"employee": self.manual_emp,
					"time": "2026-09-20 09:00:00",
					"log_type": "IN",
				}
			).insert(ignore_permissions=True)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Attendance", absent.name, "docstatus"), 2)


def make_employee(company, first_name):
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": first_name,
				"last_name": "_Test Reconcile",
				"company": company,
				"gender": frappe.get_all("Gender", pluck="name", limit=1)[0],
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"status": "Active",
			}
		)
		.insert()
		.name
	)


def run_auto_attendance():
	frappe.get_doc("Shift Type", SHIFT).process_auto_attendance()


def checkin(employee, time, log_type):
	return frappe.get_doc(
		{"doctype": "Employee Checkin", "employee": employee, "time": time, "log_type": log_type}
	).insert()


def get_attendance(employee, date):
	rows = frappe.get_all(
		"Attendance",
		filters={"employee": employee, "attendance_date": date, "docstatus": 1},
		fields=["name", "status", "working_hours"],
	)
	return rows[0] if rows else None


if __name__ == "__main__":
	unittest.main(argv=sys.argv[:1], verbosity=2)
