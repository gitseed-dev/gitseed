from typing import Any, Literal

from pydantic import BaseModel, Field


class ReceiptPublicKeyResponse(BaseModel):
    key_id: str
    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_pem: str
    previous_public_keys: list[dict[str, str]] = Field(default_factory=list)


class GovernanceReceiptSeedInfo(BaseModel):
    id: str
    number: int
    title: str
    project_id: str
    project_slug: str
    status: str
    stage: str
    branch_name: str | None = None
    pr_url: str | None = None


class GovernanceReceiptPolicyInfo(BaseModel):
    autonomy: str
    min_approver_role: str
    prevent_self_approval: bool
    blocked_paths: list[str] = Field(default_factory=list)


class GovernanceReceiptRunItem(BaseModel):
    id: str
    stage: str
    runtime: str
    model: str
    status: str
    cost_usd: str
    cost_enforcement: Literal["enforced", "estimated", "unpriced"] = "enforced"
    input_tokens: int = 0
    output_tokens: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    trusted: dict[str, Any] | None = None


class GovernanceReceiptApprovalItem(BaseModel):
    id: str
    stage: str
    status: str
    approver_user_id: str | None = None
    approver_name: str | None = None
    comment: str | None = None
    decided_at: str | None = None


class GovernanceReceiptTaskItem(BaseModel):
    id: str
    title: str
    status: str
    order_index: int = 0


class GovernanceReceiptArtifactsInfo(BaseModel):
    specification_sha256: str | None = None
    acceptance_criteria_sha256: str | None = None
    plan_sha256: str | None = None
    task_count: int = 0
    tasks: list[GovernanceReceiptTaskItem] = Field(default_factory=list)


class GovernanceReceiptPayload(BaseModel):
    schema_version: Literal["gitseed.receipt.v1"] = "gitseed.receipt.v1"
    seed: GovernanceReceiptSeedInfo
    policy: GovernanceReceiptPolicyInfo
    artifacts: GovernanceReceiptArtifactsInfo | None = None
    runs: list[GovernanceReceiptRunItem] = Field(default_factory=list)
    approvals: list[GovernanceReceiptApprovalItem] = Field(default_factory=list)
    generated_at: str


class GovernanceReceiptEnvelopeResponse(BaseModel):
    receipt: dict[str, Any]
    canonical_json: str
    signature: str
    algorithm: Literal["Ed25519"] = "Ed25519"
    key_id: str
    public_key_pem: str
    markdown: str | None = None
