from dataclasses import dataclass
from enum import StrEnum


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class ActivityType(StrEnum):
    SEED_CREATED = "seed.created"
    SEED_AUTONOMY_CHANGED = "seed.autonomy_changed"
    SEED_MERGED = "seed.merged"
    SEED_CLOSED = "seed.closed"
    SEED_REOPENED = "seed.reopened"
    SEED_STOPPED = "seed.stopped"
    SEED_RESUMED = "seed.resumed"
    STAGE_CHANGED = "stage.changed"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_CHANGES_REQUESTED = "approval.changes_requested"
    RUN_STARTED = "run.started"
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMED_OUT = "run.timed_out"
    POLICY_UPDATED = "policy.updated"
    BUDGET_RAISED = "budget.raised"
    BUDGET_BLOCKED = "budget.blocked"
    PROJECT_CREATED = "project.created"
    PROJECT_ARCHIVED = "project.archived"
    PROJECT_RESTORED = "project.restored"
    USER_INVITED = "user.invited"
    USER_ACTIVATED = "user.activated"
    USER_SUSPENDED = "user.suspended"
    USER_REACTIVATED = "user.reactivated"
    REPOSITORY_ADDED = "repository.added"
    REPOSITORY_REMOVED = "repository.removed"
    BACKEND_GRANTED = "backend.granted"
    BACKEND_REVOKED = "backend.revoked"
    BACKEND_SECRET_UPDATED = "backend.secret_updated"
    MEMBER_ADDED = "member.added"
    MEMBER_ROLE_CHANGED = "member.role_changed"
    MEMBER_REMOVED = "member.removed"
    SETTINGS_UPDATED = "settings.updated"
    PAT_CREATED = "pat.created"
    PAT_REVOKED = "pat.revoked"
    SANDBOX_STARTED = "sandbox.started"


class AgentKind(StrEnum):
    STAGE = "stage"  # does SDLC work; assigned to stages in Policies
    ASSISTANT = "assistant"  # the Ctrl+K system assistant; read-only, no stages


class AgentRuntime(StrEnum):
    OPENCODE = "opencode"
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    ANTIGRAVITY = "antigravity"
    GEMINI = "gemini"
    GROK_BUILD = "grok-build"


class OrgRole(StrEnum):
    ADMIN = "admin"  # org settings: backends, agents, policies, users
    MEMBER = "member"  # everything else comes from project roles


class ProjectRole(StrEnum):
    MAINTAINER = "maintainer"  # manages the project; approves any gate its policy allows
    DEVELOPER = "developer"  # creates seeds; approves gates whose policy allows developers
    VIEWER = "viewer"  # read-only


class ProjectVisibility(StrEnum):
    PRIVATE = "private"  # only members can see it
    PUBLIC = "public"  # every user in the org can view it (roles still apply)


class UserStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class BackendType(StrEnum):
    GIT = "git"
    INFERENCE = "inference"
    COMPUTE = "compute"
    TOOLS = "tools"

    @classmethod
    def _missing_(cls, value: object) -> "BackendType | None":
        if isinstance(value, str) and value.strip().lower() == "models":
            return cls.INFERENCE
        return None


class BackendHealth(StrEnum):
    UNKNOWN = "unknown"  # never checked yet
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # reachable but slow or erroring
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class ProviderSpec:
    required_secrets: frozenset[str] = frozenset()
    optional_secrets: frozenset[str] = frozenset()

    @property
    def allowed_secrets(self) -> frozenset[str]:
        return self.required_secrets | self.optional_secrets


