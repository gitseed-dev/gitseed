from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from gitseed_cli.client import CLIError
from gitseed_cli.manifest import (
    agent_to_markdown,
    build_agent_requests,
    build_create_seed_request,
    build_policy_request,
    build_project_requests,
    build_skill_requests,
    build_update_seed_request,
    parse_markdown_manifest,
    policy_to_markdown,
    project_to_markdown,
    seed_to_markdown,
    skill_to_markdown,
)
from gitseed_common.enums import (
    AgentKind,
    AgentRuntime,
    ProjectRole,
    ProjectVisibility,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
)
from gitseed_common.schemas import (
    AgentResponse,
    ProjectResponse,
    SeedResponse,
    SeedTaskResponse,
    SkillResponse,
    StagePolicyResponse,
)


def _sample_seed(stage: SeedStage = SeedStage.IDEATE) -> SeedResponse:
    now = datetime.now(UTC)
    return SeedResponse(
        id="sed_01htestseed",
        project_id="prj_01htestproj",
        number=12,
        title="Add sliding-window rate limiting",
        description="Protect auth endpoints from credential stuffing.",
        specification="Implement Redis token bucket middleware.",
        acceptance_criteria=[
            "Returns 429 with Retry-After header",
            "Configurable per-route limits",
        ],
        plan="1. Add middleware\n2. Add tests",
        stage=stage,
        status=SeedStatus.AWAITING_APPROVAL,
        autonomy=SeedAutonomy.SUPERVISED,
        budget_cap=Decimal("15.00"),
        spent=Decimal("1.25"),
        checkpoint_approved_through=Decimal("0"),
        tags=["security", "api"],
        created_by="usr_01",
        created_at=now,
        updated_at=now,
    )


def test_parse_seed_manifest_and_create_request() -> None:
    md = """---
kind: Seed
project: api
title: Add sliding-window rate limiting
autonomy: supervised
budget_cap: 15.00
tags: [security, api]
---

## Summary
Protect public auth endpoints from credential-stuffing bursts.

## Specification
Implement a Redis-backed sliding-window rate limiter middleware.

## Acceptance Criteria
- [ ] Returns HTTP 429 with `Retry-After` header when limit is exceeded
- [x] Configurable per-route limits
"""
    parsed = parse_markdown_manifest(md, source="seed.md")
    assert parsed.kind == "Seed"
    assert parsed.ignored_readonly_sections == []
    req = build_create_seed_request(parsed, project_id="prj_01htestproj", source="seed.md")
    assert req.project_id == "prj_01htestproj"
    assert req.title == "Add sliding-window rate limiting"
    assert req.description == "Protect public auth endpoints from credential-stuffing bursts."
    assert req.specification == "Implement a Redis-backed sliding-window rate limiter middleware."
    assert req.acceptance_criteria == [
        "Returns HTTP 429 with `Retry-After` header when limit is exceeded",
        "Configurable per-route limits",
    ]
    assert req.autonomy == SeedAutonomy.SUPERVISED
    assert req.budget_cap == Decimal("15.00")
    assert req.tags == ["security", "api"]


def test_seed_manifest_rejects_unknown_heading() -> None:
    md = """---
kind: Seed
title: Bad heading seed
---

## Summary
Valid summary.

## Criteria
- Should fail because heading is not Acceptance Criteria
"""
    with pytest.raises(CLIError, match="Unknown heading '## Criteria'"):
        parse_markdown_manifest(md, source="bad.md")


def test_seed_manifest_rejects_workflow_owned_frontmatter() -> None:
    md = """---
kind: Seed
title: Sneaky stage override
stage: review
---

## Summary
Attempting to bypass gates via manifest.
"""
    with pytest.raises(CLIError, match="Seed manifest cannot set stage"):
        parse_markdown_manifest(md, source="sneaky.md")


