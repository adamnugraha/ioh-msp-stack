"""Small Zabbix JSON-RPC client (tested with Zabbix 7.0 LTS, written to stay valid on 8.0).

Avoids methods removed in Zabbix 8.0 (host.massupdate, hostinterface.replacehostinterfaces).
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

from ..http import DEFAULT_TIMEOUT, APIError, make_session

log = logging.getLogger("msp_glue.zabbix")

PERM_DENY, PERM_READ, PERM_READ_WRITE = 0, 2, 3


class Zabbix:
    def __init__(self, url: str, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.endpoint = url.rstrip("/") + "/api_jsonrpc.php"
        self.token = token
        self.timeout = timeout
        self.session = make_session()
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ core
    def call(self, method: str, params: Any = None, auth: bool = True) -> Any:
        payload = {"jsonrpc": "2.0", "method": method, "params": params if params is not None else {}, "id": next(self._ids)}
        headers = {"Content-Type": "application/json-rpc"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = self.session.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            err = body["error"]
            raise APIError(f"Zabbix {method} failed: {err.get('message')} {err.get('data')}")
        return body["result"]

    def version(self) -> str:
        return self.call("apiinfo.version", {}, auth=False)

    @classmethod
    def login(cls, url: str, username: str, password: str) -> "Zabbix":
        """Log in with a user name and password (used once by the bootstrap to create a token)."""
        client = cls(url, token="")
        session_id = client.call("user.login", {"username": username, "password": password}, auth=False)
        client.token = session_id
        return client

    # ------------------------------------------------------------ host groups
    def ensure_hostgroup(self, name: str) -> str:
        found = self.call("hostgroup.get", {"filter": {"name": [name]}, "output": ["groupid"]})
        if found:
            return found[0]["groupid"]
        log.info("Zabbix: creating host group %s", name)
        return self.call("hostgroup.create", {"name": name})["groupids"][0]

    def hostgroups_with_prefix(self, prefix: str) -> list[dict]:
        groups = self.call("hostgroup.get", {"search": {"name": prefix}, "startSearch": True, "output": ["groupid", "name"]})
        return [g for g in groups if g["name"] == prefix or g["name"].startswith(prefix + "/")]

    def ensure_templategroup(self, name: str) -> str:
        found = self.call("templategroup.get", {"filter": {"name": [name]}, "output": ["groupid"]})
        if found:
            return found[0]["groupid"]
        return self.call("templategroup.create", {"name": name})["groupids"][0]

    # ------------------------------------------------------------ user groups
    def ensure_usergroup(self, name: str, hostgroup_rights: list[tuple[str, int]], gui_access: int = 0) -> str:
        rights = [{"id": gid, "permission": perm} for gid, perm in hostgroup_rights]
        found = self.call("usergroup.get", {"filter": {"name": [name]}, "output": ["usrgrpid"]})
        if found:
            usrgrpid = found[0]["usrgrpid"]
            self.call("usergroup.update", {"usrgrpid": usrgrpid, "hostgroup_rights": rights})
            return usrgrpid
        log.info("Zabbix: creating user group %s", name)
        return self.call(
            "usergroup.create",
            {"name": name, "gui_access": gui_access, "users_status": 0, "hostgroup_rights": rights},
        )["usrgrpids"][0]

    # ------------------------------------------------------------- templates
    def template_ids(self, names: list[str]) -> dict[str, str]:
        if not names:
            return {}
        found = self.call("template.get", {"filter": {"host": names}, "output": ["templateid", "host"]})
        result = {t["host"]: t["templateid"] for t in found}
        missing = sorted(set(names) - set(result))
        if missing:
            log.warning("Zabbix: templates not found (check mappings.yaml): %s", ", ".join(missing))
        return result

    def ensure_macro_template(self, name: str, group_id: str, macros: list[dict], description: str = "") -> str:
        """A template that only carries macros (for example one customer's SNMP credentials).

        Hosts linked to it inherit the macros. Secret values are stored as secret macros.
        """
        found = self.call("template.get", {"filter": {"host": [name]}, "output": ["templateid"]})
        if found:
            templateid = found[0]["templateid"]
            self.call("template.update", {"templateid": templateid, "macros": macros, "description": description})
            return templateid
        log.info("Zabbix: creating macro template %s", name)
        return self.call(
            "template.create",
            {"host": name, "groups": [{"groupid": group_id}], "macros": macros, "description": description},
        )["templateids"][0]

    def ensure_api_reader_role(self, name: str = "MSP API reader") -> str:
        """A user-type role that may call the API but not open the web interface.

        Zabbix 8.0 turns API access off for every role by default, so read-only integration
        accounts (Grafana) get this explicit role instead of the built-in "User role".
        """
        rules = {"ui.default_access": 0, "api.access": 1, "api.mode": 0, "actions.default_access": 0,
                 "modules.default_access": 0}
        found = self.call("role.get", {"filter": {"name": [name]}, "output": ["roleid"]})
        if found:
            self.call("role.update", {"roleid": found[0]["roleid"], "rules": rules})
            return found[0]["roleid"]
        return self.call("role.create", {"name": name, "type": 1, "rules": rules})["roleids"][0]

    # --------------------------------------------------------------- proxies
    def ensure_proxy_group(self, name: str, failover_delay: str = "1m", min_online: str = "1") -> str:
        found = self.call("proxygroup.get", {"filter": {"name": [name]}, "output": ["proxy_groupid"]})
        if found:
            return found[0]["proxy_groupid"]
        log.info("Zabbix: creating proxy group %s", name)
        return self.call(
            "proxygroup.create", {"name": name, "failover_delay": failover_delay, "min_online": min_online}
        )["proxy_groupids"][0]

    def proxy_group_id(self, name: str) -> str | None:
        found = self.call("proxygroup.get", {"filter": {"name": [name]}, "output": ["proxy_groupid"]})
        return found[0]["proxy_groupid"] if found else None

    def ensure_active_proxy(self, name: str, proxy_groupid: str | None = None, psk_identity: str = "", psk: str = "",
                            local_address: str = "") -> str:
        """Register an active proxy (the proxy connects out to the server, so no inbound firewall rule
        is needed at the customer site). Encrypted with a pre-shared key when one is given."""
        params: dict[str, Any] = {"name": name, "operating_mode": 0}
        if proxy_groupid:
            params["proxy_groupid"] = proxy_groupid
            params["local_port"] = "10051"
            if local_address:
                params["local_address"] = local_address
        if psk and psk_identity:
            params.update({"tls_connect": 1, "tls_accept": 2, "tls_psk_identity": psk_identity, "tls_psk": psk})
        found = self.call("proxy.get", {"filter": {"name": [name]}, "output": ["proxyid"]})
        if found:
            # Without a new address the proxy keeps the one it has.
            params["proxyid"] = found[0]["proxyid"]
            self.call("proxy.update", params)
            return found[0]["proxyid"]
        if proxy_groupid:
            # A proxy in a group needs the address the agents and server use to reach it.
            params.setdefault("local_address", name)
        log.info("Zabbix: registering proxy %s", name)
        return self.call("proxy.create", params)["proxyids"][0]

    # ----------------------------------------------------------------- hosts
    def managed_hosts(self, tag: str = "managed_by", value: str = "msp-glue") -> list[dict]:
        return self.call(
            "host.get",
            {
                "output": ["hostid", "host", "name", "status", "monitored_by", "proxyid", "proxy_groupid"],
                "tags": [{"tag": tag, "value": value, "operator": 1}],
                "selectTags": ["tag", "value"],
                "selectHostGroups": ["groupid", "name"],
                "selectParentTemplates": ["templateid", "host"],
                "selectInterfaces": ["interfaceid", "type", "ip", "dns", "useip", "port", "main", "details"],
            },
        )

    def host_by_name(self, technical_name: str) -> dict | None:
        found = self.call(
            "host.get",
            {
                "output": ["hostid", "host", "name", "status", "monitored_by", "proxyid", "proxy_groupid"],
                "filter": {"host": [technical_name]},
                "selectTags": ["tag", "value"],
                "selectHostGroups": ["groupid", "name"],
                "selectParentTemplates": ["templateid", "host"],
                "selectInterfaces": ["interfaceid", "type", "ip", "dns", "useip", "port", "main", "details"],
            },
        )
        return found[0] if found else None

    def host_create(self, params: dict) -> str:
        return self.call("host.create", params)["hostids"][0]

    def host_update(self, params: dict) -> None:
        self.call("host.update", params)

    def interface_update(self, params: dict) -> None:
        self.call("hostinterface.update", params)

    # --------------------------------------------------------- notifications
    def ensure_global_macro(self, macro: str, value: str) -> None:
        found = self.call("usermacro.get", {"globalmacro": True, "filter": {"macro": macro}, "output": ["globalmacroid", "value"]})
        if found:
            if found[0].get("value") != value:
                self.call("usermacro.updateglobal", {"globalmacroid": found[0]["globalmacroid"], "value": value})
            return
        self.call("usermacro.createglobal", {"macro": macro, "value": value})
