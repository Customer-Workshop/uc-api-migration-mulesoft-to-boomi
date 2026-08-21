"""Small offline AtomSphere client.

A real migration points this client at a test Atom.  Without an API token,
calls are recorded as a replayable plan instead of sent to api.boomi.com.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


class AtomSphereClient:
    def __init__(self, token: str | None = None, report_path: Path | None = None):
        self.token = token or os.environ.get("BOOMI_API_TOKEN")
        self.report_path = report_path or Path(__file__).resolve().parent / "reports" / "atomsphere-plan.json"
        self.calls: list[dict[str, Any]] = []

    def _call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.token:
            record = {"action": action, "payload": payload}
            self.calls.append(record)
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            self.report_path.write_text(json.dumps(self.calls, indent=2) + "\n")
            return {"offline": True, **record}
        request = Request(
            f"https://api.boomi.com/api/rest/v1/{action}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Basic {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            return json.loads(response.read())

    def create_component(self, component: dict[str, Any]) -> dict[str, Any]:
        return self._call("Component", component)

    def deploy(self, component_id: str, environment_id: str) -> dict[str, Any]:
        return self._call("DeployedComponent", {"componentId": component_id, "environmentId": environment_id})

    def execute(self, process_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return self._call("ExecutionRequest", {"processId": process_id, "request": request})