def test_seed_roundtrip_strips_readonly_sections_and_omits_locked_spec_past_ideate() -> None:
    seed_plan = _sample_seed(stage=SeedStage.PLAN)
    now = datetime.now(UTC)
    tasks = [
        SeedTaskResponse(
            id="tsk_01",
            seed_id=seed_plan.id,
            position=1,
            title="Create Redis limiter",
            description="Write Lua script",
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
    ]

    exported = seed_to_markdown(seed_plan, project_slug="api", tasks=tasks)
    assert "## Plan (read-only)" in exported
    assert "## Tasks (read-only)" in exported

    # Re-parse exported markdown without modifying specification
    parsed = parse_markdown_manifest(exported, source="exported.md")
    assert "Plan" in parsed.ignored_readonly_sections
    assert "Tasks" in parsed.ignored_readonly_sections

    # Since specification and acceptance_criteria are unchanged, update_req omits them
    # so updating title/summary/tags on a post-Ideate seed succeeds cleanly
    update_req = build_update_seed_request(parsed, existing_seed=seed_plan, source="exported.md")
    dumped = update_req.model_dump(exclude_unset=True)
    assert "specification" not in dumped
    assert "acceptance_criteria" not in dumped

    # If the user alters Specification on a post-Ideate seed, it IS included in UpdateSeedRequest
    # so the server's 409 Conflict governance rule is triggered
    modified_md = exported.replace(
        "Implement Redis token bucket middleware.",
        "Changed spec after Ideate was approved.",
    )
    parsed_mod = parse_markdown_manifest(modified_md, source="modified.md")
    update_mod = build_update_seed_request(
        parsed_mod, existing_seed=seed_plan, source="modified.md"
    )
    dumped_mod = update_mod.model_dump(exclude_unset=True)
    assert dumped_mod["specification"] == "Changed spec after Ideate was approved."


def test_agent_skill_project_policy_roundtrip() -> None:
    now = datetime.now(UTC)
    agent = AgentResponse(
        id="agt_01hcoder",
        handle="coder",
        name="Coder",
        kind=AgentKind.STAGE,
        template="coder",
        instructions="You are @coder. Write clean code and tests.",
        runtime=AgentRuntime.CLAUDE_CODE,
        model="claude-sonnet-4-20250514",
        skills=["pytest-conventions"],
        tools=["repo_read", "repo_write", "shell_exec"],
        enabled=True,
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    agent_md = agent_to_markdown(agent)
    parsed_agent = parse_markdown_manifest(agent_md)
    create_ag, update_ag = build_agent_requests(parsed_agent)
    assert create_ag.handle == "coder"
    assert create_ag.kind == AgentKind.STAGE
    assert "You are @coder" in create_ag.instructions
    assert update_ag.name == "Coder"

    skill = SkillResponse(
        id="skl_01hpytest",
        slug="pytest-conventions",
        name="Pytest Conventions",
        description="Testing rules for Python services.",
        content="Always use fixtures and assert status codes.",
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    skill_md = skill_to_markdown(skill)
    parsed_skill = parse_markdown_manifest(skill_md)
    create_sk, _ = build_skill_requests(parsed_skill)
    assert create_sk.slug == "pytest-conventions"
    assert create_sk.content == "Always use fixtures and assert status codes."

    project = ProjectResponse(
        id="prj_01hapi",
        slug="api",
        name="Core API",
        description="Backend control plane API.",
        visibility=ProjectVisibility.PRIVATE,
        monthly_budget=Decimal("200.00"),
        seed_budget_cap=Decimal("10.00"),
        seed_counter=5,
        archived_at=None,
        my_role=ProjectRole.MAINTAINER,
        created_at=now,
        updated_at=now,
    )
    proj_md = project_to_markdown(project)
    parsed_proj = parse_markdown_manifest(proj_md)
    create_pr, _ = build_project_requests(parsed_proj)
    assert create_pr.slug == "api"
    assert create_pr.description == "Backend control plane API."

    policy = StagePolicyResponse(
        stage=SeedStage.EXECUTE,
        enabled=True,
        agent_id="agt_01hcoder",
        requires_approval=True,
        min_approvers=1,
        min_approver_role=ProjectRole.DEVELOPER,
        allow_auto_approve=False,
        allow_self_approval=True,
        max_attempts=2,
        timeout_minutes=30,
        overridden_fields=["agent_id"],
    )
    pol_md = policy_to_markdown(policy, project_slug="api", agent_handle="@coder")
    parsed_pol = parse_markdown_manifest(pol_md)
    stage, proj_slug, _, proj_pol_req = build_policy_request(
        parsed_pol, resolved_agent_id="agt_01hcoder"
    )
    assert stage == SeedStage.EXECUTE
    assert proj_slug == "api"
    assert proj_pol_req.agent_id == "agt_01hcoder"
    assert proj_pol_req.requires_approval is True
