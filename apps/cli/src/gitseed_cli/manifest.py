from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml  # type: ignore[import-untyped]
from gitseed_common.enums import SeedStage, TaskStatus
from gitseed_common.schemas import (
    AgentResponse,
    CreateAgentRequest,
    CreateProjectRequest,
    CreateSeedRequest,
    CreateSkillRequest,
    ProjectResponse,
    SeedResponse,
    SeedTaskResponse,
    SkillResponse,
    StagePolicyResponse,
    UpdateAgentRequest,
    UpdateOrgPolicyRequest,
    UpdateProjectPolicyRequest,
    UpdateProjectRequest,
    UpdateSeedRequest,
    UpdateSkillRequest,
)
from pydantic import ValidationError

from gitseed_cli.client import CLIError

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\r?\n(?P<yaml>.*?)\r?\n---\s*(?:\r?\n(?P<body>.*))?\Z",
    re.DOTALL,
)
_H2_RE = re.compile(r"(?m)^##[ \t]+(.+?)[ \t]*$")
_BULLET_RE = re.compile(r"^(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?(.+)$")

SEED_MUTABLE_HEADINGS = frozenset({"summary", "specification", "acceptance criteria"})
SEED_READONLY_HEADINGS = frozenset({"plan", "plan (read-only)", "tasks", "tasks (read-only)"})
SEED_FORBIDDEN_FRONTMATTER = frozenset({"stage", "status", "plan", "tasks"})
SEED_ALLOWED_FRONTMATTER = (
    (
        frozenset(CreateSeedRequest.model_fields.keys())
        | frozenset(UpdateSeedRequest.model_fields.keys())
        | {"kind", "id", "seed", "number", "project"}
    )
    - SEED_FORBIDDEN_FRONTMATTER
    - {"project_id", "description", "specification", "acceptance_criteria"}
)

AGENT_ALLOWED_FRONTMATTER = (
    frozenset(CreateAgentRequest.model_fields.keys())
    | frozenset(UpdateAgentRequest.model_fields.keys())
    | {"kind", "agent_kind", "id"}
) - {"instructions"}

SKILL_ALLOWED_FRONTMATTER = (
    frozenset(CreateSkillRequest.model_fields.keys())
    | frozenset(UpdateSkillRequest.model_fields.keys())
    | {"kind", "id"}
) - {"content"}

PROJECT_ALLOWED_FRONTMATTER = (
    frozenset(CreateProjectRequest.model_fields.keys())
    | frozenset(UpdateProjectRequest.model_fields.keys())
    | {"kind", "id"}
) - {"description"}

POLICY_ALLOWED_FRONTMATTER = (
    frozenset(UpdateOrgPolicyRequest.model_fields.keys())
    | frozenset(UpdateProjectPolicyRequest.model_fields.keys())
    | {"kind", "id", "stage", "project", "agent"}
)


@dataclass
class ParsedManifest:
    kind: str
    frontmatter: dict[str, Any]
    body: str
    sections: dict[str, str] = field(default_factory=dict)
    ignored_readonly_sections: list[str] = field(default_factory=list)


