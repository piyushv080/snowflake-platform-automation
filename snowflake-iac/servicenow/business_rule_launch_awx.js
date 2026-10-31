/**
 * Business Rule: "Launch AWX on Snowflake CR implement"
 * Table:     change_request
 * When:      after, update
 * Condition: current.state.changesTo(-1) && current.u_platform == 'snowflake'   // -1 = Implement
 *
 * Launches the matching AWX job template. The playbook validates the CR state itself
 * (servicenow_change/gate_start) and writes work notes back when it finishes.
 */
(function executeRule(current, previous /* null when async */) {
    var templateIds = {
        provision: gs.getProperty('x_platform.awx.template.provision'),
        user_lifecycle: gs.getProperty('x_platform.awx.template.user_lifecycle')
    };
    var action = current.getValue('u_snowflake_action') || 'provision';
    var templateId = templateIds[action];
    if (!templateId) {
        current.work_notes = 'No AWX template configured for action ' + action;
        current.update();
        return;
    }

    var res = new AwxClient().launch(templateId, {
        cr_number: current.getValue('number'),
        servicenow_enabled: true
    });

    current.work_notes = res.ok
        ? 'AWX job ' + res.job + ' launched for ' + action + '.'
        : 'AWX launch failed (HTTP ' + res.status + '): ' + res.body;
    if (!res.ok) {
        gs.error('[SnowflakeCR] AWX launch failed for ' + current.getValue('number') + ': ' + res.body);
    }
    current.update();
})(current, previous);