BACKEND_PROVIDER_SPECS: dict[BackendType, dict[str, ProviderSpec]] = {
    BackendType.GIT: {
        "github": ProviderSpec(
            required_secrets=frozenset({"token"}),
            optional_secrets=frozenset({"webhook_secret"}),
        ),
        "github-app": ProviderSpec(
            required_secrets=frozenset({"private_key"}),
            optional_secrets=frozenset({"webhook_secret", "token"}),
        ),
        "github-enterprise": ProviderSpec(
            required_secrets=frozenset({"token"}),
            optional_secrets=frozenset({"webhook_secret"}),
        ),
        "gitlab": ProviderSpec(
            required_secrets=frozenset({"token"}),
            optional_secrets=frozenset({"webhook_secret"}),
        ),
        "bitbucket": ProviderSpec(
            required_secrets=frozenset({"token"}),
            optional_secrets=frozenset({"webhook_secret"}),
        ),
        "git": ProviderSpec(
            optional_secrets=frozenset({"token", "ssh_key"}),
        ),
        "local": ProviderSpec(
            optional_secrets=frozenset({"token"}),
        ),
    },
    BackendType.INFERENCE: {
        "litellm": ProviderSpec(required_secrets=frozenset({"api_key"})),
        "anthropic": ProviderSpec(
            required_secrets=frozenset({"api_key"}),
            optional_secrets=frozenset({"oauth_token", "cli_auth_bundle"}),
        ),
        "openai": ProviderSpec(
            required_secrets=frozenset({"api_key"}),
            optional_secrets=frozenset({"oauth_token", "cli_auth_bundle"}),
        ),
        "antigravity": ProviderSpec(
            optional_secrets=frozenset({"api_key", "oauth_token", "cli_auth_bundle"}),
        ),
        "gemini": ProviderSpec(
            required_secrets=frozenset({"api_key"}),
            optional_secrets=frozenset({"oauth_token", "cli_auth_bundle"}),
        ),
        "xai": ProviderSpec(
            required_secrets=frozenset({"api_key"}),
            optional_secrets=frozenset({"oauth_token", "cli_auth_bundle"}),
        ),
        "ollama": ProviderSpec(optional_secrets=frozenset({"api_key"})),
        "lmstudio": ProviderSpec(optional_secrets=frozenset({"api_key"})),
        "vllm": ProviderSpec(optional_secrets=frozenset({"api_key"})),
        "custom": ProviderSpec(optional_secrets=frozenset({"api_key"})),
    },
    BackendType.COMPUTE: {
        "docker": ProviderSpec(optional_secrets=frozenset({"tls_cert", "tls_key", "tls_ca"})),
        "kubernetes": ProviderSpec(optional_secrets=frozenset({"token", "ca_cert"})),
    },
    BackendType.TOOLS: {
        "mcp-sse": ProviderSpec(optional_secrets=frozenset({"api_key", "bearer_token"})),
        "mcp-stdio": ProviderSpec(optional_secrets=frozenset({"env_secret"})),
    },
}


class SeedStage(StrEnum):
    IDEATE = "ideate"
    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
    MERGE = "merge"


class SeedStatus(StrEnum):
    STAGED = "staged"  # created in staging/pending state; requires manual start
    QUEUED = "queued"  # waiting for a runner slot
    RUNNING = "running"  # an agent is working on the current stage
    AWAITING_APPROVAL = "awaiting_approval"  # stage output ready, gate needs a person ("Needs you")
    BLOCKED = "blocked"  # budget, CI or backend; see blocked_reason
    FAILED = "failed"  # the run failed after retries
    STOPPED = "stopped"  # a person stopped the agent mid-stage; Resume re-queues the same stage
    MERGED = "merged"  # done: PR merged
    CLOSED = "closed"  # done: abandoned without merging


SEED_OPEN_STATUSES = frozenset(
    {
        SeedStatus.STAGED,
        SeedStatus.QUEUED,
        SeedStatus.RUNNING,
        SeedStatus.AWAITING_APPROVAL,
        SeedStatus.BLOCKED,
        SeedStatus.FAILED,
        SeedStatus.STOPPED,
    }
)


class SeedAutonomy(StrEnum):
    MANUAL = "manual"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


RUN_ACTIVE_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING})


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"  # stop: the seed is closed
    CHANGES_REQUESTED = "changes_requested"  # back to the stage's agent with the comment


class QueueStrategy(StrEnum):
    FIFO = "fifo"
    FAIR_SHARE = "fair_share"


class SandboxStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"


SANDBOX_ACTIVE_STATUSES = frozenset({SandboxStatus.STARTING, SandboxStatus.RUNNING})


class AgentSessionStatus(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    RUNNING_TURN = "running_turn"
    STOPPED = "stopped"
    FAILED = "failed"


AGENT_SESSION_ACTIVE_STATUSES = frozenset(
    {
        AgentSessionStatus.STARTING,
        AgentSessionStatus.IDLE,
        AgentSessionStatus.RUNNING_TURN,
    }
)
