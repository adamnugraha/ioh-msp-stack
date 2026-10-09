"""GLPI REST client using the legacy REST API (apirest.php).

Chosen because it covers every object type msp-glue needs (entities, profiles, rules,
assets, tickets, changes) and is present in GLPI 10, 11 and 12. The newer high-level
API (api.php/v2.x) can replace it later without changing the rest of msp-glue.
"""

from __future__ import annotations

import logging
from typing import Any

from ..http import DEFAULT_TIMEOUT, APIError, make_session

log = logging.getLogger("msp_glue.glpi")

# Ticket and change status values used by GLPI
STATUS_NEW, STATUS_ASSIGNED, STATUS_PLANNED, STATUS_WAITING, STATUS_SOLVED, STATUS_CLOSED = 1, 2, 3, 4, 5, 6
CHANGE_ACCEPTED, CHANGE_EVALUATION, CHANGE_APPROVAL = 7, 9, 10
VALIDATION_WAITING, VALIDATION_ACCEPTED, VALIDATION_REFUSED = 2, 3, 4
TYPE_INCIDENT, TYPE_REQUEST = 1, 2


class GLPI:
    def __init__(self, url: str, user_token: str, app_token: str = "", timeout: int = DEFAULT_TIMEOUT):
        self.base = url.rstrip("/") + "/apirest.php"
        self.user_token = user_token
        self.app_token = app_token
        self.timeout = timeout
        self.session = make_session()
        self.session_token: str | None = None

    # ------------------------------------------------------------- sessions
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.app_token:
            headers["App-Token"] = self.app_token
        if self.session_token:
            headers["Session-Token"] = self.session_token
        return headers

    def open(self) -> "GLPI":
        headers = {"Content-Type": "application/json", "Authorization": f"user_token {self.user_token}"}
        if self.app_token:
            headers["App-Token"] = self.app_token
        response = self.session.get(f"{self.base}/initSession", headers=headers, timeout=self.timeout)
        self._raise(response, "initSession")
        self.session_token = response.json()["session_token"]
        self.refresh_entities()
        return self

    def refresh_entities(self) -> None:
        """Work from the root entity with recursion so every customer entity is in scope.

        GLPI fixes the list of entities a session may write to when this is called, so call it
        again after creating an entity, or GLPI refuses objects in the new entity
        ("You don't have permission to perform this action").
        """
        self.request("POST", "changeActiveEntities", {"entities_id": 0, "is_recursive": True})

    def close(self) -> None:
        if self.session_token:
            try:
                self.session.get(f"{self.base}/killSession", headers=self._headers(), timeout=self.timeout)
            finally:
                self.session_token = None

    def __enter__(self) -> "GLPI":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def _raise(response, what: str) -> None:
        if response.status_code >= 400:
            raise APIError(f"GLPI {what} failed: HTTP {response.status_code} {response.text[:500]}")

    def request(self, method: str, path: str, payload: Any = None, params: dict | None = None) -> Any:
        response = self.session.request(
            method, f"{self.base}/{path.lstrip('/')}", json=payload, params=params,
            headers=self._headers(), timeout=self.timeout,
        )
        self._raise(response, f"{method} {path}")
        if not response.content:
            return None
        return response.json()

    # --------------------------------------------------------------- generic
    def get(self, itemtype: str, item_id: int, /, **params) -> dict:
        return self.request("GET", f"{itemtype}/{item_id}", params=params or None)

    def find(self, itemtype: str, /, _deleted: bool = False, **criteria) -> list[dict]:
        """Exact-match search on simple fields, for example find("Entity", name="ACME").

        Uses the searchText filter of the legacy API (a 'contains' match), then keeps
        only exact matches so the result is reliable. _deleted=True searches the trash bin.
        """
        params = {"range": "0-999", "is_deleted": int(_deleted)}
        for key, value in criteria.items():
            params[f"searchText[{key}]"] = f"^{value}$" if isinstance(value, str) else value
        items = self.request("GET", itemtype, params=params) or []
        return [i for i in items if all(str(i.get(k)) == str(v) for k, v in criteria.items())]

    def add(self, itemtype: str, fields: dict) -> int:
        result = self.request("POST", itemtype, {"input": fields})
        if isinstance(result, list):
            result = result[0]
        if not result or not result.get("id"):
            raise APIError(f"GLPI add {itemtype} returned no id: {result}")
        return int(result["id"])

    def update(self, itemtype: str, item_id: int, fields: dict) -> None:
        self.request("PUT", f"{itemtype}/{item_id}", {"input": {"id": item_id, **fields}})

    def ensure(self, itemtype: str, match: dict, fields: dict | None = None) -> int:
        """Return the id of the item matching `match`, creating it (with match+fields) if missing."""
        found = self.find(itemtype, **match)
        if found:
            item_id = int(found[0]["id"])
            if fields:
                current = found[0]
                changed = {k: v for k, v in fields.items() if str(current.get(k)) != str(v)}
                if changed:
                    self.update(itemtype, item_id, changed)
            return item_id
        log.info("GLPI: creating %s %s", itemtype, match)
        return self.add(itemtype, {**match, **(fields or {})})

    # --------------------------------------------------------------- tickets
    def add_followup(self, itemtype: str, items_id: int, content: str, is_private: bool = False) -> int:
        return self.add("ITILFollowup", {"itemtype": itemtype, "items_id": items_id, "content": content,
                                         "is_private": int(is_private)})

    def link_item_to_ticket(self, ticket_id: int, itemtype: str, items_id: int) -> None:
        if self.find("Item_Ticket", tickets_id=ticket_id, itemtype=itemtype, items_id=items_id):
            return
        self.add("Item_Ticket", {"tickets_id": ticket_id, "itemtype": itemtype, "items_id": items_id})
