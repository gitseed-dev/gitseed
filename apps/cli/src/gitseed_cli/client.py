from __future__ import annotations

import threading
from typing import Any
from urllib.parse import quote

import httpx
from gitseed_common.schemas import (
    ActivityResponse,
    AdvanceSeedRequest,
    AgentBenchmarksResponse,
    AgentResponse,
    AgentSessionResponse,
    ApprovalResponse,
    ApproveCheckpointRequest,
    BackendResponse,
    CoordinatorChatMessage,
    CoordinatorChatRequest,
    CoordinatorChatResponse,
    CreateAgentRequest,
    CreateAgentSessionRequest,
    CreateApprovalRequest,
    CreateProjectRequest,
    CreateRunRequest,
    CreateSandboxSessionRequest,
    CreateSeedCommentRequest,
    CreateSeedRequest,
    CreateSkillRequest,
    CreateTokenRequest,
    CurrentUserResponse,
    GovernanceReceiptEnvelopeResponse,
    HealthResponse,
    LoginRequest,
    ProjectResponse,
    ReceiptPublicKeyResponse,
    RunLogsResponse,
    RunResponse,
    SandboxInputRequest,
    SandboxRunConfigResponse,
    SandboxSessionResponse,
    SeedChangesResponse,
    SeedCommentResponse,
    SeedResponse,
    SeedTaskResponse,
    SendAgentSessionMessageRequest,
    SessionListItem,
    SkillResponse,
    StagePolicyResponse,
    TokenCreatedResponse,
    TokenResponse,
    UpdateAgentRequest,
    UpdateAgentSessionRequest,
    UpdateOrgPolicyRequest,
    UpdateProjectPolicyRequest,
    UpdateProjectRequest,
    UpdateSeedRequest,
    UpdateSkillRequest,
    UserResponse,
    VersionResponse,
)


