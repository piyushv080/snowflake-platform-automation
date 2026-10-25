/**
 * Script Include: AwxClient
 *
 * System properties (sys_properties):
 *   x_platform.awx.url     e.g. https://awx.example.com
 *   x_platform.awx.token   type password2 - AWX OAuth2 token (Users -> Tokens), scope "write"
 */
var AwxClient = Class.create();
AwxClient.prototype = {
    initialize: function () {
        this.url = gs.getProperty('x_platform.awx.url');
        this.token = gs.getProperty('x_platform.awx.token');
    },

    /** Launch a job template by numeric id. extraVars is a plain object. */
    launch: function (templateId, extraVars) {
        var rm = new sn_ws.RESTMessageV2();
        rm.setEndpoint(this.url + '/api/v2/job_templates/' + templateId + '/launch/');
        rm.setHttpMethod('POST');
        rm.setRequestHeader('Content-Type', 'application/json');
        rm.setRequestHeader('Authorization', 'Bearer ' + this.token);
        rm.setRequestBody(JSON.stringify({ extra_vars: extraVars }));
        try {
            var resp = rm.execute();
            var status = resp.getStatusCode();
            var body = resp.getBody();
            var job = status === 201 ? JSON.parse(body).job : null;
            return { ok: status === 201, status: status, job: job, body: body };
        } catch (e) {
            return { ok: false, status: 0, job: null, body: String(e) };
        }
    },

    type: 'AwxClient'
};
