/**
 * Conditional field visibility for ScheduledTask admin.
 *
 * Shows/hides schedule-type-specific fieldsets based on the selected schedule_type.
 * - cron    -> shows "Cron Schedule" fieldset
 * - interval -> shows "Interval Schedule" fieldset
 * - once    -> shows "One-Time Schedule" fieldset
 */
(function() {
    'use strict';

    function updateScheduleFieldsets() {
        var scheduleType = document.getElementById('id_schedule_type');
        if (!scheduleType) return;

        var value = scheduleType.value;

        // Find fieldsets by their CSS class
        var fieldsets = document.querySelectorAll('fieldset');
        fieldsets.forEach(function(fieldset) {
            var classes = fieldset.className || '';

            if (classes.indexOf('schedule-cron') !== -1) {
                fieldset.style.display = (value === 'cron') ? '' : 'none';
            } else if (classes.indexOf('schedule-interval') !== -1) {
                fieldset.style.display = (value === 'interval') ? '' : 'none';
            } else if (classes.indexOf('schedule-once') !== -1) {
                fieldset.style.display = (value === 'once') ? '' : 'none';
            }
        });
    }

    // Run on page load
    document.addEventListener('DOMContentLoaded', function() {
        updateScheduleFieldsets();

        // Bind change event to schedule_type select
        var scheduleType = document.getElementById('id_schedule_type');
        if (scheduleType) {
            scheduleType.addEventListener('change', updateScheduleFieldsets);
        }
    });
})();
