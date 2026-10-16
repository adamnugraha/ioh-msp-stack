// =============================================================================
// Zabbix webhook "Versus chat": posts problems, updates and recoveries on managed devices to
// Versus Incident, which sends them to the NOC's Telegram group, Teams channel or mailbox.
// Written for Zabbix 7.0+ (ES5, Duktape engine). The copy inside media_versus_chat.yaml is the one
// Zabbix runs; keep both the same.
//
// The fields sent are the ones the message templates use (components/versus/templates):
//   kind (problem, update, resolved), severity, nseverity, problem, customer, customer_code, device,
//   site, address, started, duration, opdata, event_id, event_url, ticket_id, ticket_url,
//   console_url, update_user, update_action, update_message
// =============================================================================

function isMacro(value) {
    return typeof value === 'string' && /^\{[^}]+\}$/.test(value.trim());
}

function clean(value) {
    if (value === undefined || value === null || isMacro(String(value))) {
        return '';
    }
    return String(value).trim();
}

function tagMap(tagsJson) {
    var map = {},
        list;

    try {
        list = JSON.parse(tagsJson || '[]');
    }
    catch (error) {
        list = [];
    }
    for (var i = 0; i < list.length; i++) {
        map[list[i].tag] = list[i].value;
    }
    return map;
}

function trimUrl(url) {
    return clean(url).replace(/\/+$/, '');
}

try {
    var p = JSON.parse(value);

    if (!clean(p.versus_url)) {
        throw 'Missing required parameter: versus_url';
    }
    // Only trigger events (source 0) are sent.
    if (String(p.event_source) !== '0') {
        return 'OK';
    }

    var tags = tagMap(p.event_tags),
        kind = String(p.event_update_status) === '1' ? 'update' : (String(p.event_value) === '0' ? 'resolved' : 'problem'),
        ticketId = clean(p.glpi_ticket_id),
        code = clean(tags.tenant),
        zabbixUrl = trimUrl(p.zabbix_url),
        glpiUrl = trimUrl(p.glpi_public_url),
        consoleUrl = trimUrl(p.console_url),
        body = {
            kind: kind,
            severity: clean(p.event_severity),
            nseverity: parseInt(clean(p.event_nseverity) || '0', 10),
            problem: clean(p.event_name),
            customer: clean(tags.tenant_name) || code || 'Unassigned',
            customer_code: code,
            device: clean(tags.device) || clean(p.host_name),
            site: clean(tags.site),
            address: clean(p.host_ip),
            started: clean(p.event_date) + ' ' + clean(p.event_time),
            duration: clean(p.event_duration),
            opdata: clean(p.event_opdata),
            event_id: clean(p.event_id),
            event_url: zabbixUrl + '/tr_events.php?triggerid=' + clean(p.trigger_id) + '&eventid=' + clean(p.event_id),
            ticket_id: ticketId,
            ticket_url: (ticketId && glpiUrl) ? glpiUrl + '/front/ticket.form.php?id=' + ticketId : '',
            console_url: (consoleUrl && code) ? consoleUrl + '/customers/' + encodeURIComponent(code) : '',
            update_user: clean(p.update_user),
            update_action: clean(p.update_action),
            update_message: clean(p.update_message)
        };

    // The MSP Console acknowledges as its service account and names the engineer in the message:
    // show the engineer instead of the service account. The shortest name that is followed by an
    // e-mail address in brackets wins, so brackets in the note itself do not move the split.
    var fromConsole = /^(Acknowledged|Note) in the MSP Console by (.+?) \(([^()\s]+@[^()\s]+)\)(?:: ([\s\S]*)|\.)$/.exec(body.update_message);
    if (kind === 'update' && fromConsole) {
        body.update_user = fromConsole[2];
        body.update_action = fromConsole[1] === 'Acknowledged' ? 'acknowledged in the MSP Console' : 'commented in the MSP Console';
        body.update_message = fromConsole[4] || '';
    }

    var req = new HttpRequest(),
        response;

    req.addHeader('Content-Type: application/json');
    if (clean(p.http_proxy)) {
        req.setProxy(p.http_proxy);
    }
    response = req.post(p.versus_url, JSON.stringify(body));
    var status = req.getStatus();
    Zabbix.log(4, '[Versus chat] HTTP ' + status + ': ' + response);
    if (status < 200 || status >= 300) {
        throw 'Versus returned HTTP ' + status + ': ' + response;
    }
    return 'OK';
}
catch (error) {
    Zabbix.log(3, '[Versus chat] notification failed: ' + error);
    throw 'Sending failed: ' + error;
}