def _format_pydantic_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = str(err.get("msg", "invalid value"))
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def parse_markdown_manifest(text: str, *, source: str = "<manifest>") -> ParsedManifest:
    """Parse a Markdown document with YAML frontmatter and validate its structure."""
    match = _FRONTMATTER_RE.match(text.lstrip("\ufeff"))
    if not match:
        raise CLIError(
            f"{source}: Missing YAML frontmatter block ('--- ... ---') at the top of the file."
        )

    yaml_raw = match.group("yaml")
    body = (match.group("body") or "").strip()

    try:
        loaded = yaml.safe_load(yaml_raw)
    except yaml.YAMLError as exc:
        raise CLIError(f"{source}: Invalid YAML frontmatter: {exc}") from exc

    if not isinstance(loaded, dict):
        raise CLIError(f"{source}: YAML frontmatter must be a mapping.")

    raw_kind = str(loaded.get("kind") or "").strip()
    if not raw_kind:
        raise CLIError(
            f"{source}: Frontmatter must include 'kind: Seed | Agent | Skill | Policy | Project'."
        )

    normalized_kind = raw_kind.capitalize()
    if normalized_kind not in {"Seed", "Agent", "Skill", "Policy", "Project"}:
        raise CLIError(
            f"{source}: Unsupported manifest kind '{raw_kind}'. "
            "Expected one of: Seed, Agent, Skill, Policy, Project."
        )

    if normalized_kind == "Seed":
        return _parse_seed_manifest(loaded, body, source=source)
    if normalized_kind == "Agent":
        _check_unknown_keys(loaded, AGENT_ALLOWED_FRONTMATTER, "Agent", source)
    elif normalized_kind == "Skill":
        _check_unknown_keys(loaded, SKILL_ALLOWED_FRONTMATTER, "Skill", source)
    elif normalized_kind == "Project":
        _check_unknown_keys(loaded, PROJECT_ALLOWED_FRONTMATTER, "Project", source)
    elif normalized_kind == "Policy":
        _check_unknown_keys(loaded, POLICY_ALLOWED_FRONTMATTER, "Policy", source)

    return ParsedManifest(kind=normalized_kind, frontmatter=loaded, body=body)


def _check_unknown_keys(
    frontmatter: dict[str, Any],
    allowed: frozenset[str],
    kind_name: str,
    source: str,
) -> None:
    unknown = sorted(set(frontmatter.keys()) - allowed)
    if unknown:
        raise CLIError(
            f"{source}: Unknown {kind_name} frontmatter field(s): {', '.join(unknown)}. "
            f"Allowed fields: {', '.join(sorted(allowed - {'kind'}))}."
        )


def _parse_seed_manifest(frontmatter: dict[str, Any], body: str, *, source: str) -> ParsedManifest:
    forbidden = sorted(set(frontmatter.keys()) & SEED_FORBIDDEN_FRONTMATTER)
    if forbidden:
        raise CLIError(
            f"{source}: Seed manifest cannot set {', '.join(forbidden)}. "
            "Stages, plans, and tasks are managed by workflow gates and agent runs."
        )

    _check_unknown_keys(frontmatter, SEED_ALLOWED_FRONTMATTER, "Seed", source)

    sections: dict[str, str] = {}
    ignored_readonly: list[str] = []

    if body:
        # Remove HTML comments (such as export read-only notes) before heading checks
        cleaned_body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
        matches = list(_H2_RE.finditer(cleaned_body))
        if not matches:
            raise CLIError(
                f"{source}: Seed manifest body must use '## Summary', "
                "'## Specification', and/or '## Acceptance Criteria' headings."
            )
        leading = cleaned_body[: matches[0].start()].strip()
        if leading:
            raise CLIError(
                f"{source}: Unexpected content before first '##' heading. "
                "Place seed text under '## Summary', '## Specification', "
                "or '## Acceptance Criteria'."
            )

        for idx, m in enumerate(matches):
            raw_heading = m.group(1).strip()
            norm_heading = raw_heading.lower()
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(cleaned_body)
            section_content = cleaned_body[start:end].strip()

            if norm_heading in SEED_MUTABLE_HEADINGS:
                sections[norm_heading] = section_content
            elif norm_heading in SEED_READONLY_HEADINGS:
                label = "Plan" if norm_heading.startswith("plan") else "Tasks"
                if label not in ignored_readonly:
                    ignored_readonly.append(label)
            else:
                raise CLIError(
                    f"{source}: Unknown heading '## {raw_heading}' in Seed manifest. "
                    "Allowed headings are: '## Summary', '## Specification', "
                    "'## Acceptance Criteria'."
                )

    return ParsedManifest(
        kind="Seed",
        frontmatter=frontmatter,
        body=body,
        sections=sections,
        ignored_readonly_sections=ignored_readonly,
    )


