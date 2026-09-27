from . import __version__ as app_version  # noqa: F401

app_name = "hrms_attendance_reconciliation"
app_title = "HRMS Attendance Reconciliation"
app_publisher = "Yasir Shaikh"
app_description = (
	"When biometric check-ins arrive late for a day that auto attendance already "
	"marked Absent, cancel that Absent so the next auto attendance run re-marks the "
	"day from the check-ins."
)
app_email = "erp.yasirshaikh@gmail.com"
app_license = "MIT"
required_apps = ["hrms"]

doc_events = {
	"Employee Checkin": {
		"after_insert": "hrms_attendance_reconciliation.attendance_reconciliation.reconcile.reconcile_late_checkin",
	},
}

fixtures = [
	{
		"doctype": "Custom Field",
		"filters": [["name", "in", ["HR Settings-reconcile_attendance_with_late_checkins"]]],
	},
]

doctype_js = {"Shift Type": "public/js/shift_type.js"}
