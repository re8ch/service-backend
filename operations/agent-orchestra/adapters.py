"""Protocol-aware Agent Orchestra adapters with typed calls."""

from __future__ import annotations

import json
import urllib.request
from typing import Any


class HttpJsonAdapter:
    def __init__(self, base_url: str, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def get(self, path: str) -> Any:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with urllib.request.urlopen(urllib.request.Request(self.base_url + path, headers=headers), timeout=10) as response:
            return json.load(response)


class Orchestra:
    def __init__(self, codex_app: Any, artifact_api: HttpJsonAdapter, litellm: HttpJsonAdapter):
        self.codex_app = codex_app
        self.artifact_api = artifact_api
        self.litellm = litellm

    def tasks_list(self) -> Any:
        return self.codex_app.tasks_list()

    def tasks_get(self, task_id: str) -> Any:
        return self.codex_app.tasks_get(task_id)

    def artifacts_list(self, task_id: str) -> Any:
        return self.artifact_api.get(f"/v1/tasks/{task_id}/artifacts")

    def artifacts_get(self, task_id: str, artifact_id: str) -> Any:
        return self.artifact_api.get(f"/v1/tasks/{task_id}/artifacts/{artifact_id}")

    def agents_status(self) -> Any:
        return self.codex_app.agents_status()

    def litellm_health(self) -> Any:
        return self.litellm.get("/health/readiness")


TOOLS = {"tasks.list", "tasks.get", "artifacts.list", "artifacts.get", "agents.status", "litellm.health"}