def parse_acceptance_criteria(section_text: str) -> list[str]:
    items: list[str] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _BULLET_RE.match(line)
        if m:
            cleaned = m.group(1).strip()
            if cleaned:
                items.append(cleaned)
        else:
            items.append(line)
    return items


def build_create_seed_request(
    manifest: ParsedManifest, *, project_id: str, source: str = "<manifest>"
) -> CreateSeedRequest:
    fm = manifest.frontmatter
    payload: dict[str, Any] = {
        "project_id": project_id,
        "title": fm.get("title") or "",
        "description": manifest.sections.get("summary", ""),
        "specification": manifest.sections.get("specification", ""),
        "acceptance_criteria": parse_acceptance_criteria(
            manifest.sections.get("acceptance criteria", "")
        ),
    }
    if "autonomy" in fm and fm["autonomy"] is not None:
        payload["autonomy"] = fm["autonomy"]
    if "budget_cap" in fm:
        payload["budget_cap"] = fm["budget_cap"]
    if "tags" in fm and fm["tags"] is not None:
        payload["tags"] = fm["tags"]
    if "staged" in fm and fm["staged"] is not None:
        payload["staged"] = fm["staged"]
    if "fast_track" in fm and fm["fast_track"] is not None:
        payload["fast_track"] = bool(fm["fast_track"])
        if payload["fast_track"]:
            payload["stage"] = SeedStage.EXECUTE

    try:
        return CreateSeedRequest.model_validate(payload)
    except ValidationError as exc:
        raise CLIError(f"{source}: {_format_pydantic_error(exc)}") from exc


def build_update_seed_request(
    manifest: ParsedManifest,
    *,
    existing_seed: SeedResponse | None = None,
    source: str = "<manifest>",
) -> UpdateSeedRequest:
    fm = manifest.frontmatter
    payload: dict[str, Any] = {}
    if "title" in fm and fm["title"] is not None:
        payload["title"] = fm["title"]
    if "autonomy" in fm and fm["autonomy"] is not None:
        payload["autonomy"] = fm["autonomy"]
    if "budget_cap" in fm:
        payload["budget_cap"] = fm["budget_cap"]
    if "tags" in fm and fm["tags"] is not None:
        payload["tags"] = fm["tags"]

    if "summary" in manifest.sections:
        payload["description"] = manifest.sections["summary"]
    if "specification" in manifest.sections:
        new_spec = manifest.sections["specification"]
        if (
            existing_seed is not None
            and existing_seed.stage != SeedStage.IDEATE
            and new_spec.strip() == (existing_seed.specification or "").strip()
        ):
            pass
        else:
            payload["specification"] = new_spec
    if "acceptance criteria" in manifest.sections:
        new_criteria = parse_acceptance_criteria(manifest.sections["acceptance criteria"])
        if (
            existing_seed is not None
            and existing_seed.stage != SeedStage.IDEATE
            and new_criteria == list(existing_seed.acceptance_criteria or [])
        ):
            pass
        else:
            payload["acceptance_criteria"] = new_criteria

    try:
        return UpdateSeedRequest.model_validate(payload)
    except ValidationError as exc:
        raise CLIError(f"{source}: {_format_pydantic_error(exc)}") from exc


def build_agent_requests(
    manifest: ParsedManifest, *, source: str = "<manifest>"
) -> tuple[CreateAgentRequest, UpdateAgentRequest]:
    fm = dict(manifest.frontmatter)
    fm.pop("kind", None)
    fm.pop("id", None)
    agent_kind = fm.pop("agent_kind", None)
    if agent_kind is not None:
        fm["kind"] = agent_kind
    if manifest.body:
        fm["instructions"] = manifest.body
    try:
        create_req = CreateAgentRequest.model_validate(fm)
        update_req = UpdateAgentRequest.model_validate(fm)
        return create_req, update_req
    except ValidationError as exc:
        raise CLIError(f"{source}: {_format_pydantic_error(exc)}") from exc


