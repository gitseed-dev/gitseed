from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class GitSeedApiError(Exception):
    """Raised when the upstream GitSeed Control Plane API returns an error or is unreachable."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload


def _encode_segment(value: str) -> str:
    return quote(str(value), safe="")


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None}


class GitSeedApiClient:
    """Async HTTP client for the GitSeed Control Plane REST API."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip() if token and token.strip() else None
        self.timeout = timeout
        self._transport = transport
        self._http_client: httpx.AsyncClient | None = http_client

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=self.timeout,
                transport=self._transport,
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None

    async def __aenter__(self) -> GitSeedApiClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "gitseed-mcp/0.1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        cleaned_params = _clean_params(params) if params else None

        try:
            client = self._get_http_client()
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=self._headers(),
                params=cleaned_params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise GitSeedApiError(
                f"Failed to reach GitSeed API at {self.base_url}: {exc}"
            ) from exc

        if response.status_code == 204 or not response.content:
            if response.is_success:
                return {"ok": True, "status_code": response.status_code}

        parsed: Any = None
        try:
            parsed = response.json()
        except ValueError:
            parsed = response.text

        if not response.is_success:
            detail_msg = self._extract_error_detail(parsed, response.status_code)
            raise GitSeedApiError(
                detail_msg,
                status_code=response.status_code,
                payload=parsed,
            )

        return parsed

    @staticmethod
    def _extract_error_detail(payload: Any, status_code: int) -> str:
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return f"GitSeed API error ({status_code}): {detail}"
            if isinstance(detail, list):
                parts: list[str] = []
                for item in detail:
                    if isinstance(item, dict):
                        loc = ".".join(str(x) for x in item.get("loc", []))
                        msg = str(item.get("msg", "validation error"))
                        parts.append(f"{loc}: {msg}" if loc else msg)
                    else:
                        parts.append(str(item))
                if parts:
                    return f"GitSeed API validation error ({status_code}): {'; '.join(parts)}"
            message = payload.get("message") or payload.get("error")
            if isinstance(message, str) and message.strip():
                return f"GitSeed API error ({status_code}): {message}"
        if isinstance(payload, str) and payload.strip():
            return f"GitSeed API error ({status_code}): {payload.strip()}"
        return f"GitSeed API error ({status_code})"

    # ── System & Auth ─────────────────────────────────────────────────

    async def get_health(self) -> Any:
        return await self.request("GET", "/healthz")

    async def get_version(self) -> Any:
        return await self.request("GET", "/version")

    async def get_current_user(self) -> Any:
        return await self.request("GET", "/api/v1/auth/me")

    # ── Projects & Policies ───────────────────────────────────────────

    async def list_projects(
        self,
        *,
        include_archived: bool = False,
        tag: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/v1/projects",
            params={"include_archived": include_archived, "tag": tag},
        )

    async def get_project(self, project_id: str) -> Any:
        return await self.request("GET", f"/api/v1/projects/{_encode_segment(project_id)}")

    async def create_project(
        self,
        *,
        name: str,
        slug: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        visibility: str = "private",
        monthly_budget: float | None = None,
        seed_budget_cap: float | None = None,
        spend_checkpoint: float | None = None,
        max_concurrent_runs: int | None = None,
    ) -> Any:
        body = _clean_params(
            {
                "name": name,
                "slug": slug,
                "description": description,
                "tags": tags,
                "visibility": visibility,
                "monthly_budget": monthly_budget,
                "seed_budget_cap": seed_budget_cap,
                "spend_checkpoint": spend_checkpoint,
                "max_concurrent_runs": max_concurrent_runs,
            }
        )
        return await self.request("POST", "/api/v1/projects", json_body=body)

    async def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        visibility: str | None = None,
        monthly_budget: float | None = None,
        seed_budget_cap: float | None = None,
        spend_checkpoint: float | None = None,
        max_concurrent_runs: int | None = None,
    ) -> Any:
        body = _clean_params(
            {
                "name": name,
                "description": description,
                "tags": tags,
                "visibility": visibility,
                "monthly_budget": monthly_budget,
                "seed_budget_cap": seed_budget_cap,
                "spend_checkpoint": spend_checkpoint,
                "max_concurrent_runs": max_concurrent_runs,
            }
        )
        return await self.request(
            "PATCH",
            f"/api/v1/projects/{_encode_segment(project_id)}",
            json_body=body,
        )

    async def get_project_policies(self, project_id: str) -> Any:
        return await self.request(
            "GET", f"/api/v1/policies/projects/{_encode_segment(project_id)}"
        )

    async def list_org_policies(self) -> Any:
        return await self.request("GET", "/api/v1/policies")

    async def update_org_policy(
        self,
        stage: str,
        *,
        enabled: bool | None = None,
        agent_id: str | None = None,
        requires_approval: bool | None = None,
        min_approvers: int | None = None,
        min_approver_role: str | None = None,
        allow_auto_approve: bool | None = None,
        allow_self_approval: bool | None = None,
        max_attempts: int | None = None,
        timeout_minutes: int | None = None,
        min_acceptance_criteria: int | None = None,
        max_acceptance_criteria: int | None = None,
        max_tasks: int | None = None,
        instructions: str | None = None,
    ) -> Any:
        body = _clean_params(
            {
                "enabled": enabled,
                "agent_id": agent_id,
                "requires_approval": requires_approval,
                "min_approvers": min_approvers,
                "min_approver_role": min_approver_role,
                "allow_auto_approve": allow_auto_approve,
                "allow_self_approval": allow_self_approval,
                "max_attempts": max_attempts,
                "timeout_minutes": timeout_minutes,
                "min_acceptance_criteria": min_acceptance_criteria,
                "max_acceptance_criteria": max_acceptance_criteria,
                "max_tasks": max_tasks,
                "instructions": instructions,
            }
        )
        return await self.request(
            "PATCH",
            f"/api/v1/policies/{_encode_segment(stage)}",
            json_body=body,
        )

    async def update_project_policy(
        self,
        project_id: str,
        stage: str,
        *,
        enabled: bool | None = None,
        agent_id: str | None = None,
        requires_approval: bool | None = None,
        min_approvers: int | None = None,
        min_approver_role: str | None = None,
        allow_auto_approve: bool | None = None,
        allow_self_approval: bool | None = None,
        max_attempts: int | None = None,
        timeout_minutes: int | None = None,
        min_acceptance_criteria: int | None = None,
        max_acceptance_criteria: int | None = None,
        max_tasks: int | None = None,
        instructions: str | None = None,
    ) -> Any:
        body = _clean_params(
            {
                "enabled": enabled,
                "agent_id": agent_id,
                "requires_approval": requires_approval,
                "min_approvers": min_approvers,
                "min_approver_role": min_approver_role,
                "allow_auto_approve": allow_auto_approve,
                "allow_self_approval": allow_self_approval,
                "max_attempts": max_attempts,
                "timeout_minutes": timeout_minutes,
                "min_acceptance_criteria": min_acceptance_criteria,
                "max_acceptance_criteria": max_acceptance_criteria,
                "max_tasks": max_tasks,
                "instructions": instructions,
            }
        )
        return await self.request(
            "PATCH",
            f"/api/v1/policies/projects/{_encode_segment(project_id)}/{_encode_segment(stage)}",
            json_body=body,
        )

    async def reset_project_policy(self, project_id: str, stage: str) -> Any:
        return await self.request(
            "DELETE",
            f"/api/v1/policies/projects/{_encode_segment(project_id)}/{_encode_segment(stage)}",
        )

    # ── Repositories & Code Browsing ──────────────────────────────────

    async def list_project_repositories(self, project_id: str) -> Any:
        return await self.request(
            "GET", f"/api/v1/projects/{_encode_segment(project_id)}/repositories"
        )

    async def list_repository_branches(
        self, project_id: str, repo_id: int | str
    ) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/projects/{_encode_segment(project_id)}/repositories/{_encode_segment(str(repo_id))}/branches",
        )

    async def get_repository_tree(
        self,
        project_id: str,
        repo_id: int | str,
        *,
        ref: str | None = None,
        path: str = "",
    ) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/projects/{_encode_segment(project_id)}/repositories/{_encode_segment(str(repo_id))}/tree",
            params={"ref": ref, "path": path},
        )

    async def get_repository_blob(
        self,
        project_id: str,
        repo_id: int | str,
        *,
        path: str,
        ref: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/projects/{_encode_segment(project_id)}/repositories/{_encode_segment(str(repo_id))}/blob",
            params={"path": path, "ref": ref},
        )

    # ── Seeds ─────────────────────────────────────────────────────────

    async def list_seeds(
        self,
        *,
        project_id: str | None = None,
        stage: str | None = None,
        status: str | None = None,
        open_only: bool = False,
        autonomy: str | None = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/v1/seeds",
            params={
                "project_id": project_id,
                "stage": stage,
                "status": status,
                "open_only": open_only,
                "autonomy": autonomy,
                "tag": tag,
                "limit": limit,
            },
        )

    async def get_seed(self, seed_id: str) -> Any:
        return await self.request("GET", f"/api/v1/seeds/{_encode_segment(seed_id)}")

    async def create_seed(
        self,
        *,
        project_id: str,
        title: str,
        description: str | None = None,
        specification: str | None = None,
        acceptance_criteria: list[str] | None = None,
        plan: str | None = None,
        tags: list[str] | None = None,
        stage: str | None = None,
        fast_track: bool | None = None,
        staged: bool | None = None,
        autonomy: str = "supervised",
        budget_cap: float | None = None,
    ) -> Any:
        body = _clean_params(
            {
                "project_id": project_id,
                "title": title,
                "description": description,
                "specification": specification,
                "acceptance_criteria": acceptance_criteria,
                "plan": plan,
                "tags": tags,
                "stage": stage,
                "fast_track": fast_track,
                "staged": staged,
                "autonomy": autonomy,
                "budget_cap": budget_cap,
            }
        )
        return await self.request("POST", "/api/v1/seeds", json_body=body)

    async def update_seed(
        self,
        seed_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        specification: str | None = None,
        acceptance_criteria: list[str] | None = None,
        plan: str | None = None,
        tags: list[str] | None = None,
        autonomy: str | None = None,
        budget_cap: float | None = None,
    ) -> Any:
        body = _clean_params(
            {
                "title": title,
                "description": description,
                "specification": specification,
                "acceptance_criteria": acceptance_criteria,
                "plan": plan,
                "tags": tags,
                "autonomy": autonomy,
                "budget_cap": budget_cap,
            }
        )
        return await self.request(
            "PATCH",
            f"/api/v1/seeds/{_encode_segment(seed_id)}",
            json_body=body,
        )

    async def start_seed(self, seed_id: str) -> Any:
        return await self.request(
            "POST", f"/api/v1/seeds/{_encode_segment(seed_id)}/start"
        )

    async def advance_seed(
        self,
        seed_id: str,
        *,
        stage: str | None = None,
        fast_track: bool | None = None,
    ) -> Any:
        body = _clean_params({"stage": stage, "fast_track": fast_track})
        return await self.request(
            "POST",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/advance",
            json_body=body,
        )

    async def move_seed_back(
        self,
        seed_id: str,
        *,
        stage: str,
        reason: str,
    ) -> Any:
        return await self.request(
            "POST",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/move-back",
            json_body={"stage": stage, "reason": reason},
        )

    async def stop_seed(self, seed_id: str) -> Any:
        return await self.request(
            "POST", f"/api/v1/seeds/{_encode_segment(seed_id)}/stop"
        )

    async def resume_seed(self, seed_id: str) -> Any:
        return await self.request(
            "POST", f"/api/v1/seeds/{_encode_segment(seed_id)}/resume"
        )

    async def approve_seed_checkpoint(
        self,
        seed_id: str,
        *,
        approved_through: float | None = None,
    ) -> Any:
        body: dict[str, Any] = (
            {"approved_through": approved_through} if approved_through is not None else {}
        )
        return await self.request(
            "POST",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/approve-checkpoint",
            json_body=body,
        )

    async def close_seed(self, seed_id: str) -> Any:
        return await self.request("POST", f"/api/v1/seeds/{_encode_segment(seed_id)}/close")

    async def reopen_seed(self, seed_id: str) -> Any:
        return await self.request("POST", f"/api/v1/seeds/{_encode_segment(seed_id)}/reopen")

    async def get_seed_changes(
        self,
        seed_id: str,
        *,
        repo_id: int | str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/changes",
            params={"repo_id": repo_id},
        )

    async def get_seed_receipt(self, seed_id: str) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/receipt",
        )

    # ── Seed Approvals (Stage Gates) ──────────────────────────────────

    async def list_seed_approvals(self, seed_id: str) -> Any:
        return await self.request(
            "GET", f"/api/v1/seeds/{_encode_segment(seed_id)}/approvals"
        )

    async def submit_seed_approval(
        self,
        seed_id: str,
        *,
        decision: str,
        comment: str | None = None,
    ) -> Any:
        body = _clean_params({"decision": decision, "comment": comment})
        return await self.request(
            "POST",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/approvals",
            json_body=body,
        )

    # ── Seed Tasks ────────────────────────────────────────────────────

    async def list_seed_tasks(self, seed_id: str) -> Any:
        return await self.request("GET", f"/api/v1/seeds/{_encode_segment(seed_id)}/tasks")

    async def create_seed_task(
        self,
        seed_id: str,
        *,
        title: str,
        description: str | None = None,
        position: int | None = None,
    ) -> Any:
        body = _clean_params(
            {"title": title, "description": description, "position": position}
        )
        return await self.request(
            "POST",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/tasks",
            json_body=body,
        )

    async def update_seed_task(
        self,
        seed_id: str,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        position: int | None = None,
        status: str | None = None,
    ) -> Any:
        body = _clean_params(
            {
                "title": title,
                "description": description,
                "position": position,
                "status": status,
            }
        )
        return await self.request(
            "PATCH",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/tasks/{_encode_segment(task_id)}",
            json_body=body,
        )

    async def delete_seed_task(self, seed_id: str, task_id: str) -> Any:
        return await self.request(
            "DELETE",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/tasks/{_encode_segment(task_id)}",
        )

    # ── Seed Comments & @agent Steering ───────────────────────────────

    async def list_seed_comments(self, seed_id: str) -> Any:
        return await self.request("GET", f"/api/v1/seeds/{_encode_segment(seed_id)}/comments")

    async def create_seed_comment(self, seed_id: str, *, body: str) -> Any:
        return await self.request(
            "POST",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/comments",
            json_body={"body": body},
        )

    async def update_seed_comment(
        self, seed_id: str, comment_id: str, *, body: str
    ) -> Any:
        return await self.request(
            "PATCH",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/comments/{_encode_segment(comment_id)}",
            json_body={"body": body},
        )

    async def delete_seed_comment(self, seed_id: str, comment_id: str) -> Any:
        return await self.request(
            "DELETE",
            f"/api/v1/seeds/{_encode_segment(seed_id)}/comments/{_encode_segment(comment_id)}",
        )

    # ── Runs ──────────────────────────────────────────────────────────

    async def list_runs(
        self,
        *,
        seed_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        stage: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/v1/runs",
            params={
                "seed_id": seed_id,
                "project_id": project_id,
                "agent_id": agent_id,
                "task_id": task_id,
                "stage": stage,
                "status": status,
                "active_only": active_only,
                "limit": limit,
            },
        )

    async def get_run(self, run_id: str) -> Any:
        return await self.request("GET", f"/api/v1/runs/{_encode_segment(run_id)}")

    async def create_run(
        self,
        *,
        seed_id: str,
        task_id: str | None = None,
        stage: str | None = None,
        agent_id: str | None = None,
        compute_backend_id: str | None = None,
    ) -> Any:
        body = _clean_params(
            {
                "seed_id": seed_id,
                "task_id": task_id,
                "stage": stage,
                "agent_id": agent_id,
                "compute_backend_id": compute_backend_id,
            }
        )
        return await self.request("POST", "/api/v1/runs", json_body=body)

    async def cancel_run(self, run_id: str) -> Any:
        return await self.request("POST", f"/api/v1/runs/{_encode_segment(run_id)}/cancel")

    async def get_run_logs(self, run_id: str, *, tail: int = 4000) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/runs/{_encode_segment(run_id)}/logs",
            params={"tail": tail},
        )

    # ── Agents & Coordinator ──────────────────────────────────────────

    async def list_agents(
        self,
        *,
        kind: str | None = None,
        enabled: bool | None = None,
        include_archived: bool = False,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/v1/agents",
            params={
                "kind": kind,
                "enabled": enabled,
                "include_archived": include_archived,
            },
        )

    async def get_agent(self, agent_id: str) -> Any:
        return await self.request("GET", f"/api/v1/agents/{_encode_segment(agent_id)}")

    async def get_agent_benchmarks(
        self,
        *,
        project_id: str | None = None,
        stage: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/v1/agents/benchmarks",
            params={"project_id": project_id, "stage": stage},
        )

    async def ask_coordinator(
        self,
        *,
        messages: list[dict[str, str]],
        project_id: str | None = None,
        seed_id: str | None = None,
        mode: str = "general",
        propose_seeds: bool = False,
    ) -> Any:
        body = _clean_params(
            {
                "messages": messages,
                "project_id": project_id,
                "seed_id": seed_id,
                "mode": mode,
                "propose_seeds": propose_seeds,
            }
        )
        return await self.request(
            "POST",
            "/api/v1/agents/coordinator/chat",
            json_body=body,
        )

    # ── Skills & Backends ─────────────────────────────────────────────

    async def list_skills(self, *, include_archived: bool = False) -> Any:
        return await self.request(
            "GET",
            "/api/v1/skills",
            params={"include_archived": include_archived},
        )

    async def get_skill(self, skill_id: str) -> Any:
        return await self.request("GET", f"/api/v1/skills/{_encode_segment(skill_id)}")

    async def list_backends(
        self,
        *,
        backend_type: str | None = None,
        enabled: bool | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/v1/backends",
            params={"type": backend_type, "enabled": enabled},
        )

    async def get_backend(self, backend_id: str) -> Any:
        return await self.request(
            "GET", f"/api/v1/backends/{_encode_segment(backend_id)}"
        )

    # ── Activity Audit Trail ──────────────────────────────────────────

    async def list_activity(
        self,
        *,
        project_id: str | None = None,
        seed_id: str | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        before_id: int | str | None = None,
        limit: int = 50,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/v1/activity",
            params={
                "project_id": project_id,
                "seed_id": seed_id,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "type": event_type,
                "before_id": before_id,
                "limit": limit,
            },
        )

    # ── Sandboxes & Interactive Sessions ──────────────────────────────

    async def get_sandbox_config(
        self,
        project_id: str,
        *,
        repo_id: int | str | None = None,
        ref: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/projects/{_encode_segment(project_id)}/sandboxes/config",
            params={"repo_id": repo_id, "ref": ref},
        )

    async def list_sandboxes(self, project_id: str, *, limit: int = 50) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/projects/{_encode_segment(project_id)}/sandboxes",
            params={"limit": limit},
        )

    async def create_sandbox(
        self,
        project_id: str,
        *,
        repository_id: int | str | None = None,
        seed_id: str | None = None,
        ref: str | None = None,
        image: str | None = None,
        setup_command: str | None = None,
        command: str | None = None,
        artifact_globs: list[str] | None = None,
        initial_stdin: str | None = None,
        kind: str | None = None,
        title: str | None = None,
        as_root: bool | None = None,
    ) -> Any:
        body = _clean_params(
            {
                "repository_id": repository_id,
                "seed_id": seed_id,
                "ref": ref,
                "image": image,
                "setup_command": setup_command,
                "command": command,
                "artifact_globs": artifact_globs,
                "initial_stdin": initial_stdin,
                "kind": kind,
                "title": title,
                "as_root": as_root,
            }
        )
        return await self.request(
            "POST",
            f"/api/v1/projects/{_encode_segment(project_id)}/sandboxes",
            json_body=body,
        )

    async def get_sandbox(self, project_id: str, sandbox_id: str) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/projects/{_encode_segment(project_id)}/sandboxes/{_encode_segment(sandbox_id)}",
        )

    async def send_sandbox_input(
        self,
        project_id: str,
        sandbox_id: str,
        *,
        input_text: str = "",
        append_newline: bool = True,
        eof: bool = False,
    ) -> Any:
        body = {
            "input": input_text,
            "append_newline": append_newline,
            "eof": eof,
        }
        return await self.request(
            "POST",
            f"/api/v1/projects/{_encode_segment(project_id)}/sandboxes/{_encode_segment(sandbox_id)}/input",
            json_body=body,
        )

    async def stop_sandbox(self, project_id: str, sandbox_id: str) -> Any:
        return await self.request(
            "POST",
            f"/api/v1/projects/{_encode_segment(project_id)}/sandboxes/{_encode_segment(sandbox_id)}/stop",
        )

    async def list_sessions(
        self,
        *,
        scope: str = "everyone",
        state: str = "all",
        project_id: str | None = None,
        limit: int = 100,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/v1/sessions",
            params={
                "scope": scope,
                "state": state,
                "project_id": project_id,
                "limit": limit,
            },
        )

    async def list_agent_sessions(
        self,
        project_id: str,
        *,
        saved_only: bool = False,
        limit: int = 30,
    ) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/projects/{_encode_segment(project_id)}/agent-sessions",
            params={"saved_only": saved_only, "limit": limit},
        )

    async def get_agent_session(self, project_id: str, session_id: str) -> Any:
        return await self.request(
            "GET",
            f"/api/v1/projects/{_encode_segment(project_id)}/agent-sessions/{_encode_segment(session_id)}",
        )

    async def create_agent_session(
        self,
        project_id: str,
        *,
        agent_id: str | None = None,
        runtime: str | None = None,
        inference_backend_id: str | None = None,
        model: str | None = None,
        repository_id: int | str | None = None,
        seed_id: str | None = None,
        ref: str | None = None,
        title: str | None = None,
        is_saved: bool = False,
        initial_message: str | None = None,
        propose_seeds: bool = False,
    ) -> Any:
        body = _clean_params(
            {
                "agent_id": agent_id,
                "runtime": runtime,
                "inference_backend_id": inference_backend_id,
                "models_backend_id": inference_backend_id,
                "model": model,
                "repository_id": repository_id,
                "seed_id": seed_id,
                "ref": ref,
                "title": title,
                "is_saved": is_saved,
                "initial_message": initial_message,
                "propose_seeds": propose_seeds,
            }
        )
        return await self.request(
            "POST",
            f"/api/v1/projects/{_encode_segment(project_id)}/agent-sessions",
            json_body=body,
        )

    async def send_agent_session_message(
        self,
        project_id: str,
        session_id: str,
        *,
        content: str,
        propose_seeds: bool = False,
    ) -> Any:
        return await self.request(
            "POST",
            f"/api/v1/projects/{_encode_segment(project_id)}/agent-sessions/{_encode_segment(session_id)}/messages",
            json_body={"content": content, "propose_seeds": propose_seeds},
        )

    async def stop_agent_session(self, project_id: str, session_id: str) -> Any:
        return await self.request(
            "POST",
            f"/api/v1/projects/{_encode_segment(project_id)}/agent-sessions/{_encode_segment(session_id)}/stop",
        )

    async def delete_agent_session(self, project_id: str, session_id: str) -> Any:
        return await self.request(
            "DELETE",
            f"/api/v1/projects/{_encode_segment(project_id)}/agent-sessions/{_encode_segment(session_id)}",
        )

