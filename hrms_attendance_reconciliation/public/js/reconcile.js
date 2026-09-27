// Loaded on both Shift Type and HR Settings (doctype_js).
frappe.provide("hrms_attendance_reconciliation");

hrms_attendance_reconciliation.open_reconcile_dialog = (shift_type) => {
	const dialog = new frappe.ui.Dialog({
		title: __("Reconcile Late Check-ins"),
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"Cancels auto-marked Absents on days that now have check-ins, re-opens check-ins that were skipped because of them, and marks attendance again. Manually marked attendance and leaves are not touched.",
				)}</p>`,
			},
			{
				fieldname: "shift_type",
				fieldtype: "Link",
				options: "Shift Type",
				label: __("Shift Type"),
				description: __("Leave empty for every shift with auto attendance enabled."),
				get_query: () => ({ filters: { enable_auto_attendance: 1 } }),
				default: shift_type,
				read_only: shift_type ? 1 : 0,
			},
			{
				fieldname: "from_date",
				fieldtype: "Date",
				label: __("From Date"),
				reqd: 1,
				default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			},
			{
				fieldname: "to_date",
				fieldtype: "Date",
				label: __("To Date"),
				reqd: 1,
				default: frappe.datetime.get_today(),
			},
		],
		primary_action_label: __("Reconcile"),
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method: "hrms_attendance_reconciliation.attendance_reconciliation.reconcile.reconcile_shifts",
				args: values,
				freeze: true,
				freeze_message: __("Reconciling attendance..."),
				callback: ({ message: r }) => {
					let msg = r.cancelled
						? __("{0} Absent attendance cancelled, {1} skipped check-ins re-opened.", [
								r.cancelled,
								r.reopened,
						  ])
						: __("No auto-marked Absent in this range has late check-ins.");
					if (!r.shifts) {
						msg = __("No Shift Type has auto attendance fully set up.");
					}
					r.results.forEach((result) => (msg += `<br><br>${__(result)}`));
					if (r.not_set_up.length) {
						msg +=
							"<br><br>" +
							__("Skipped, auto attendance not fully set up: {0}", [
								r.not_set_up.join(", "),
							]);
					}
					frappe.msgprint({ title: __("Reconcile Late Check-ins"), message: msg });
				},
			});
		},
	});
	dialog.show();
};

frappe.ui.form.on("Shift Type", {
	refresh(frm) {
		if (frm.doc.__islocal || !frm.doc.enable_auto_attendance) return;

		frm.add_custom_button(__("Reconcile Late Check-ins"), () =>
			hrms_attendance_reconciliation.open_reconcile_dialog(frm.doc.name),
		);
	},
});

frappe.ui.form.on("HR Settings", {
	reconcile_late_checkins_now() {
		hrms_attendance_reconciliation.open_reconcile_dialog();
	},
});