def build_skill_requests(
    manifest: ParsedManifest, *, source: str = "<manifest>"
) -> tuple[CreateSkillRequest, UpdateSkillRequest]:
    fm = dict(manifest.frontmatter)
    fm.pop("kind", None)
    fm.pop("id", None)
    if manifest.body:
        fm["content"] = manifest.body
    try:
        create_req = CreateSkillRequest.model_validate(fm)
        update_req = UpdateSkillRequest.model_validate(fm)
        return create_req, update_req
    except ValidationError as exc:
        raise CLIError(f"{source}: {_format_pydantic_error(exc)}") from exc


def build_project_requests(
    manifest: ParsedManifest, *, source: str = "<manifest>"
) -> tuple[CreateProjectRequest, UpdateProjectRequest]:
    fm = dict(manifest.frontmatter)
    fm.pop("kind", None)
    fm.pop("id", None)
    if manifest.body:
        fm["description"] = manifest.body
    try:
        create_req = CreateProjectRequest.model_validate(fm)
        update_req = UpdateProjectRequest.model_validate(fm)
        return create_req, update_req
    except ValidationError as exc:
        raise CLIError(f"{source}: {_format_pydantic_error(exc)}") from exc


def build_policy_request(
    manifest: ParsedManifest,
    *,
    resolved_agent_id: str | None = None,
    source: str = "<manifest>",
) -> tuple[SeedStage, str | None, UpdateOrgPolicyRequest, UpdateProjectPolicyRequest]:
    fm = dict(manifest.frontmatter)
    fm.pop("kind", None)
    fm.pop("id", None)
    raw_stage = fm.pop("stage", None)
    if not raw_stage:
        raise CLIError(f"{source}: Policy manifest requires 'stage' in frontmatter.")
    try:
        stage = SeedStage(str(raw_stage).strip().lower())
    except ValueError as exc:
        valid = ", ".join(s.value for s in SeedStage)
        raise CLIError(f"{source}: Invalid stage '{raw_stage}'. Expected one of: {valid}.") from exc

    project = fm.pop("project", None)
    fm.pop("agent", None)
    if resolved_agent_id is not None:
        fm["agent_id"] = resolved_agent_id
    if manifest.body and "instructions" not in fm:
        fm["instructions"] = manifest.body

    try:
        org_req = UpdateOrgPolicyRequest.model_validate(fm)
        proj_req = UpdateProjectPolicyRequest.model_validate(fm)
        return stage, str(project).strip() if project else None, org_req, proj_req
    except ValidationError as exc:
        raise CLIError(f"{source}: {_format_pydantic_error(exc)}") from exc


# ── Markdown Export Serializers (-o md) ───────────────────────────────


def _dump_frontmatter(data: dict[str, Any]) -> str:
    yaml_str = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    return f"---\n{yaml_str}\n---\n"