class CLIError(Exception):
    """Raised when the CLI encounters a validation or API error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _enc(segment: str) -> str:
    return quote(str(segment), safe="")


class GitSeedClient:
    """Synchronous HTTP client for the GitSeed Control Plane API returning common schemas."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        default_project: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip() if token and token.strip() else None
        self.default_project = (
            default_project.strip() if default_project and default_project.strip() else None
        )
        self.timeout = timeout
        self._transport = transport
        self._client_lock = threading.Lock()
        self._client: httpx.Client | None = None
        self._http_client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(timeout=self.timeout, transport=self._transport)
                self._http_client = self._client
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None
                self._http_client = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "gitseed-cli/0.1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        cleaned_params = {k: v for k, v in params.items() if v is not None} if params else None
        try:
            resp = self._get_client().request(
                method.upper(),
                url,
                headers=self._headers(),
                params=cleaned_params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise CLIError(f"Could not connect to GitSeed API at {self.base_url}: {exc}") from exc

        if resp.status_code == 204 or not resp.content:
            if resp.is_success:
                return None

        parsed: Any = None
        try:
            parsed = resp.json()
        except ValueError:
            parsed = resp.text

        if not resp.is_success:
            raise CLIError(
                self._extract_error(parsed, resp.status_code),
                status_code=resp.status_code,
            )
        return parsed

    @staticmethod
    def _extract_error(payload: Any, status_code: int) -> str:
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return f"{detail} (HTTP {status_code})"
            if isinstance(detail, list):
                items: list[str] = []
                for entry in detail:
                    if isinstance(entry, dict):
                        loc = ".".join(str(x) for x in entry.get("loc", []) if x != "body")
                        msg = str(entry.get("msg", "invalid value"))
                        items.append(f"{loc}: {msg}" if loc else msg)
                    else:
                        items.append(str(entry))
                if items:
                    return f"Validation error: {'; '.join(items)} (HTTP {status_code})"
            err_obj = payload.get("error")
            if isinstance(err_obj, dict) and isinstance(err_obj.get("message"), str):
                return f"{err_obj['message']} (HTTP {status_code})"
            msg_val = payload.get("message") or err_obj
            if isinstance(msg_val, str) and msg_val.strip():
                return f"{msg_val} (HTTP {status_code})"
        if isinstance(payload, str) and payload.strip():
            return f"{payload.strip()} (HTTP {status_code})"
        return f"API request failed with HTTP {status_code}"

    # ── Reference Resolution ──────────────────────────────────────────

    def resolve_project_ref(self, project_ref: str | None = None) -> str:
        ref = (project_ref or self.default_project or "").strip()
        if not ref:
            raise CLIError(
                "No project specified. Pass -p/--project <slug> or set a default project "
                "with 'gitseed config use-project <slug>'."
            )
        return ref

    def resolve_seed_ref(self, seed_ref: str, *, project_override: str | None = None) -> str:
        cleaned = seed_ref.strip()
        if cleaned.startswith("sed_"):
            return cleaned
        if "#" in cleaned:
            proj_part, _, num_part = cleaned.partition("#")
            if not num_part.isdigit():
                raise CLIError(f"Invalid seed reference '{seed_ref}'. Expected <project>#<number>.")
            proj = proj_part.strip() or self.resolve_project_ref(project_override)
            return f"{proj}#{num_part}"
        num_candidate = cleaned.lstrip("#")
        if num_candidate.isdigit():
            proj = self.resolve_project_ref(project_override)
            return f"{proj}#{num_candidate}"
        return cleaned

    def resolve_agent_id(self, agent_ref: str) -> str:
        cleaned = agent_ref.strip()
        if cleaned.startswith("agt_"):
            return cleaned
        agent = self.get_agent(cleaned.lstrip("@"))
        return agent.id

    # ── Auth & System ─────────────────────────────────────────────────

    def login_and_mint_token(
        self,
        *,
        email: str,
        password: str,
        token_name: str,
        expires_in_days: int | None = None,
    ) -> tuple[CurrentUserResponse, TokenCreatedResponse]:
        login_req = LoginRequest(email=email, password=password)
        token_req = CreateTokenRequest(name=token_name, expires_in_days=expires_in_days)
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self._transport,
                headers={"X-GitSeed-CSRF": "1"},
            ) as session_client:
                login_resp = session_client.post(
                    "/api/v1/auth/login",
                    json=login_req.model_dump(mode="json"),
                )
                if not login_resp.is_success:
                    try:
                        err_body = login_resp.json()
                    except ValueError:
                        err_body = login_resp.text
                    raise CLIError(
                        self._extract_error(err_body, login_resp.status_code),
                        status_code=login_resp.status_code,
                    )
                user = CurrentUserResponse.model_validate(login_resp.json())

                pat_resp = session_client.post(
                    "/api/v1/auth/tokens",
                    json=token_req.model_dump(mode="json", exclude_none=True),
                )
                if not pat_resp.is_success:
                    try:
                        err_body = pat_resp.json()
                    except ValueError:
                        err_body = pat_resp.text
                    raise CLIError(
                        self._extract_error(err_body, pat_resp.status_code),
                        status_code=pat_resp.status_code,
                    )
                created_token = TokenCreatedResponse.model_validate(pat_resp.json())

                # Best-effort logout of the temporary web session cookie
                session_client.post("/api/v1/auth/logout")
                return user, created_token
        except httpx.HTTPError as exc:
            raise CLIError(f"Could not connect to GitSeed API at {self.base_url}: {exc}") from exc

    def get_health(self) -> HealthResponse:
        return HealthResponse.model_validate(self.request("GET", "/healthz"))

    def get_version(self) -> VersionResponse:
        return VersionResponse.model_validate(self.request("GET", "/version"))

    def get_me(self) -> CurrentUserResponse:
        return CurrentUserResponse.model_validate(self.request("GET", "/api/v1/auth/me"))

    def list_tokens(self) -> list[TokenResponse]:
        data = self.request("GET", "/api/v1/auth/tokens")
        return [TokenResponse.model_validate(x) for x in data]

    def delete_token(self, token_id: str) -> None:
        self.request("DELETE", f"/api/v1/auth/tokens/{_enc(token_id)}")

    # ── Projects ──────────────────────────────────────────────────────

    def list_projects(
        self, *, include_archived: bool = False, tag: str | None = None
    ) -> list[ProjectResponse]:
        data = self.request(
            "GET",
            "/api/v1/projects",
            params={"include_archived": include_archived, "tag": tag},
        )
        return [ProjectResponse.model_validate(x) for x in data]

    def get_project(self, project_id_or_slug: str) -> ProjectResponse:
        data = self.request("GET", f"/api/v1/projects/{_enc(project_id_or_slug)}")
        return ProjectResponse.model_validate(data)

    def create_project(self, payload: CreateProjectRequest) -> ProjectResponse:
        data = self.request(
            "POST",
            "/api/v1/projects",
            json_body=payload.model_dump(mode="json", exclude_none=True),
        )
        return ProjectResponse.model_validate(data)

    def update_project(
        self, project_id_or_slug: str, payload: UpdateProjectRequest
    ) -> ProjectResponse:
        data = self.request(
            "PATCH",
            f"/api/v1/projects/{_enc(project_id_or_slug)}",
            json_body=payload.model_dump(mode="json", exclude_unset=True),
        )
        return ProjectResponse.model_validate(data)

    def archive_project(self, project_id_or_slug: str) -> ProjectResponse:
        data = self.request("POST", f"/api/v1/projects/{_enc(project_id_or_slug)}/archive")
        return ProjectResponse.model_validate(data)

    def restore_project(self, project_id_or_slug: str) -> ProjectResponse:
        data = self.request("POST", f"/api/v1/projects/{_enc(project_id_or_slug)}/restore")
        return ProjectResponse.model_validate(data)

    # ── Seeds ─────────────────────────────────────────────────────────

    def list_seeds(
        self,
        *,
        project_id: str | None = None,
        stage: str | None = None,
        status: str | None = None,
        open_only: bool = False,
        autonomy: str | None = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[SeedResponse]:
        data = self.request(
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
        return [SeedResponse.model_validate(x) for x in data]

    def get_seed(self, seed_ref: str, *, project_override: str | None = None) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("GET", f"/api/v1/seeds/{_enc(resolved)}")
        return SeedResponse.model_validate(data)

    def create_seed(self, payload: CreateSeedRequest) -> SeedResponse:
        data = self.request(
            "POST",
            "/api/v1/seeds",
            json_body=payload.model_dump(mode="json", exclude_none=True),
        )
        return SeedResponse.model_validate(data)

    def update_seed(
        self,
        seed_ref: str,
        payload: UpdateSeedRequest,
        *,
        project_override: str | None = None,
    ) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request(
            "PATCH",
            f"/api/v1/seeds/{_enc(resolved)}",
            json_body=payload.model_dump(mode="json", exclude_unset=True),
        )
        return SeedResponse.model_validate(data)

    def start_seed(self, seed_ref: str, *, project_override: str | None = None) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("POST", f"/api/v1/seeds/{_enc(resolved)}/start")
        return SeedResponse.model_validate(data)

    def advance_seed(
        self,
        seed_ref: str,
        *,
        stage: str | None = None,
        fast_track: bool = False,
        project_override: str | None = None,
    ) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        body = (
            AdvanceSeedRequest.model_validate(
                {"stage": stage, "fast_track": fast_track}
            ).model_dump(mode="json", exclude_none=True)
            if (stage is not None or fast_track)
            else {}
        )
        data = self.request(
            "POST",
            f"/api/v1/seeds/{_enc(resolved)}/advance",
            json_body=body,
        )
        return SeedResponse.model_validate(data)

    def move_seed_back(
        self,
        seed_ref: str,
        *,
        stage: str,
        reason: str,
        project_override: str | None = None,
    ) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request(
            "POST",
            f"/api/v1/seeds/{_enc(resolved)}/move-back",
            json_body={"stage": stage, "reason": reason},
        )
        return SeedResponse.model_validate(data)

    def stop_seed(self, seed_ref: str, *, project_override: str | None = None) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("POST", f"/api/v1/seeds/{_enc(resolved)}/stop")
        return SeedResponse.model_validate(data)

    def resume_seed(self, seed_ref: str, *, project_override: str | None = None) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("POST", f"/api/v1/seeds/{_enc(resolved)}/resume")
        return SeedResponse.model_validate(data)

    def close_seed(self, seed_ref: str, *, project_override: str | None = None) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("POST", f"/api/v1/seeds/{_enc(resolved)}/close")
        return SeedResponse.model_validate(data)

    def reopen_seed(self, seed_ref: str, *, project_override: str | None = None) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("POST", f"/api/v1/seeds/{_enc(resolved)}/reopen")
        return SeedResponse.model_validate(data)

    def approve_checkpoint(
        self,
        seed_ref: str,
        payload: ApproveCheckpointRequest,
        *,
        project_override: str | None = None,
    ) -> SeedResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request(
            "POST",
            f"/api/v1/seeds/{_enc(resolved)}/approve-checkpoint",
            json_body=payload.model_dump(mode="json"),
        )
        return SeedResponse.model_validate(data)

    def list_seed_approvals(
        self, seed_ref: str, *, project_override: str | None = None
    ) -> list[ApprovalResponse]:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("GET", f"/api/v1/seeds/{_enc(resolved)}/approvals")
        return [ApprovalResponse.model_validate(x) for x in data]

    def create_seed_approval(
        self,
        seed_ref: str,
        payload: CreateApprovalRequest,
        *,
        project_override: str | None = None,
    ) -> ApprovalResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request(
            "POST",
            f"/api/v1/seeds/{_enc(resolved)}/approvals",
            json_body=payload.model_dump(mode="json", exclude_none=True),
        )
        return ApprovalResponse.model_validate(data)

    def list_seed_tasks(
        self, seed_ref: str, *, project_override: str | None = None
    ) -> list[SeedTaskResponse]:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("GET", f"/api/v1/seeds/{_enc(resolved)}/tasks")
        return [SeedTaskResponse.model_validate(x) for x in data]

    def list_seed_comments(
        self, seed_ref: str, *, project_override: str | None = None
    ) -> list[SeedCommentResponse]:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("GET", f"/api/v1/seeds/{_enc(resolved)}/comments")
        return [SeedCommentResponse.model_validate(x) for x in data]

    def create_seed_comment(
        self,
        seed_ref: str,
        payload: CreateSeedCommentRequest,
        *,
        project_override: str | None = None,
    ) -> SeedCommentResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request(
            "POST",
            f"/api/v1/seeds/{_enc(resolved)}/comments",
            json_body=payload.model_dump(mode="json"),
        )
        return SeedCommentResponse.model_validate(data)

    def get_seed_changes(
        self,
        seed_ref: str,
        *,
        repo_id: int | None = None,
        project_override: str | None = None,
    ) -> SeedChangesResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request(
            "GET",
            f"/api/v1/seeds/{_enc(resolved)}/changes",
            params={"repo_id": repo_id},
        )
        return SeedChangesResponse.model_validate(data)

    def get_seed_receipt(
        self,
        seed_ref: str,
        *,
        project_override: str | None = None,
    ) -> GovernanceReceiptEnvelopeResponse:
        resolved = self.resolve_seed_ref(seed_ref, project_override=project_override)
        data = self.request("GET", f"/api/v1/seeds/{_enc(resolved)}/receipt")
        return GovernanceReceiptEnvelopeResponse.model_validate(data)

    def get_receipt_public_key(self) -> ReceiptPublicKeyResponse:
        data = self.request("GET", "/api/v1/receipts/public-key")
        return ReceiptPublicKeyResponse.model_validate(data)

    # ── Runs ──────────────────────────────────────────────────────────

    def list_runs(
        self,
        *,
        seed_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        stage: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[RunResponse]:
        data = self.request(
            "GET",
            "/api/v1/runs",
            params={
                "seed_id": seed_id,
                "project_id": project_id,
                "agent_id": agent_id,
                "stage": stage,
                "status": status,
                "active_only": active_only,
                "limit": limit,
            },
        )
        return [RunResponse.model_validate(x) for x in data]

    def get_run(self, run_id: str) -> RunResponse:
        data = self.request("GET", f"/api/v1/runs/{_enc(run_id)}")
        return RunResponse.model_validate(data)

    def create_run(self, payload: CreateRunRequest) -> RunResponse:
        data = self.request(
            "POST",
            "/api/v1/runs",
            json_body=payload.model_dump(mode="json", exclude_none=True),
        )
        return RunResponse.model_validate(data)

    def cancel_run(self, run_id: str) -> RunResponse:
        data = self.request("POST", f"/api/v1/runs/{_enc(run_id)}/cancel")
        return RunResponse.model_validate(data)

    def get_run_logs(self, run_id: str, *, tail: int = 4000) -> RunLogsResponse:
        data = self.request(
            "GET",
            f"/api/v1/runs/{_enc(run_id)}/logs",
            params={"tail": tail},
        )
        return RunLogsResponse.model_validate(data)

    # ── Agents & Coordinator ──────────────────────────────────────────

    def list_agents(
        self,
        *,
        kind: str | None = None,
        enabled: bool | None = None,
        include_archived: bool = False,
    ) -> list[AgentResponse]:
        data = self.request(
            "GET",
            "/api/v1/agents",
            params={
                "kind": kind,
                "enabled": enabled,
                "include_archived": include_archived,
            },
        )
        return [AgentResponse.model_validate(x) for x in data]

    def get_agent(self, agent_id_or_handle: str) -> AgentResponse:
        cleaned = agent_id_or_handle.strip().lstrip("@")
        data = self.request("GET", f"/api/v1/agents/{_enc(cleaned)}")
        return AgentResponse.model_validate(data)

    def create_agent(self, payload: CreateAgentRequest) -> AgentResponse:
        data = self.request(
            "POST",
            "/api/v1/agents",
            json_body=payload.model_dump(mode="json", exclude_none=True),
        )
        return AgentResponse.model_validate(data)

    def update_agent(self, agent_id_or_handle: str, payload: UpdateAgentRequest) -> AgentResponse:
        cleaned = agent_id_or_handle.strip().lstrip("@")
        data = self.request(
            "PATCH",
            f"/api/v1/agents/{_enc(cleaned)}",
            json_body=payload.model_dump(mode="json", exclude_unset=True),
        )
        return AgentResponse.model_validate(data)

    def archive_agent(self, agent_id_or_handle: str) -> AgentResponse:
        cleaned = agent_id_or_handle.strip().lstrip("@")
        data = self.request("POST", f"/api/v1/agents/{_enc(cleaned)}/archive")
        return AgentResponse.model_validate(data)

    def restore_agent(self, agent_id_or_handle: str) -> AgentResponse:
        cleaned = agent_id_or_handle.strip().lstrip("@")
        data = self.request("POST", f"/api/v1/agents/{_enc(cleaned)}/restore")
        return AgentResponse.model_validate(data)

    def get_agent_benchmarks(
        self,
        *,
        project_id: str | None = None,
        stage: str | None = None,
    ) -> AgentBenchmarksResponse:
        resolved_project = None
        if project_id:
            resolved_project = (
                project_id
                if project_id.startswith("prj_")
                else self.get_project(project_id).id
            )
        data = self.request(
            "GET",
            "/api/v1/agents/benchmarks",
            params={
                "project_id": resolved_project,
                "stage": stage,
            },
        )
        return AgentBenchmarksResponse.model_validate(data)

    def chat_coordinator(
        self,
        prompt: str,
        *,
        project_id: str | None = None,
        seed_id: str | None = None,
        mode: str = "general",
        propose_seeds: bool = False,
    ) -> CoordinatorChatResponse:
        req = CoordinatorChatRequest(
            messages=[CoordinatorChatMessage(role="user", content=prompt)],
            project_id=project_id,
            seed_id=seed_id,
            mode="brainstorm" if mode == "brainstorm" else "general",
            propose_seeds=propose_seeds,
        )
        data = self.request(
            "POST",
            "/api/v1/agents/coordinator/chat",
            json_body=req.model_dump(mode="json", exclude_none=True),
        )
        return CoordinatorChatResponse.model_validate(data)

    # ── Skills ────────────────────────────────────────────────────────

    def list_skills(self, *, include_archived: bool = False) -> list[SkillResponse]:
        data = self.request(
            "GET",
            "/api/v1/skills",
            params={"include_archived": include_archived},
        )
        return [SkillResponse.model_validate(x) for x in data]

    def get_skill(self, skill_id_or_slug: str) -> SkillResponse:
        data = self.request("GET", f"/api/v1/skills/{_enc(skill_id_or_slug.strip())}")
        return SkillResponse.model_validate(data)

    def create_skill(self, payload: CreateSkillRequest) -> SkillResponse:
        data = self.request(
            "POST",
            "/api/v1/skills",
            json_body=payload.model_dump(mode="json", exclude_none=True),
        )
        return SkillResponse.model_validate(data)

    def update_skill(self, skill_id_or_slug: str, payload: UpdateSkillRequest) -> SkillResponse:
        data = self.request(
            "PATCH",
            f"/api/v1/skills/{_enc(skill_id_or_slug.strip())}",
            json_body=payload.model_dump(mode="json", exclude_unset=True),
        )
        return SkillResponse.model_validate(data)

    def archive_skill(self, skill_id_or_slug: str) -> SkillResponse:
        data = self.request("POST", f"/api/v1/skills/{_enc(skill_id_or_slug.strip())}/archive")
        return SkillResponse.model_validate(data)

    def restore_skill(self, skill_id_or_slug: str) -> SkillResponse:
        data = self.request("POST", f"/api/v1/skills/{_enc(skill_id_or_slug.strip())}/restore")
        return SkillResponse.model_validate(data)

    # ── Policies ──────────────────────────────────────────────────────

    def list_org_policies(self) -> list[StagePolicyResponse]:
        data = self.request("GET", "/api/v1/policies")
        return [StagePolicyResponse.model_validate(x) for x in data]

    def update_org_policy(self, stage: str, payload: UpdateOrgPolicyRequest) -> StagePolicyResponse:
        data = self.request(
            "PATCH",
            f"/api/v1/policies/{_enc(stage)}",
            json_body=payload.model_dump(mode="json", exclude_unset=True),
        )
        return StagePolicyResponse.model_validate(data)

    def list_project_policies(self, project_id_or_slug: str) -> list[StagePolicyResponse]:
        data = self.request("GET", f"/api/v1/policies/projects/{_enc(project_id_or_slug)}")
        return [StagePolicyResponse.model_validate(x) for x in data]

    def update_project_policy(
        self,
        project_id_or_slug: str,
        stage: str,
        payload: UpdateProjectPolicyRequest,
    ) -> StagePolicyResponse:
        data = self.request(
            "PATCH",
            f"/api/v1/policies/projects/{_enc(project_id_or_slug)}/{_enc(stage)}",
            json_body=payload.model_dump(mode="json", exclude_unset=True),
        )
        return StagePolicyResponse.model_validate(data)

    def reset_project_policy(self, project_id_or_slug: str, stage: str) -> None:
        self.request(
            "DELETE",
            f"/api/v1/policies/projects/{_enc(project_id_or_slug)}/{_enc(stage)}",
        )

    # ── Backends ──────────────────────────────────────────────────────

    def list_backends(
        self, *, type_filter: str | None = None, enabled: bool | None = None
    ) -> list[BackendResponse]:
        data = self.request(
            "GET",
            "/api/v1/backends",
            params={"type": type_filter, "enabled": enabled},
        )
        return [BackendResponse.model_validate(x) for x in data]

    def get_backend(self, backend_id: str) -> BackendResponse:
        data = self.request("GET", f"/api/v1/backends/{_enc(backend_id)}")
        return BackendResponse.model_validate(data)

    def delete_backend(self, backend_id: str) -> None:
        self.request("DELETE", f"/api/v1/backends/{_enc(backend_id)}")

    # ── Sandboxes ─────────────────────────────────────────────────────

    def get_sandbox_config(
        self,
        project_id_or_slug: str,
        *,
        repo_id: int | None = None,
        ref: str | None = None,
    ) -> SandboxRunConfigResponse:
        data = self.request(
            "GET",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/sandboxes/config",
            params={"repo_id": repo_id, "ref": ref},
        )
        return SandboxRunConfigResponse.model_validate(data)

    def list_sandboxes(
        self, project_id_or_slug: str, *, limit: int = 25
    ) -> list[SandboxSessionResponse]:
        data = self.request(
            "GET",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/sandboxes",
            params={"limit": limit},
        )
        return [SandboxSessionResponse.model_validate(x) for x in data]

    def get_sandbox(self, project_id_or_slug: str, sandbox_id: str) -> SandboxSessionResponse:
        data = self.request(
            "GET",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/sandboxes/{_enc(sandbox_id)}",
        )
        return SandboxSessionResponse.model_validate(data)

    def create_sandbox(
        self, project_id_or_slug: str, payload: CreateSandboxSessionRequest
    ) -> SandboxSessionResponse:
        data = self.request(
            "POST",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/sandboxes",
            json_body=payload.model_dump(mode="json", exclude_none=True),
        )
        return SandboxSessionResponse.model_validate(data)

    def send_sandbox_input(
        self,
        project_id_or_slug: str,
        sandbox_id: str,
        payload: SandboxInputRequest,
    ) -> SandboxSessionResponse:
        data = self.request(
            "POST",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/sandboxes/{_enc(sandbox_id)}/input",
            json_body=payload.model_dump(mode="json"),
        )
        return SandboxSessionResponse.model_validate(data)

    def stop_sandbox(self, project_id_or_slug: str, sandbox_id: str) -> SandboxSessionResponse:
        data = self.request(
            "POST",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/sandboxes/{_enc(sandbox_id)}/stop",
        )
        return SandboxSessionResponse.model_validate(data)

    def delete_sandbox(self, project_id_or_slug: str, sandbox_id: str) -> None:
        self.request(
            "DELETE",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/sandboxes/{_enc(sandbox_id)}",
        )

    # ── Interactive Sessions & Agent Sessions ─────────────────────────

    def list_sessions(
        self,
        *,
        scope: str = "everyone",
        state: str = "all",
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[SessionListItem]:
        data = self.request(
            "GET",
            "/api/v1/sessions",
            params={
                "scope": scope,
                "state": state,
                "project_id": project_id,
                "limit": limit,
            },
        )
        return [SessionListItem.model_validate(x) for x in data]

    def get_session_item(self, kind: str, session_id: str) -> SessionListItem:
        data = self.request(
            "GET",
            f"/api/v1/sessions/{_enc(kind)}/{_enc(session_id)}",
        )
        return SessionListItem.model_validate(data)

    def list_agent_sessions(
        self,
        project_id_or_slug: str,
        *,
        saved_only: bool = False,
        limit: int = 30,
    ) -> list[AgentSessionResponse]:
        data = self.request(
            "GET",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/agent-sessions",
            params={"saved_only": saved_only, "limit": limit},
        )
        return [AgentSessionResponse.model_validate(x) for x in data]

    def get_agent_session(
        self, project_id_or_slug: str, session_id: str
    ) -> AgentSessionResponse:
        data = self.request(
            "GET",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/agent-sessions/{_enc(session_id)}",
        )
        return AgentSessionResponse.model_validate(data)

    def create_agent_session(
        self, project_id_or_slug: str, payload: CreateAgentSessionRequest
    ) -> AgentSessionResponse:
        data = self.request(
            "POST",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/agent-sessions",
            json_body=payload.model_dump(mode="json", exclude_none=True),
        )
        return AgentSessionResponse.model_validate(data)

    def send_agent_session_message(
        self,
        project_id_or_slug: str,
        session_id: str,
        payload: SendAgentSessionMessageRequest,
    ) -> AgentSessionResponse:
        data = self.request(
            "POST",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/agent-sessions/{_enc(session_id)}/messages",
            json_body=payload.model_dump(mode="json"),
        )
        return AgentSessionResponse.model_validate(data)

    def update_agent_session(
        self,
        project_id_or_slug: str,
        session_id: str,
        payload: UpdateAgentSessionRequest,
    ) -> AgentSessionResponse:
        data = self.request(
            "PATCH",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/agent-sessions/{_enc(session_id)}",
            json_body=payload.model_dump(mode="json", exclude_unset=True),
        )
        return AgentSessionResponse.model_validate(data)

    def stop_agent_session(
        self, project_id_or_slug: str, session_id: str
    ) -> AgentSessionResponse:
        data = self.request(
            "POST",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/agent-sessions/{_enc(session_id)}/stop",
        )
        return AgentSessionResponse.model_validate(data)

    def delete_agent_session(self, project_id_or_slug: str, session_id: str) -> None:
        self.request(
            "DELETE",
            f"/api/v1/projects/{_enc(project_id_or_slug)}/agent-sessions/{_enc(session_id)}",
        )

    # ── Users ─────────────────────────────────────────────────────────

    def list_users(
        self, *, status: str | None = None, org_role: str | None = None
    ) -> list[UserResponse]:
        data = self.request(
            "GET",
            "/api/v1/users",
            params={"status": status, "org_role": org_role},
        )
        return [UserResponse.model_validate(x) for x in data]

    def get_user(self, user_id: str) -> UserResponse:
        data = self.request("GET", f"/api/v1/users/{_enc(user_id)}")
        return UserResponse.model_validate(data)

    def delete_invited_user(self, user_id: str) -> None:
        self.request("DELETE", f"/api/v1/users/{_enc(user_id)}")

    # ── Activity Events ───────────────────────────────────────────────

    def list_activity(
        self,
        *,
        project_id: str | None = None,
        seed_id: str | None = None,
        limit: int = 50,
    ) -> list[ActivityResponse]:
        data = self.request(
            "GET",
            "/api/v1/activity",
            params={"project_id": project_id, "seed_id": seed_id, "limit": limit},
        )
        return [ActivityResponse.model_validate(x) for x in data]
