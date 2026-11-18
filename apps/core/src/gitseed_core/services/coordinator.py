"""Conversational @coordinator assistant service backed by LiteLLM Proxy and live workspace state."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from gitseed_common.enums import (
    SEED_OPEN_STATUSES,
    AgentKind,
    BackendType,
    OrgRole,
    ProjectVisibility,
    SeedAutonomy,
    SeedStage,
)
from gitseed_common.schemas.agent import (
    CoordinatorChatRequest,
    CoordinatorChatResponse,
    ProposedSeedDraft,
)
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.agent import Agent, AgentBackend
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import (
    Project,
    ProjectBackend,
    ProjectMember,
    ProjectRepository,
)
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedTask
from gitseed_core.models.skill import Skill
from gitseed_core.models.user import User
from gitseed_core.security import decrypt_secrets
from gitseed_core.services.gateway import (
    _litellm_headers,
    _probe_live_backend_models,
    docker_host_fallback_url,
    extract_enabled_model_ids,
    is_personal_mode_backend,
    sync_backend_to_litellm,
    validate_personal_mode_backend_access,
)
from gitseed_core.services.git_browse import get_repo_blob, get_repo_tree
from gitseed_core.services.policies import (
    DEFAULT_ORG_STAGE_POLICIES,
    ensure_org_policies,
    get_effective_stage_policy,
)

logger = logging.getLogger("uvicorn.error")

_PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-5.4",
    "google": "gemini-4-argon",
    "gemini": "gemini-4-argon",
    "antigravity": "gemini-4-argon",
    "xai": "grok-4.7",
    "openrouter": "anthropic/claude-sonnet-5-5",
    "ollama": "qwen2.5-coder:32b",
    "lmstudio": "local-model",
}


async def _resolve_coordinator_agent(session: AsyncSession) -> Agent | None:
    stmt = (
        select(Agent)
        .where(
            col(Agent.archived_at).is_(None),
            or_(
                Agent.kind == AgentKind.ASSISTANT,
                Agent.handle == "coordinator",
            ),
        )
        .order_by(col(Agent.enabled).desc(), col(Agent.created_at).asc())
    )
    return (await session.execute(stmt)).scalars().first()


async def _resolve_coordinator_model(
    session: AsyncSession,
    agent: Agent | None,
    project: Project | None,
    *,
    actor_user_id: str | None = None,
) -> tuple[Backend | None, str]:
    stmt = (
        select(Backend)
        .where(
            Backend.type == BackendType.INFERENCE,
            col(Backend.enabled).is_(True),
        )
        .order_by(col(Backend.created_at).asc())
    )
    backends = list((await session.execute(stmt)).scalars().all())
    if not backends:
        return None, "default"

    if project is not None and project.backends_restricted:
        allowed_bids = set(
            (
                await session.execute(
                    select(ProjectBackend.backend_id).where(
                        ProjectBackend.project_id == project.id
                    )
                )
            )
            .scalars()
            .all()
        )
        backends = [b for b in backends if b.id in allowed_bids]

    accessible: list[Backend] = []
    for b in backends:
        if b.access_restricted and agent is not None:
            grant = await session.get(AgentBackend, (b.id, agent.id))
            if grant is None:
                continue
        if is_personal_mode_backend(b):
            try:
                validate_personal_mode_backend_access(
                    session, b, actor_user_id=actor_user_id or ""
                )
            except Exception:
                continue
        accessible.append(b)
    if not accessible:
        return None, (agent.model if agent and agent.model else "default").strip() or "default"

    # Prefer HTTP/LiteLLM-capable backends (API key / local Ollama / LM Studio) over
    # Personal Mode CLI OAuth backends, whose subscription credentials never enter LiteLLM.
    accessible.sort(
        key=lambda b: (
            1 if is_personal_mode_backend(b) else 0,
            0 if bool((b.config or {}).get("is_sdlc_default")) else 1,
        )
    )

    requested = (agent.model if agent and agent.model else "default").strip()
    if requested and requested != "default":
        for b in accessible:
            if requested in extract_enabled_model_ids(b):
                return b, requested
        return accessible[0], requested

    for b in accessible:
        enabled_ids = extract_enabled_model_ids(b)
        if enabled_ids:
            return b, enabled_ids[0]

    for b in accessible:
        probed = await _probe_live_backend_models(b)
        if probed:
            return b, probed[0]

    first = accessible[0]
    return first, _PROVIDER_DEFAULT_MODEL.get(first.provider.lower(), "gpt-5.4")


async def _build_workspace_snapshot(
    session: AsyncSession,
    actor: User,
    *,
    project_id: str | None = None,
    seed_id: str | None = None,
) -> tuple[str, str, Project | None]:
    """Return `(system_context_markdown, fallback_reply, resolved_project)`."""
    if actor.org_role == OrgRole.ADMIN:
        proj_stmt = select(Project).where(col(Project.archived_at).is_(None))
    else:
        proj_stmt = (
            select(Project)
            .outerjoin(
                ProjectMember,
                (col(ProjectMember.project_id) == col(Project.id))
                & (col(ProjectMember.user_id) == actor.id),
            )
            .where(
                col(Project.archived_at).is_(None),
                or_(
                    col(Project.visibility) == ProjectVisibility.PUBLIC,
                    col(ProjectMember.user_id).is_not(None),
                ),
            )
        )
    visible_projects = list((await session.execute(proj_stmt)).scalars().all())
    proj_by_id = {p.id: p for p in visible_projects}

    focused_project: Project | None = None
    if project_id:
        focused_project = proj_by_id.get(project_id)
        if focused_project is None:
            for p in visible_projects:
                if p.slug == project_id:
                    focused_project = p
                    break

    visible_pids = (
        [focused_project.id]
        if focused_project is not None
        else [p.id for p in visible_projects]
    )

    seeds: list[Seed] = []
    if visible_pids:
        seed_stmt = (
            select(Seed)
            .where(col(Seed.project_id).in_(visible_pids))
            .order_by(col(Seed.updated_at).desc())
            .limit(40)
        )
        seeds = list((await session.execute(seed_stmt)).scalars().all())

    seed_ids = [s.id for s in seeds]
    runs: list[Run] = []
    if seed_ids:
        run_stmt = (
            select(Run)
            .where(col(Run.seed_id).in_(seed_ids))
            .order_by(col(Run.created_at).desc())
            .limit(25)
        )
        runs = list((await session.execute(run_stmt)).scalars().all())

    repos: list[ProjectRepository] = []
    if visible_pids:
        repo_stmt = select(ProjectRepository).where(
            col(ProjectRepository.project_id).in_(visible_pids)
        )
        repos = list((await session.execute(repo_stmt)).scalars().all())
    repos_by_proj: dict[str, list[str]] = {}
    for r in repos:
        repos_by_proj.setdefault(r.project_id, []).append(
            f"{r.full_name} ({r.default_branch})"
        )

    open_statuses = {s.value for s in SEED_OPEN_STATUSES}
    open_seeds = [s for s in seeds if str(s.status) in open_statuses]
    attention_seeds = [
        s
        for s in seeds
        if str(s.status) in ("awaiting_approval", "blocked", "failed")
    ]
    running_seeds = [s for s in seeds if str(s.status) == "running"]
    total_spend = sum(float(s.spent or 0) for s in seeds)
    total_tokens = sum(int(r.input_tokens or 0) + int(r.output_tokens or 0) for r in runs)

    lines: list[str] = [
        "## Live GitSeed Control Plane Context",
        f"- **Operator**: {actor.preferred_name or actor.name} (role: `{actor.org_role}`)",
        f"- **Scope**: {focused_project.name + ' (`' + focused_project.slug + '`)' if focused_project else 'All Visible Projects'}",
        f"- **Summary**: {len(open_seeds)} open seed(s), {len(attention_seeds)} needing operator attention, {len(running_seeds)} actively running, ${total_spend:.2f} tracked spend, {total_tokens:,} tokens across recent runs.",
    ]

    if visible_projects:
        lines.append("\n### Projects")
        for p in (
            [focused_project] if focused_project is not None else visible_projects[:12]
        ):
            repo_str = ", ".join(repos_by_proj.get(p.id, [])) or "no repos connected"
            lines.append(
                f"- **{p.name}** (`{p.slug}`): repos=[{repo_str}], monthly_budget={p.monthly_budget or 'org default'}"
            )

    if seeds:
        lines.append("\n### Seeds (Most Recent)")
        for s in seeds[:25]:
            p = proj_by_id.get(s.project_id)
            ref = f"{p.slug if p else 'proj'}#{s.number}"
            blocked_note = f" | blocked_reason: {s.blocked_reason}" if s.blocked_reason else ""
            desc_short = (s.description or "").strip().replace("\n", " ")[:180]
            lines.append(
                f"- **{ref}** — {s.title} | stage=`{s.stage}`, status=`{s.status}`, autonomy=`{s.autonomy}`, spent=${float(s.spent or 0):.2f}{blocked_note}"
                + (f"\n  Summary: {desc_short}" if desc_short else "")
            )

    if seed_id:
        target_seed = next((s for s in seeds if s.id == seed_id), None)
        if target_seed is None:
            target_seed = await session.get(Seed, seed_id)
        if target_seed is not None and target_seed.project_id in proj_by_id:
            tasks = list(
                (
                    await session.execute(
                        select(SeedTask)
                        .where(SeedTask.seed_id == target_seed.id)
                        .order_by(col(SeedTask.position).asc())
                    )
                )
                .scalars()
                .all()
            )
            lines.append(f"\n### Focused Seed Detail (`#{target_seed.number}` — {target_seed.title})")
            if target_seed.specification:
                lines.append(f"Specification excerpt:\n{target_seed.specification[:1200]}")
            if tasks:
                lines.append(
                    "Tasks:\n"
                    + "\n".join(
                        f"  - [{t.status}] #{t.position + 1}: {t.title}" for t in tasks
                    )
                )

    if runs:
        lines.append("\n### Recent Agent Runs")
        for r in runs[:15]:
            s = next((item for item in seeds if item.id == r.seed_id), None)
            p = proj_by_id.get(s.project_id) if s else None
            ref = f"{p.slug if p else ''}#{s.number}" if s else r.seed_id
            out_dict = r.output if isinstance(r.output, dict) else {}
            summary_note = str(out_dict.get("summary_comment") or r.error or "").strip().replace("\n", " ")[:180]
            tok = int(r.input_tokens or 0) + int(r.output_tokens or 0)
            lines.append(
                f"- Run `{r.id}` on **{ref}** ({r.stage} #{r.attempt}): status=`{r.status}`, model=`{r.model}`, cost=${float(r.cost or 0):.4f}, tokens={tok:,}"
                + (f" — {summary_note}" if summary_note else "")
            )

    scope_label = focused_project.name if focused_project else "All Projects"
    fallback = (
        f"**{scope_label}** status: **{len(open_seeds)}** open seed(s), "
        f"**{len(attention_seeds)}** needing operator review, "
        f"**{len(running_seeds)}** currently running, and **${total_spend:.2f}** total spend "
        f"({total_tokens:,} tokens across {len(runs)} recent run(s))."
    )
    if attention_seeds:
        att_lines = []
        for s in attention_seeds[:5]:
            p = proj_by_id.get(s.project_id)
            ref = f"{p.slug if p else ''}#{s.number}"
            att_lines.append(f"- `{ref}` **{s.title}** (`{s.stage}` · `{s.status}`)")
        fallback += "\n\nSeeds needing attention:\n" + "\n".join(att_lines)

    return "\n".join(lines), fallback, focused_project


_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "have", "what", "how",
    "can", "you", "should", "would", "could", "into", "about", "want", "need",
    "make", "like", "build", "create", "add", "update", "seed", "seeds", "project",
    "feature", "idea", "brainstorm", "let", "lets", "some", "more", "when", "where",
}


def _extract_keywords(messages: list[Any]) -> list[str]:
    text = " ".join(str(getattr(m, "content", "") or "") for m in messages[-8:]).lower()
    tokens = re.findall(r"[a-z0-9_./-]{3,}", text)
    seen: set[str] = set()
    keywords: list[str] = []
    for tok in tokens:
        clean = tok.strip("./-_")
        if len(clean) < 3 or clean in _STOPWORDS or clean.isdigit():
            continue
        if clean not in seen:
            seen.add(clean)
            keywords.append(clean)
    return keywords[:24]


async def _build_brainstorm_context(
    session: AsyncSession,
    actor: User,
    focused_project: Project | None = None,
    messages: list[Any] | None = None,
    *,
    project_id: str | None = None,
) -> tuple[str, list[str], Project | None, list[Seed]]:
    """Inspect repository tree, README, matching file blobs, and related seeds for Brainstorm mode."""
    messages = messages or []
    referenced_files: list[str] = []
    sections: list[str] = []

    if actor.org_role == OrgRole.ADMIN:
        proj_stmt = select(Project).where(col(Project.archived_at).is_(None))
    else:
        proj_stmt = (
            select(Project)
            .outerjoin(
                ProjectMember,
                (col(ProjectMember.project_id) == col(Project.id))
                & (col(ProjectMember.user_id) == actor.id),
            )
            .where(
                col(Project.archived_at).is_(None),
                or_(
                    col(Project.visibility) == ProjectVisibility.PUBLIC,
                    col(ProjectMember.user_id).is_not(None),
                ),
            )
        )
    visible_projects = list((await session.execute(proj_stmt)).scalars().all())
    visible_by_id = {p.id: p for p in visible_projects}
    if focused_project is not None and focused_project.id not in visible_by_id:
        focused_project = None
    if focused_project is None and project_id:
        focused_project = visible_by_id.get(project_id)
        if focused_project is None:
            for p in visible_projects:
                if p.slug == project_id:
                    focused_project = p
                    break
    convo_lower = " ".join(str(getattr(m, "content", "") or "") for m in messages).lower()

    if focused_project is None and visible_projects:
        for p in visible_projects:
            if p.slug.lower() in convo_lower or p.name.lower() in convo_lower:
                focused_project = p
                break
        if focused_project is None:
            focused_project = visible_projects[0]

    target_pids = [focused_project.id] if focused_project else [p.id for p in visible_projects]
    project_seeds: list[Seed] = []
    if target_pids:
        seed_stmt = (
            select(Seed)
            .where(col(Seed.project_id).in_(target_pids))
            .order_by(col(Seed.updated_at).desc())
            .limit(50)
        )
        project_seeds = list((await session.execute(seed_stmt)).scalars().all())

    keywords = _extract_keywords(messages)

    # 1. Dynamic Repository Tree, README & File Blob Inspection
    if focused_project is not None:
        repo_stmt = (
            select(ProjectRepository)
            .where(ProjectRepository.project_id == focused_project.id)
            .order_by(col(ProjectRepository.is_primary).desc(), col(ProjectRepository.created_at).asc())
        )
        repos = list((await session.execute(repo_stmt)).scalars().all())
        if repos:
            primary_repo = repos[0]
            try:
                tree_resp = await get_repo_tree(
                    session,
                    actor,
                    focused_project.id,
                    int(primary_repo.id or 0),
                    ref=primary_repo.default_branch,
                    path="",
                )
                tree_entries = tree_resp.get("entries") or []
                entry_lines = [
                    f"  - [{'dir' if e.get('type') in ('dir', 'tree') else 'file'}] `{e.get('path')}`"
                    for e in tree_entries[:60]
                    if isinstance(e, dict)
                ]
                repo_section = [
                    f"## Live Repository Context (`{primary_repo.full_name}` @ `{tree_resp.get('ref') or primary_repo.default_branch}`)",
                    "### Top-Level Repository Structure",
                    *(entry_lines or ["  - (empty repository)"]),
                ]
                readme_obj = tree_resp.get("readme")
                if isinstance(readme_obj, dict) and readme_obj.get("content"):
                    readme_path = str(readme_obj.get("path") or "README.md")
                    referenced_files.append(readme_path)
                    readme_excerpt = str(readme_obj.get("content") or "").strip()[:2200]
                    repo_section.append(
                        f"\n### Repository README (`{readme_path}`)\n```markdown\n{readme_excerpt}\n```"
                    )

                # Inspect 1 relevant subdirectory if present (e.g. src, apps, packages, lib)
                sub_Entries: list[dict[str, Any]] = []
                preferred_dirs = [
                    str(e.get("path"))
                    for e in tree_entries
                    if isinstance(e, dict)
                    and e.get("type") in ("dir", "tree")
                    and (
                        str(e.get("name") or "").lower() in ("src", "apps", "packages", "lib", "server", "client", "cmd", "internal")
                        or any(k in str(e.get("name") or "").lower() for k in keywords)
                    )
                ]
                for sub_dir in preferred_dirs[:2]:
                    try:
                        sub_tree = await get_repo_tree(
                            session,
                            actor,
                            focused_project.id,
                            int(primary_repo.id or 0),
                            ref=primary_repo.default_branch,
                            path=sub_dir,
                        )
                        sub_tree_entries = [
                            e for e in (sub_tree.get("entries") or []) if isinstance(e, dict)
                        ]
                        sub_Entries.extend(sub_tree_entries)
                        sub_list = ", ".join(f"`{e.get('path')}`" for e in sub_tree_entries[:25])
                        if sub_list:
                            repo_section.append(f"\n### Directory `{sub_dir}/` Contents\n{sub_list}")
                    except Exception:
                        pass

                # Dynamically fetch up to 3 relevant file blobs mentioned in conversation or matching keywords
                all_known_files = [
                    str(e.get("path"))
                    for e in [*tree_entries, *sub_Entries]
                    if isinstance(e, dict) and e.get("type") in ("file", "blob") and e.get("path")
                ]
                candidate_paths: list[str] = []
                for path in all_known_files:
                    fname = path.split("/")[-1].lower()
                    if path.lower() in convo_lower or fname in convo_lower:
                        candidate_paths.append(path)
                for path in all_known_files:
                    if path in candidate_paths:
                        continue
                    fname = path.split("/")[-1].lower()
                    if any(k in fname for k in keywords if len(k) >= 4):
                        candidate_paths.append(path)
                for cfg_name in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod"):
                    if len(candidate_paths) < 2 and cfg_name in all_known_files and cfg_name not in candidate_paths:
                        candidate_paths.append(cfg_name)

                for blob_path in candidate_paths[:3]:
                    try:
                        blob = await get_repo_blob(
                            session,
                            actor,
                            focused_project.id,
                            int(primary_repo.id or 0),
                            ref=primary_repo.default_branch,
                            path=blob_path,
                        )
                        blob_content = str(blob.get("content") or "").strip()
                        resolved_blob_path = str(blob.get("path") or blob_path)
                        if not blob.get("is_binary") and blob_content:
                            if resolved_blob_path not in referenced_files:
                                referenced_files.append(resolved_blob_path)
                            snippet = blob_content[:1600]
                            repo_section.append(
                                f"\n### Inspected File (`{resolved_blob_path}`)\n```\n{snippet}\n```"
                            )
                    except Exception:
                        pass

                sections.append("\n".join(repo_section))
            except Exception as exc:
                logger.debug("Brainstorm repo inspection skipped for %s: %s", focused_project.slug, exc)

    # 2. Dynamic Seed Lookup (matching keywords + recent seeds with specs & acceptance criteria)
    if project_seeds:
        scored_seeds: list[tuple[int, Seed]] = []
        for s in project_seeds:
            haystack = " ".join(
                [
                    s.title or "",
                    s.description or "",
                    s.specification or "",
                    " ".join(s.tags or []),
                ]
            ).lower()
            score = sum(2 if k in (s.title or "").lower() else 1 for k in keywords if k in haystack)
            scored_seeds.append((score, s))
        scored_seeds.sort(key=lambda item: (item[0], item[1].number), reverse=True)
        top_seeds = [s for _, s in scored_seeds[:8]]
        seed_lines = [
            "## Relevant Existing Project Seeds (Avoid Duplication & Build Upon These)"
        ]
        for s in top_seeds:
            criteria_str = (
                " | Criteria: " + "; ".join((s.acceptance_criteria or [])[:4])
                if s.acceptance_criteria
                else ""
            )
            tags_str = f" | tags={s.tags}" if s.tags else ""
            spec_str = (
                f"\n  Specification excerpt: {s.specification.strip()[:450]}"
                if s.specification
                else ""
            )
            seed_lines.append(
                f"- **#{s.number} {s.title}** (stage=`{s.stage}`, status=`{s.status}`{tags_str})\n"
                f"  Summary: {(s.description or '').strip()[:240]}{criteria_str}{spec_str}"
            )
        sections.append("\n".join(seed_lines))

    return "\n\n".join(sections), referenced_files, focused_project, project_seeds


def _extract_seed_proposals(raw_reply: str) -> tuple[str, list[ProposedSeedDraft]]:
    """Parse `<seed_proposals>[...]</seed_proposals>` out of the assistant response."""
    proposals: list[ProposedSeedDraft] = []
    cleaned_reply = raw_reply

    pattern = re.compile(r"<seed_proposals>\s*(.*?)\s*</seed_proposals>", re.DOTALL | re.IGNORECASE)
    matches = list(pattern.finditer(raw_reply))
    json_blobs: list[str] = []
    if matches:
        for m in matches:
            json_blobs.append(m.group(1).strip())
        cleaned_reply = pattern.sub("", raw_reply).strip()
    else:
        # Also check if a fenced json block with seed proposal objects was emitted
        fenced = re.findall(r"```json\s*(\[\s*\{.*?\}\s*\])\s*```", raw_reply, flags=re.DOTALL)
        for block in fenced:
            if '"title"' in block and ('"specification"' in block or '"acceptance_criteria"' in block):
                json_blobs.append(block)
                cleaned_reply = raw_reply.replace(f"```json\n{block}\n```", "").strip()

    for raw_blob in json_blobs:
        snippet = raw_blob.strip()
        if snippet.startswith("```"):
            snippet = re.sub(r"^```(?:json)?\s*|\s*```$", "", snippet, flags=re.DOTALL).strip()
        try:
            parsed = json.loads(snippet)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                raw_autonomy = str(item.get("autonomy") or "supervised").lower().strip()
                try:
                    autonomy = SeedAutonomy(raw_autonomy)
                except ValueError:
                    autonomy = SeedAutonomy.SUPERVISED
                raw_criteria = item.get("acceptance_criteria") or []
                criteria = [
                    str(c).strip()
                    for c in (raw_criteria if isinstance(raw_criteria, list) else [raw_criteria])
                    if str(c).strip()
                ]
                raw_tags = item.get("tags") or []
                tags = [
                    str(t).strip().lower()
                    for t in (raw_tags if isinstance(raw_tags, list) else [raw_tags])
                    if str(t).strip()
                ]
                proposals.append(
                    ProposedSeedDraft(
                        title=title[:200],
                        description=str(item.get("description") or "").strip(),
                        specification=str(item.get("specification") or "").strip(),
                        acceptance_criteria=criteria[:10],
                        tags=tags[:8],
                        autonomy=autonomy,
                    )
                )
        except Exception as exc:
            logger.debug("Failed to parse <seed_proposals> JSON block: %s", exc)

    return cleaned_reply or raw_reply, proposals


def _build_fallback_brainstorm_proposals(
    messages: list[Any],
    focused_project: Project | None,
    referenced_files: list[str],
    existing_seeds: list[Seed],
    propose_now: bool,
    *,
    reason: str | None = None,
) -> tuple[str, list[ProposedSeedDraft]]:
    """Honest reply for when no model answer is available.

    Never fabricates recommendations or seed proposals from the prompt text: it says the
    model couldn't be reached (and why), and lists the project's open seeds as plain facts.
    """
    proj_name = focused_project.name if focused_project else "this workspace"
    why = f" ({reason})" if reason else ""
    lines = [
        f"I couldn't get an answer from the model{why}, so I don't have a real recommendation yet.",
    ]
    def _val(v: Any) -> str:
        return str(getattr(v, "value", v) or "").lower()

    open_seeds = [s for s in existing_seeds if _val(getattr(s, "status", "")) not in ("merged", "closed")][:8]
    if open_seeds:
        lines += ["", f"**Open seeds in {proj_name}:**"]
        for s in open_seeds:
            stage = _val(getattr(s, "stage", ""))
            lines.append(f"- #{s.number} {s.title}" + (f" \u00b7 {stage}" if stage else ""))
    lines += [
        "",
        "Try again in a moment. If this keeps happening, check the Inference backend for the coordinator "
        "(it may be offline or too slow for large prompts).",
    ]
    return "\n".join(lines), []


async def chat_with_coordinator(
    session: AsyncSession,
    actor: User,
    payload: CoordinatorChatRequest,
) -> CoordinatorChatResponse:
    """Execute a multi-turn conversational query against the @coordinator agent."""
    coordinator = await _resolve_coordinator_agent(session)
    handle = coordinator.handle if coordinator else "coordinator"
    name = coordinator.name if coordinator else "Coordinator"
    instructions = (
        coordinator.instructions.strip()
        if coordinator and coordinator.instructions
        else (
            "System assistant for workspace steering, conversational search, "
            "seed triage, and pipeline inspection."
        )
    )

    context_md, fallback_reply, focused_project = await _build_workspace_snapshot(
        session,
        actor,
        project_id=payload.project_id,
        seed_id=payload.seed_id,
    )

    is_brainstorm = payload.mode == "brainstorm" or payload.propose_seeds
    brainstorm_context_md = ""
    referenced_files: list[str] = []
    project_seeds: list[Seed] = []
    if is_brainstorm:
        (
            brainstorm_context_md,
            referenced_files,
            focused_project,
            project_seeds,
        ) = await _build_brainstorm_context(
            session,
            actor,
            focused_project,
            payload.messages,
        )

    skill_blocks: list[str] = []
    if coordinator and coordinator.skills:
        skill_rows = (
            await session.execute(
                select(Skill).where(
                    (col(Skill.id).in_(coordinator.skills))
                    | (col(Skill.slug).in_(coordinator.skills))
                )
            )
        ).scalars().all()
        for sk in skill_rows:
            skill_blocks.append(f"### Skill: {sk.name}\n{sk.content}")

    backend, resolved_model = await _resolve_coordinator_model(
        session, coordinator, focused_project, actor_user_id=actor.id
    )
    if backend is None:
        if is_brainstorm:
            fb_reply, fb_proposals = _build_fallback_brainstorm_proposals(
                payload.messages,
                focused_project,
                referenced_files,
                project_seeds,
                payload.propose_seeds,
                reason="no Inference backend is configured for the coordinator",
            )
            return CoordinatorChatResponse(
                reply=fb_reply,
                agent_handle=handle,
                model="none",
                proposed_seeds=fb_proposals,
                referenced_files=referenced_files,
            )
        return CoordinatorChatResponse(
            reply=fallback_reply,
            agent_handle=handle,
            model="none",
        )

    if is_personal_mode_backend(backend):
        personal_note = (
            "Personal Mode CLI OAuth credentials run inside isolated stage and Live Agent Session "
            "containers rather than the shared HTTP Model Gateway. Launch a Live Agent Session for "
            "interactive CLI chat, or connect an API-key / local Ollama backend for live `@coordinator` responses."
        )
        if is_brainstorm:
            fb_reply, fb_proposals = _build_fallback_brainstorm_proposals(
                payload.messages,
                focused_project,
                referenced_files,
                project_seeds,
                payload.propose_seeds,
                reason=personal_note,
            )
            return CoordinatorChatResponse(
                reply=fb_reply,
                agent_handle=handle,
                model="personal-mode-cli",
                proposed_seeds=fb_proposals,
                referenced_files=referenced_files,
            )
        return CoordinatorChatResponse(
            reply=f"> **Personal Mode (`cli_oauth`)**: {personal_note}\n\n{fallback_reply}",
            agent_handle=handle,
            model="personal-mode-cli",
        )

    await sync_backend_to_litellm(backend, extra_models=[resolved_model])

    brainstorm_guidelines = ""
    if is_brainstorm:
        min_crit = int(DEFAULT_ORG_STAGE_POLICIES[SeedStage.IDEATE]["min_acceptance_criteria"])
        max_crit = int(DEFAULT_ORG_STAGE_POLICIES[SeedStage.IDEATE]["max_acceptance_criteria"])
        if focused_project is not None:
            ideate_pol = await get_effective_stage_policy(
                session, focused_project.id, SeedStage.IDEATE
            )
            if ideate_pol.min_acceptance_criteria is not None:
                min_crit = ideate_pol.min_acceptance_criteria
            if ideate_pol.max_acceptance_criteria is not None:
                max_crit = ideate_pol.max_acceptance_criteria
        else:
            org_map = await ensure_org_policies(session)
            org_ideate = org_map.get(SeedStage.IDEATE)
            if org_ideate is not None:
                if org_ideate.min_acceptance_criteria is not None:
                    min_crit = org_ideate.min_acceptance_criteria
                if org_ideate.max_acceptance_criteria is not None:
                    max_crit = org_ideate.max_acceptance_criteria

        force_propose_instruction = (
            "- IMPORTANT: The operator clicked **Propose Seeds** (`propose_seeds=true`). You MUST include a `<seed_proposals>` JSON block at the end of this response with 1 to 4 concrete, ready-to-plant seeds based on the conversation so far."
            if payload.propose_seeds
            else "- Whenever the concept is concrete enough (or when the operator asks to generate/break down seeds), append a `<seed_proposals>` JSON block at the end of your response with 1 to 4 structured seed proposals."
        )
        brainstorm_guidelines = (
            "## Brainstorm & Seed Ideation Studio Mode\n"
            "You are operating in **Brainstorm Mode** as a Staff Architect & Product Ideation Partner.\n"
            "- Ground your architectural advice in the inspected repository directory structure, README, file blobs, and existing project seeds shown above.\n"
            "- Reference existing seeds by `#number` so the operator avoids duplicating existing work and can build cleanly on prior seeds.\n"
            "- Help the operator refine scope, trade-offs, data models, API contracts, and incremental rollout strategy.\n"
            f"- For each proposed seed's `acceptance_criteria`, include between **{min_crit} and {max_crit}** items (per the project's Ideate stage policy). "
            "Each criterion must: (1) describe an outcome rather than an implementation step, (2) be checkable by Review on its own, "
            "(3) not repeat what `specification` already says, and (4) include one covering tests where relevant.\n"
            f"{force_propose_instruction}\n"
            "- Format the `<seed_proposals>` block at the very end of your reply using valid JSON exactly like this:\n"
            "<seed_proposals>\n"
            "[\n"
            "  {\n"
            '    "title": "Short imperative seed title",\n'
            '    "description": "1-2 sentence high-level summary of the seed goal and value.",\n'
            '    "specification": "Detailed technical specification referencing repo modules/files, architecture, and implementation approach.",\n'
            '    "acceptance_criteria": ["Testable criterion 1", "Testable criterion 2", "Testable criterion 3"],\n'
            '    "tags": ["feature", "backend"],\n'
            '    "autonomy": "supervised"\n'
            "  }\n"
            "]\n"
            "</seed_proposals>"
        )

    system_prompt = "\n\n".join(
        [
            f"You are @{handle} ({name}), the GitSeed Control Plane assistant.",
            f"## Agent Instructions\n{instructions}",
            *(["## Attached Skills\n" + "\n\n".join(skill_blocks)] if skill_blocks else []),
            context_md,
            *([brainstorm_context_md] if brainstorm_context_md else []),
            *([brainstorm_guidelines] if brainstorm_guidelines else []),
            (
                "## Response Guidelines\n"
                "- Answer the operator's question directly and concisely using Markdown.\n"
                "- Reference specific seeds by their `#number` and title, and mention their current stage/status.\n"
                "- When a seed failed or had review changes requested, summarize the root cause and advise the operator on next steps."
            ),
        ]
    )

    chat_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for m in payload.messages[-16:]:
        chat_messages.append({"role": m.role, "content": m.content})
    if payload.propose_seeds and chat_messages[-1]["role"] == "user":
        chat_messages[-1] = {
            "role": "user",
            "content": (
                chat_messages[-1]["content"]
                + "\n\n[System Note: Please synthesize our discussion and include a `<seed_proposals>[...]</seed_proposals>` JSON block with 1 to 4 well-scoped seeds ready to be planted.]"
            ),
        }

    base_url = settings.LITELLM_URL.rstrip("/")
    # Brainstorm prompts carry repo context; local models (LM Studio/Ollama) can take minutes
    llm_timeout = 180.0 if is_brainstorm else 90.0
    failure_reason: str | None = None
    timed_out = False
    gateway_rejected = False
    no_proposals_note = (
        "\n\n_No seed proposals came back this time. Click **Propose seeds** again, "
        "or describe the idea in a bit more detail._"
    )
    try:
        async with httpx.AsyncClient(timeout=llm_timeout) as client:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                headers=_litellm_headers(),
                json={
                    "model": resolved_model,
                    "messages": chat_messages,
                    "temperature": 0.3 if is_brainstorm else 0.2,
                },
            )
            if 200 <= resp.status_code < 300 and isinstance(resp.json(), dict):
                data = resp.json()
                choices = data.get("choices") or []
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                in_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                out_tok = int(
                    usage.get("completion_tokens") or usage.get("output_tokens") or 0
                )
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message") or {}
                    content = str(msg.get("content") or "").strip()
                    if content:
                        cleaned_reply, proposals = _extract_seed_proposals(content)
                        if payload.propose_seeds and not proposals:
                            cleaned_reply += no_proposals_note
                        return CoordinatorChatResponse(
                            reply=cleaned_reply,
                            agent_handle=handle,
                            model=resolved_model,
                            input_tokens=in_tok,
                            output_tokens=out_tok,
                            proposed_seeds=proposals,
                            referenced_files=referenced_files,
                        )
                    failure_reason = "the model returned an empty reply"
            else:
                if resp.status_code in (400, 401, 402, 403, 429):
                    gateway_rejected = True
                failure_reason = f"model gateway returned HTTP {resp.status_code}"
                logger.warning(
                    "Coordinator chat via LiteLLM failed (HTTP %s, model %s): %s",
                    resp.status_code,
                    resolved_model,
                    resp.text[:300],
                )
    except httpx.TimeoutException:
        timed_out = True
        failure_reason = f"no reply from {resolved_model} within {int(llm_timeout)}s"
        logger.warning("Coordinator chat timed out after %ss (model %s)", int(llm_timeout), resolved_model)
    except Exception as exc:
        failure_reason = "couldn't reach the model gateway"
        logger.warning("Coordinator chat via LiteLLM failed: %s", exc)

    # Direct upstream fallback if the LiteLLM proxy is unreachable or errored. Not after a
    # timeout or explicit gateway rejection (400/401/402/403/429 budget/rate-limit).
    try:
        if timed_out or gateway_rejected:
            raise RuntimeError("skipped after timeout or gateway rejection")
        secrets_map = decrypt_secrets(backend.secrets_encrypted)
        api_key = secrets_map.get("api_key", "").strip()
        provider = backend.provider.lower()
        raw_ep = (backend.endpoint or "").strip().rstrip("/")
        endpoint = docker_host_fallback_url(raw_ep) if raw_ep else ""
        if not endpoint:
            if provider == "lmstudio":
                endpoint = "http://host.docker.internal:1234/v1"
            elif provider == "ollama":
                endpoint = "http://host.docker.internal:11434/v1"
            elif provider == "openai":
                endpoint = "https://api.openai.com/v1"
        if endpoint and provider != "anthropic":
            chat_url = (
                f"{endpoint}/chat/completions"
                if endpoint.endswith("/v1")
                else f"{endpoint}/v1/chat/completions"
            )
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            async with httpx.AsyncClient(timeout=llm_timeout) as client:
                resp = await client.post(
                    chat_url,
                    headers=headers,
                    json={
                        "model": resolved_model,
                        "messages": chat_messages,
                        "temperature": 0.3 if is_brainstorm else 0.2,
                    },
                )
                if 200 <= resp.status_code < 300 and isinstance(resp.json(), dict):
                    data = resp.json()
                    choices = data.get("choices") or []
                    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                    in_tok = int(usage.get("prompt_tokens") or 0)
                    out_tok = int(usage.get("completion_tokens") or 0)
                    if choices and isinstance(choices[0], dict):
                        content = str((choices[0].get("message") or {}).get("content") or "").strip()
                        if content:
                            cleaned_reply, proposals = _extract_seed_proposals(content)
                            if payload.propose_seeds and not proposals:
                                cleaned_reply += no_proposals_note
                            return CoordinatorChatResponse(
                                reply=cleaned_reply,
                                agent_handle=handle,
                                model=resolved_model,
                                input_tokens=in_tok,
                                output_tokens=out_tok,
                                proposed_seeds=proposals,
                                referenced_files=referenced_files,
                            )
    except Exception as exc:
        logger.debug("Direct upstream coordinator fallback failed: %s", exc)

    if is_brainstorm:
        fb_reply, fb_proposals = _build_fallback_brainstorm_proposals(
            payload.messages,
            focused_project,
            referenced_files,
            project_seeds,
            payload.propose_seeds,
            reason=failure_reason,
        )
        return CoordinatorChatResponse(
            reply=fb_reply,
            agent_handle=handle,
            model=resolved_model,
            proposed_seeds=fb_proposals,
            referenced_files=referenced_files,
        )

    return CoordinatorChatResponse(
        reply=fallback_reply,
        agent_handle=handle,
        model=resolved_model,
    )