def seed_to_markdown(
    seed: SeedResponse,
    *,
    project_slug: str | None = None,
    tasks: list[SeedTaskResponse] | None = None,
) -> str:
    proj_ref = project_slug or seed.project_id
    fm: dict[str, Any] = {
        "kind": "Seed",
        "id": seed.id,
        "seed": f"{proj_ref}#{seed.number}",
        "project": proj_ref,
        "title": seed.title,
        "autonomy": str(seed.autonomy),
        "budget_cap": float(seed.budget_cap) if seed.budget_cap is not None else None,
        "tags": list(seed.tags or []),
    }
    parts = [
        _dump_frontmatter(fm),
        f"\n## Summary\n\n{(seed.description or '').strip()}\n",
        f"\n## Specification\n\n{(seed.specification or '').strip()}\n",
        "\n## Acceptance Criteria\n",
    ]
    if seed.acceptance_criteria:
        criteria_lines = "\n".join(f"- {c}" for c in seed.acceptance_criteria)
        parts.append(f"\n{criteria_lines}\n")
    else:
        parts.append("\n")

    if seed.plan or tasks:
        parts.append(
            "\n<!-- Read-only workflow output below: "
            "ignored by 'gitseed apply' and 'gitseed edit' -->\n"
        )
    if seed.plan:
        parts.append(f"\n## Plan (read-only)\n\n{seed.plan.strip()}\n")
    if tasks:
        task_lines = [
            f"- [{'x' if t.status == TaskStatus.DONE else ' '}] {t.title} ({t.status})"
            for t in tasks
        ]
        parts.append("\n## Tasks (read-only)\n\n" + "\n".join(task_lines) + "\n")

    return "".join(parts)


def agent_to_markdown(agent: AgentResponse) -> str:
    fm: dict[str, Any] = {
        "kind": "Agent",
        "id": agent.id,
        "handle": agent.handle,
        "name": agent.name,
        "agent_kind": str(agent.kind),
        "template": agent.template,
        "runtime": str(agent.runtime),
        "model": agent.model,
        "skills": list(agent.skills or []),
        "tools": list(agent.tools or []),
        "enabled": agent.enabled,
    }
    body = (agent.instructions or "").strip()
    return _dump_frontmatter(fm) + (f"\n{body}\n" if body else "\n")


def skill_to_markdown(skill: SkillResponse) -> str:
    fm: dict[str, Any] = {
        "kind": "Skill",
        "id": skill.id,
        "slug": skill.slug,
        "name": skill.name,
        "description": skill.description,
    }
    body = (skill.content or "").strip()
    return _dump_frontmatter(fm) + (f"\n{body}\n" if body else "\n")


def project_to_markdown(project: ProjectResponse) -> str:
    fm: dict[str, Any] = {
        "kind": "Project",
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "visibility": str(project.visibility),
        "tags": list(project.tags or []),
        "monthly_budget": (
            float(project.monthly_budget) if project.monthly_budget is not None else None
        ),
        "seed_budget_cap": (
            float(project.seed_budget_cap) if project.seed_budget_cap is not None else None
        ),
        "spend_checkpoint": (
            float(project.spend_checkpoint) if project.spend_checkpoint is not None else None
        ),
        "max_concurrent_runs": project.max_concurrent_runs,
        "backends_restricted": project.backends_restricted,
    }
    body = (project.description or "").strip()
    return _dump_frontmatter(fm) + (f"\n{body}\n" if body else "\n")


def policy_to_markdown(
    policy: StagePolicyResponse,
    *,
    project_slug: str | None = None,
    agent_handle: str | None = None,
) -> str:
    fm: dict[str, Any] = {
        "kind": "Policy",
        "stage": str(policy.stage),
    }
    if project_slug or policy.project_id:
        fm["project"] = project_slug or policy.project_id
    if agent_handle:
        fm["agent"] = f"@{agent_handle.lstrip('@')}"
    elif policy.agent_id:
        fm["agent_id"] = policy.agent_id
    fm.update(
        {
            "enabled": policy.enabled,
            "requires_approval": policy.requires_approval,
            "min_approvers": policy.min_approvers,
            "min_approver_role": str(policy.min_approver_role),
            "allow_auto_approve": policy.allow_auto_approve,
            "allow_self_approval": policy.allow_self_approval,
            "max_attempts": policy.max_attempts,
            "timeout_minutes": policy.timeout_minutes,
            "min_acceptance_criteria": policy.min_acceptance_criteria,
            "max_acceptance_criteria": policy.max_acceptance_criteria,
            "max_tasks": policy.max_tasks,
        }
    )
    body = (policy.instructions or "").strip()
    return _dump_frontmatter(fm) + (f"\n{body}\n" if body else "")
