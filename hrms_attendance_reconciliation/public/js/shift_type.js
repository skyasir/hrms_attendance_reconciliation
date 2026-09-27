frappe.ui.form.on("Shift Type", {
	refresh(frm) {
		if (frm.doc.__islocal || !frm.doc.enable_auto_attendance) return;

		frm.add_custom_button(__("Reconcile Late Check-ins"), () => {
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
						method: "hrms_attendance_reconciliation.attendance_reconciliation.reconcile.reconcile_shift",
						args: { shift_type: frm.doc.name, ...values },
						freeze: true,
						freeze_message: __("Reconciling attendance..."),
						callback: ({ message: r }) => {
							if (!r.cancelled) {
								frappe.msgprint(__("No auto-marked Absent in this range has late check-ins."));
								return;
							}
							frappe.msgprint(
								__("{0} Absent attendance cancelled, {1} skipped check-ins re-opened.", [
									r.cancelled,
									r.reopened,
								]) + (r.result ? `<br><br>${__(r.result)}` : ""),
							);
						},
					});
				},
			});
			dialog.show();
		});
	},
});
