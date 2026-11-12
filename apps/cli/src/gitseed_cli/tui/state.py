from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal

from gitseed_common.enums import (
    RUN_ACTIVE_STATUSES,
    ApprovalDecision,
    SeedAutonomy,
    SeedStatus,
)
from gitseed_common.schemas import (
    AgentResponse,
    ApprovalResponse,
    BackendResponse,
    CreateApprovalRequest,
    CreateRunRequest,
    CreateSeedCommentRequest,
    CreateSeedRequest,
    ProjectResponse,
    RunResponse,
    SandboxSessionResponse,
    SeedChangesResponse,
    SeedCommentResponse,
    SeedResponse,
    SeedTaskResponse,
    StagePolicyResponse,
)

from gitseed_cli.client import CLIError, GitSeedClient

VIEWS: tuple[str, ...] = ("seeds", "projects", "runs", "backends")
INSPECTOR_TABS: tuple[str, ...] = ("overview", "tasks", "logs", "diff", "timeline")
AUTONOMY_CHOICES: tuple[tuple[str, str, str], ...] = (
    (
        SeedAutonomy.SUPERVISED,
        "Supervised (Recommended)",
        "Agents execute stages and pause at configured human approval gates.",
    ),
    (
        SeedAutonomy.AUTONOMOUS,
        "Autonomous",
        "Agents advance automatically through enabled stages until Merge.",
    ),
    (
        SeedAutonomy.MANUAL,
        "Manual",
        "Each stage run is triggered explicitly by an operator.",
    ),
)

_MENTION_RE = re.compile(r"(?:^|(?<=\s))@([a-z][a-z0-9-]{1,31})\b", re.IGNORECASE)

_SPECIAL_KEY_TOKENS: frozenset[str] = frozenset(
    {
        "UP",
        "DOWN",
        "LEFT",
        "RIGHT",
        "PGUP",
        "PGDN",
        "DELETE",
        "HOME",
        "END",
        "SHIFT_TAB",
        "Escape",
        "esc",
        "ESC",
        "Enter",
        "ENTER",
        "Backspace",
        "BACKSPACE",
        "TAB",
    }
)


def _default_indices() -> dict[str, int]:
    return {v: 0 for v in VIEWS}


def compute_list_viewport(
    total: int,
    selected_idx: int,
    max_rows: int = 20,
) -> tuple[int, int]:
    """Return (start_idx, end_idx) slice bounds keeping selected_idx visible within max_rows."""
    if total <= max_rows or max_rows <= 0:
        return 0, max(0, total)
    sel = max(0, min(selected_idx, total - 1))
    half = max_rows // 2
    start_idx = max(0, min(sel - half, total - max_rows))
    end_idx = min(total, start_idx + max_rows)
    return start_idx, end_idx


@dataclass
class SeedDetailCache:
    tasks: list[SeedTaskResponse] = field(default_factory=list)
    comments: list[SeedCommentResponse] = field(default_factory=list)
    approvals: list[ApprovalResponse] = field(default_factory=list)
    changes: SeedChangesResponse | None = None
    meta_loaded: bool = False
    changes_loaded: bool = False
    changes_fingerprint: tuple[str, str, str] | None = None
    has_ever_loaded_meta: bool = False


def _seed_fingerprint(seed: SeedResponse) -> tuple[str, str, str]:
    return (str(seed.stage), str(seed.status), str(seed.updated_at))


@dataclass
class ProjectDetailCache:
    policies: list[StagePolicyResponse] = field(default_factory=list)
    sandboxes: list[SandboxSessionResponse] = field(default_factory=list)
    loaded: bool = False


@dataclass
class TUIState:
    """Observable state container for the GitSeed Rich Terminal UI."""

    api_url: str = "http://localhost:8420"
    active_view: str = "seeds"
    inspector_tab: str = "overview"
    project_filter: str | None = None
    search_query: str = ""
    selected_indices: dict[str, int] = field(default_factory=_default_indices)
    list_scroll_offsets: dict[str, int] = field(default_factory=_default_indices)
    scroll_offset: int = 0
    diff_file_index: int = 0  # 0 = All Files, 1..N = changes.files[diff_file_index - 1]
    terminal_width: int = 140
    terminal_height: int = 36

    # Cached resources from GitSeedClient
    projects: list[ProjectResponse] = field(default_factory=list)
    seeds: list[SeedResponse] = field(default_factory=list)
    runs: list[RunResponse] = field(default_factory=list)
    sandboxes: list[SandboxSessionResponse] = field(default_factory=list)
    backends: list[BackendResponse] = field(default_factory=list)
    agents: list[AgentResponse] = field(default_factory=list)
    policies: list[StagePolicyResponse] = field(default_factory=list)

    # Selected seed inspector details
    selected_seed_id: str | None = None
    seed_tasks: list[SeedTaskResponse] = field(default_factory=list)
    seed_comments: list[SeedCommentResponse] = field(default_factory=list)
    seed_approvals: list[ApprovalResponse] = field(default_factory=list)
    seed_changes: SeedChangesResponse | None = None
    active_run_log: str = ""
    detail_loading: bool = False

    # Interactive prompt / modal / feedback state
    input_mode: str | None = None
    input_buffer: str = ""
    coordinator_reply: str | None = None
    status_message: str = "Ready. Click any item or press ? for keyboard shortcuts."
    error_message: str | None = None
    show_help: bool = False
    last_refresh_ok: bool = True

    # Guided New Seed Wizard state
    new_seed_project_id: str | None = None
    new_seed_project_idx: int = 0
    new_seed_autonomy: str = SeedAutonomy.SUPERVISED
    new_seed_autonomy_idx: int = 0

    @property
    def selected_index(self) -> int:
        return self.selected_indices.get(self.active_view, 0)

    @selected_index.setter
    def selected_index(self, value: int) -> None:
        self.selected_indices[self.active_view] = max(0, value)

    @property
    def project_slug_by_id(self) -> dict[str, str]:
        return {p.id: p.slug for p in self.projects}

    @property
    def agent_handle_by_id(self) -> dict[str, str]:
        return {a.id: f"@{a.handle}" for a in self.agents}

    @property
    def filtered_seeds(self) -> list[SeedResponse]:
        items = self.seeds
        if self.project_filter:
            target = self.project_filter.lower()
            slugs = self.project_slug_by_id
            items = [
                s
                for s in items
                if s.project_id.lower() == target
                or slugs.get(s.project_id, "").lower() == target
            ]
        if self.search_query.strip():
            q = self.search_query.strip().lower()
            slugs = self.project_slug_by_id
            items = [
                s
                for s in items
                if q in s.title.lower()
                or q in str(s.stage).lower()
                or q in str(s.status).lower()
                or q in f"{slugs.get(s.project_id, s.project_id)}#{s.number}".lower()
                or any(q in t.lower() for t in s.tags)
            ]
        return items

    @property
    def filtered_projects(self) -> list[ProjectResponse]:
        items = self.projects
        if self.search_query.strip():
            q = self.search_query.strip().lower()
            items = [
                p
                for p in items
                if q in p.slug.lower()
                or q in p.name.lower()
                or q in (p.description or "").lower()
            ]
        return items

    @property
    def filtered_runs(self) -> list[RunResponse]:
        items = self.runs
        if self.project_filter:
            seed_ids = {s.id for s in self.filtered_seeds}
            items = [r for r in items if r.seed_id in seed_ids]
        if self.search_query.strip():
            q = self.search_query.strip().lower()
            handles = self.agent_handle_by_id
            items = [
                r
                for r in items
                if q in r.id.lower()
                or q in r.seed_id.lower()
                or q in str(r.stage).lower()
                or q in str(r.status).lower()
                or q in handles.get(r.agent_id, r.agent_id).lower()
            ]
        return items

    @property
    def filtered_backends(self) -> list[BackendResponse]:
        items = self.backends
        if self.search_query.strip():
            q = self.search_query.strip().lower()
            items = [
                b
                for b in items
                if q in b.name.lower()
                or q in b.provider.lower()
                or q in str(b.type).lower()
                or q in str(b.health).lower()
            ]
        return items

    @property
    def selected_seed(self) -> SeedResponse | None:
        seeds = self.filtered_seeds
        if not seeds:
            return None
        idx = min(self.selected_indices.get("seeds", 0), len(seeds) - 1)
        return seeds[max(0, idx)]

    @property
    def selected_project(self) -> ProjectResponse | None:
        projects = self.filtered_projects
        if not projects:
            return None
        idx = min(self.selected_indices.get("projects", 0), len(projects) - 1)
        return projects[max(0, idx)]

    @property
    def selected_run(self) -> RunResponse | None:
        runs = self.filtered_runs
        if not runs:
            return None
        idx = min(self.selected_indices.get("runs", 0), len(runs) - 1)
        return runs[max(0, idx)]

    @property
    def selected_backend(self) -> BackendResponse | None:
        backends = self.filtered_backends
        if not backends:
            return None
        idx = min(self.selected_indices.get("backends", 0), len(backends) - 1)
        return backends[max(0, idx)]

    @property
    def total_spend(self) -> Decimal:
        total = Decimal("0")
        for s in self.filtered_seeds:
            try:
                total += Decimal(str(s.spent))
            except Exception:
                pass
        return total

    @property
    def awaiting_approval_count(self) -> int:
        return sum(
            1 for s in self.filtered_seeds if s.status == SeedStatus.AWAITING_APPROVAL
        )

    @property
    def active_runs_count(self) -> int:
        return sum(1 for r in self.filtered_runs if r.status in RUN_ACTIVE_STATUSES)


class TUIController:
    """State controller with instant in-memory switching, caching, and non-blocking prefetch."""

    def __init__(
        self,
        client: GitSeedClient,
        *,
        initial_project: str | None = None,
        initial_view: str = "seeds",
        async_mode: bool = False,
        debounce_seconds: float = 0.035,
    ) -> None:
        self.client = client
        self.async_mode = async_mode
        self.dirty = False
        self.lock = threading.RLock()
        self._debounce_seconds = max(0.0, debounce_seconds)
        self._seed_cache: dict[str, SeedDetailCache] = {}
        self._project_cache: dict[str, ProjectDetailCache] = {}
        self._run_logs_cache: dict[str, str] = {}
        self._refresh_in_flight = False

        self._detail_cond = threading.Condition()
        self._detail_request_seq = 0
        self._latest_requested_seed_id: str | None = None
        self._pending_detail_request: tuple[int, str | None, str | None, bool, float] | None = (
            None
        )
        self._detail_worker: threading.Thread | None = None
        self._deferred_sync_actions: list[Callable[[], None]] = []
        self._pending_bg_actions: list[Callable[[], None]] = []
        self._action_threads: list[threading.Thread] = []
        self._closed = False

        view = initial_view if initial_view in VIEWS else "seeds"
        self.state = TUIState(
            api_url=client.base_url,
            active_view=view,
            project_filter=initial_project,
        )

    def close(self) -> None:
        """Stop background detail worker cleanly on TUI shutdown."""
        with self._detail_cond:
            self._closed = True
            self._pending_detail_request = None
            self._detail_cond.notify_all()

    def invalidate_cache(
        self,
        seed_id: str | None = None,
        *,
        force_changes: bool = True,
    ) -> None:
        """Mark cached details for revalidation without clearing in-memory stale data."""
        with self.lock:
            if seed_id:
                entry = self._seed_cache.get(seed_id)
                if entry is not None:
                    entry.meta_loaded = False
                    if force_changes:
                        entry.changes_loaded = False
                for r in self.state.runs:
                    if r.seed_id == seed_id:
                        self._run_logs_cache.pop(r.id, None)
            else:
                for entry in self._seed_cache.values():
                    entry.meta_loaded = False
                    if force_changes:
                        entry.changes_loaded = False
                for p_entry in self._project_cache.values():
                    p_entry.loaded = False
                self._run_logs_cache.clear()

    def _clamp_selection(self) -> None:
        counts = {
            "seeds": len(self.state.filtered_seeds),
            "projects": len(self.state.filtered_projects),
            "runs": len(self.state.filtered_runs),
            "backends": len(self.state.filtered_backends),
        }
        max_rows = max(5, self.state.terminal_height - 16)
        for view, count in counts.items():
            cur = self.state.selected_indices.get(view, 0)
            if count <= 0:
                self.state.selected_indices[view] = 0
            elif cur >= count:
                self.state.selected_indices[view] = count - 1
            elif cur < 0:
                self.state.selected_indices[view] = 0
            start_idx, _ = compute_list_viewport(
                count, self.state.selected_indices[view], max_rows=max_rows
            )
            self.state.list_scroll_offsets[view] = start_idx

    def refresh_data(
        self,
        *,
        invalidate: bool = True,
        force_changes: bool = True,
    ) -> None:
        """Fetch latest list data and selected resource details from GitSeedClient."""
        try:
            if invalidate:
                self.invalidate_cache(force_changes=force_changes)

            with ThreadPoolExecutor(max_workers=5) as pool:
                f_proj = pool.submit(self.client.list_projects, include_archived=False)
                f_seeds = pool.submit(self.client.list_seeds, limit=100)
                f_runs = pool.submit(self.client.list_runs, limit=50)
                f_agents = pool.submit(self.client.list_agents, include_archived=False)
                f_backends = pool.submit(self.client.list_backends)

                projects = f_proj.result()
                seeds = f_seeds.result()
                runs = f_runs.result()
                agents = f_agents.result()
                try:
                    backends = f_backends.result()
                except CLIError:
                    backends = []

            with self.lock:
                self.state.projects = projects
                self.state.seeds = seeds
                self.state.runs = runs
                self.state.agents = agents
                self.state.backends = backends
                self._clamp_selection()
                self._sync_in_memory_selection()
                seed_obj = self.state.selected_seed
                seed_id = seed_obj.id if seed_obj else None
                target_proj_id = self._target_project_id()
                include_changes = self.state.inspector_tab == "diff"

            self._fetch_details_for(
                seed_id,
                target_proj_id,
                include_changes=include_changes,
            )
            with self.lock:
                self.state.last_refresh_ok = True
                self.state.error_message = None
                self.dirty = True
        except CLIError as exc:
            with self.lock:
                self.state.last_refresh_ok = False
                self.state.error_message = exc.message
                self.dirty = True

    def trigger_background_refresh(self) -> None:
        """Run periodic full refresh on a background daemon thread so UI never blocks."""
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True

        def _worker() -> None:
            try:
                self.refresh_data(invalidate=True, force_changes=False)
            finally:
                self._refresh_in_flight = False

        threading.Thread(target=_worker, daemon=True).start()

    def _unfetched_run_log_id(self, seed_id: str) -> str | None:
        """Return latest run ID for seed_id if live logs have not yet been fetched."""
        seed_runs = [r for r in self.state.runs if r.seed_id == seed_id]
        if not seed_runs:
            return None
        latest_run = seed_runs[0]
        log_tail = (latest_run.output or {}).get("log_tail")
        if isinstance(log_tail, str) and log_tail.strip():
            return None
        if latest_run.error:
            return None
        if latest_run.id in self._run_logs_cache:
            return None
        return latest_run.id

    def _sync_in_memory_selection(self) -> None:
        """Instantly update selected seed log and cached details in 0ms without network I/O."""
        with self.lock:
            seed = self.state.selected_seed
            if seed is None:
                self.state.selected_seed_id = None
                self.state.seed_tasks = []
                self.state.seed_comments = []
                self.state.seed_approvals = []
                self.state.seed_changes = None
                self.state.active_run_log = ""
                self.state.detail_loading = False
                return

            self.state.selected_seed_id = seed.id

            # Compute run log tail immediately from in-memory runs and cached live logs (zero I/O)
            seed_runs = [r for r in self.state.runs if r.seed_id == seed.id]
            if seed_runs:
                latest_run = seed_runs[0]
                log_tail = (latest_run.output or {}).get("log_tail")
                cached_live_log = self._run_logs_cache.get(latest_run.id, "")
                if isinstance(log_tail, str) and log_tail.strip():
                    self.state.active_run_log = log_tail
                elif latest_run.error:
                    self.state.active_run_log = f"ERROR: {latest_run.error}"
                elif cached_live_log:
                    self.state.active_run_log = cached_live_log
                else:
                    self.state.active_run_log = (
                        f"Run {latest_run.id} ({latest_run.stage}) · status: {latest_run.status}"
                    )
            else:
                self.state.active_run_log = "No stage runs recorded for this seed yet."

            cached = self._seed_cache.get(seed.id)
            if cached is not None:
                # Invalidate cached diff only if the seed's stage/status/updated_at changed
                current_fp = _seed_fingerprint(seed)
                if (
                    cached.changes_loaded
                    and cached.changes_fingerprint is not None
                    and cached.changes_fingerprint != current_fp
                ):
                    cached.changes_loaded = False

            if cached and cached.has_ever_loaded_meta:
                self.state.seed_tasks = cached.tasks
                self.state.seed_comments = cached.comments
                self.state.seed_approvals = cached.approvals
                self.state.seed_changes = cached.changes
                self.state.detail_loading = (
                    self.state.inspector_tab == "diff"
                    and cached.changes is None
                    and not cached.changes_loaded
                )
            else:
                self.state.seed_tasks = []
                self.state.seed_comments = []
                self.state.seed_approvals = []
                self.state.seed_changes = None
                self.state.detail_loading = True

    def _ensure_detail_worker_started_locked(self) -> None:
        if self._detail_worker is not None and self._detail_worker.is_alive():
            return
        self._detail_worker = threading.Thread(
            target=self._detail_worker_loop,
            name="gitseed-tui-detail-worker",
            daemon=True,
        )
        self._detail_worker.start()

    def _detail_worker_loop(self) -> None:
        while True:
            with self._detail_cond:
                while not self._closed and self._pending_detail_request is None:
                    self._detail_cond.wait()
                if self._closed:
                    return
                assert self._pending_detail_request is not None
                seq, seed_id, proj_id, include_changes, due_at = self._pending_detail_request
                remaining = due_at - time.monotonic()
                if remaining > 0:
                    self._detail_cond.wait(timeout=remaining)
                    continue
                self._pending_detail_request = None

            self._fetch_details_for(
                seed_id,
                proj_id,
                include_changes=include_changes,
                request_seq=seq,
            )
            with self.lock:
                self.dirty = True

    def _enqueue_debounced_detail_fetch(
        self,
        seed_id: str | None,
        proj_id: str | None,
        *,
        include_changes: bool,
    ) -> None:
        with self._detail_cond:
            if self._closed:
                return
            self._detail_request_seq += 1
            self._latest_requested_seed_id = seed_id
            due_at = time.monotonic() + self._debounce_seconds
            self._pending_detail_request = (
                self._detail_request_seq,
                seed_id,
                proj_id,
                include_changes,
                due_at,
            )
            self._ensure_detail_worker_started_locked()
            self._detail_cond.notify()

    def _on_selection_or_tab_changed(self) -> None:
        """Update in-memory selection state immediately, then fetch uncached details."""
        with self.lock:
            self._sync_in_memory_selection()
            need_changes = self.state.inspector_tab == "diff"

            seed = self.state.selected_seed
            target_proj_id = self._target_project_id()
            seed_cached = self._seed_cache.get(seed.id) if seed else None
            proj_cached = self._project_cache.get(target_proj_id) if target_proj_id else None
            need_run_log = seed is not None and self._unfetched_run_log_id(seed.id) is not None

            needs_seed_fetch = seed is not None and (
                seed_cached is None
                or not seed_cached.meta_loaded
                or (need_changes and not seed_cached.changes_loaded)
                or need_run_log
            )
            needs_proj_fetch = target_proj_id is not None and (
                proj_cached is None or not proj_cached.loaded
            )

            if not needs_seed_fetch and not needs_proj_fetch:
                if proj_cached is not None:
                    self.state.policies = proj_cached.policies
                    self.state.sandboxes = proj_cached.sandboxes
                self.state.detail_loading = False
                return

            expected_seed_id = seed.id if seed else None
            expected_proj_id = target_proj_id

        if not self.async_mode:
            with self.lock:
                self._deferred_sync_actions.append(
                    lambda: self._fetch_details_for(
                        expected_seed_id,
                        expected_proj_id,
                        include_changes=need_changes,
                    )
                )
            return

        # In interactive TUI mode, schedule debounced background fetch (0ms keypress latency)
        self._enqueue_debounced_detail_fetch(
            expected_seed_id,
            expected_proj_id,
            include_changes=need_changes,
        )

    def _target_project_id(self) -> str | None:
        seed = self.state.selected_seed
        if self.state.active_view == "projects" and self.state.selected_project:
            return self.state.selected_project.id
        if seed is not None:
            return seed.project_id
        if self.state.project_filter:
            resolved = self._resolve_project_by_ref(self.state.project_filter)
            return resolved.id if resolved else self.state.project_filter
        if self.state.projects:
            return self.state.projects[0].id
        return None

    def _fetch_context_details_sync(self, *, include_changes: bool = True) -> None:
        with self.lock:
            seed = self.state.selected_seed
            seed_id = seed.id if seed else None
            proj_id = self._target_project_id()
        self._fetch_details_for(
            seed_id,
            proj_id,
            include_changes=include_changes,
        )

    def _fetch_details_for(
        self,
        seed_id: str | None,
        target_proj_id: str | None,
        *,
        include_changes: bool = False,
        request_seq: int | None = None,
    ) -> None:
        if seed_id is not None:
            with self.lock:
                existing = self._seed_cache.get(seed_id)
                meta_loaded = existing.meta_loaded if existing else False
                changes_loaded = existing.changes_loaded if existing else False
                has_ever_loaded = existing.has_ever_loaded_meta if existing else False
                prev_tasks = list(existing.tasks) if existing else []
                prev_comments = list(existing.comments) if existing else []
                prev_approvals = list(existing.approvals) if existing else []
                prev_changes = existing.changes if existing else None
                prev_fp = existing.changes_fingerprint if existing else None
                seed_obj = next((s for s in self.state.seeds if s.id == seed_id), None)
                run_log_id = self._unfetched_run_log_id(seed_id)

            fetched_tasks = prev_tasks
            fetched_comments = prev_comments
            fetched_approvals = prev_approvals
            fetched_changes = prev_changes
            fetched_fp = prev_fp
            did_load_changes = changes_loaded
            fetched_log_text: str | None = None

            with ThreadPoolExecutor(max_workers=5) as pool:
                f_tasks = (
                    pool.submit(self.client.list_seed_tasks, seed_id)
                    if not meta_loaded
                    else None
                )
                f_comments = (
                    pool.submit(self.client.list_seed_comments, seed_id)
                    if not meta_loaded
                    else None
                )
                f_approvals = (
                    pool.submit(self.client.list_seed_approvals, seed_id)
                    if not meta_loaded
                    else None
                )
                f_changes = (
                    pool.submit(self.client.get_seed_changes, seed_id)
                    if (include_changes and not changes_loaded)
                    else None
                )
                f_run_log = (
                    pool.submit(self.client.get_run_logs, run_log_id, tail=200)
                    if run_log_id is not None
                    else None
                )

                if f_tasks is not None:
                    try:
                        fetched_tasks = f_tasks.result()
                    except CLIError:
                        if not has_ever_loaded:
                            fetched_tasks = []
                if f_comments is not None:
                    try:
                        fetched_comments = f_comments.result()
                    except CLIError:
                        if not has_ever_loaded:
                            fetched_comments = []
                if f_approvals is not None:
                    try:
                        fetched_approvals = f_approvals.result()
                    except CLIError:
                        if not has_ever_loaded:
                            fetched_approvals = []
                if f_changes is not None:
                    try:
                        fetched_changes = f_changes.result()
                    except CLIError:
                        if fetched_changes is None:
                            fetched_changes = None
                    did_load_changes = True
                    if seed_obj is not None:
                        fetched_fp = _seed_fingerprint(seed_obj)
                if f_run_log is not None:
                    try:
                        log_resp = f_run_log.result()
                        if isinstance(log_resp.log_tail, str) and log_resp.log_tail.strip():
                            fetched_log_text = log_resp.log_tail
                        else:
                            fetched_log_text = ""
                    except Exception:
                        fetched_log_text = ""

            with self.lock:
                entry = self._seed_cache.get(seed_id) or SeedDetailCache()
                entry.tasks = fetched_tasks
                entry.comments = fetched_comments
                entry.approvals = fetched_approvals
                if did_load_changes:
                    entry.changes = fetched_changes
                    entry.changes_loaded = True
                    entry.changes_fingerprint = fetched_fp
                entry.meta_loaded = True
                entry.has_ever_loaded_meta = True
                self._seed_cache[seed_id] = entry

                if run_log_id is not None and fetched_log_text is not None:
                    self._run_logs_cache[run_log_id] = fetched_log_text

                is_stale_seq = False
                if request_seq is not None:
                    with self._detail_cond:
                        is_stale_seq = (
                            request_seq != self._detail_request_seq
                            or self._latest_requested_seed_id != seed_id
                        )

                if not is_stale_seq and self.state.selected_seed_id == seed_id:
                    self.state.seed_tasks = entry.tasks
                    self.state.seed_comments = entry.comments
                    self.state.seed_approvals = entry.approvals
                    self.state.seed_changes = entry.changes
                    if (
                        run_log_id is not None
                        and fetched_log_text
                        and any(
                            r.id == run_log_id and r.seed_id == seed_id
                            for r in self.state.runs[:1] + [
                                r for r in self.state.runs if r.seed_id == seed_id
                            ][:1]
                        )
                    ):
                        self.state.active_run_log = fetched_log_text
                    if (
                        entry.changes is not None
                        and entry.changes.files
                        and self.state.diff_file_index > len(entry.changes.files)
                    ):
                        self.state.diff_file_index = 0
                    self.state.detail_loading = False

        if target_proj_id:
            with self.lock:
                p_cached = self._project_cache.get(target_proj_id)
                proj_already_loaded = p_cached is not None and p_cached.loaded

            if not proj_already_loaded:
                p_entry = ProjectDetailCache()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    f_pol = pool.submit(self.client.list_project_policies, target_proj_id)
                    f_sbx = pool.submit(self.client.list_sandboxes, target_proj_id, limit=10)
                    try:
                        p_entry.policies = f_pol.result()
                    except CLIError:
                        try:
                            p_entry.policies = self.client.list_org_policies()
                        except CLIError:
                            p_entry.policies = []
                    try:
                        p_entry.sandboxes = f_sbx.result()
                    except CLIError:
                        p_entry.sandboxes = []
                p_entry.loaded = True
                with self.lock:
                    self._project_cache[target_proj_id] = p_entry

            with self.lock:
                p_final = self._project_cache.get(target_proj_id)
                if p_final is not None and self._target_project_id() == target_proj_id:
                    self.state.policies = p_final.policies
                    self.state.sandboxes = p_final.sandboxes
        else:
            try:
                org_pols = self.client.list_org_policies()
            except CLIError:
                org_pols = []
            with self.lock:
                if self._target_project_id() is None:
                    self.state.policies = org_pols

    def _current_view_len(self) -> int:
        if self.state.active_view == "seeds":
            return len(self.state.filtered_seeds)
        if self.state.active_view == "projects":
            return len(self.state.filtered_projects)
        if self.state.active_view == "runs":
            return len(self.state.filtered_runs)
        if self.state.active_view == "backends":
            return len(self.state.filtered_backends)
        return 0

    def _resolve_stage_agent_handle(self, project_id: str, stage: str) -> str:
        pol = next((p for p in self.state.policies if str(p.stage) == str(stage)), None)
        if pol and pol.agent_id:
            for ag in self.state.agents:
                if ag.id == pol.agent_id:
                    return ag.handle
        stage_defaults = {
            "ideate": "ideator",
            "plan": "planner",
            "execute": "coder",
            "review": "reviewer",
            "merge": "merger",
        }
        return stage_defaults.get(str(stage), "agent")

    def _resolve_project_by_ref(self, ref: str | None) -> ProjectResponse | None:
        if not ref:
            return None
        target = ref.strip().lower()
        for p in self.state.projects:
            if p.id.lower() == target or p.slug.lower() == target:
                return p
        return None

    def start_new_seed_flow(self) -> None:
        """Start guided New Seed flow: Project (if unscoped) -> Autonomy -> Quick Prompt."""
        if not self.state.projects:
            self.state.error_message = "No projects exist yet. Create a project first."
            return

        self.state.error_message = None
        self.state.input_buffer = ""
        self.state.new_seed_autonomy_idx = 0
        self.state.new_seed_autonomy = AUTONOMY_CHOICES[0][0]

        scoped_proj = self._resolve_project_by_ref(self.state.project_filter)
        if scoped_proj is not None:
            self.state.new_seed_project_id = scoped_proj.id
            self.state.input_mode = "new_seed_autonomy"
            self.state.status_message = (
                f"New Seed in '{scoped_proj.slug}' · Step 1/2: Choose autonomy mode."
            )
        else:
            default_idx = 0
            default_proj = self._resolve_project_by_ref(self.client.default_project)
            if default_proj is not None:
                for i, p in enumerate(self.state.projects):
                    if p.id == default_proj.id:
                        default_idx = i
                        break
            self.state.new_seed_project_idx = default_idx
            self.state.new_seed_project_id = self.state.projects[default_idx].id
            self.state.input_mode = "new_seed_project"
            self.state.status_message = (
                "New Seed · Step 1/3: Select target project "
                "(↑/↓ or 1-9 or click, Enter to confirm)."
            )

    def _flush_deferred_sync_actions(self) -> None:
        """Execute queued synchronous detail fetches outside self.lock."""
        while True:
            with self.lock:
                if not self._deferred_sync_actions:
                    return
                actions = list(self._deferred_sync_actions)
                self._deferred_sync_actions.clear()
            for fn in actions:
                fn()

    def _enqueue_network_action(self, fn: Callable[[], None]) -> None:
        with self.lock:
            self._pending_bg_actions.append(fn)

    def _dispatch_pending_network_actions(self) -> None:
        with self.lock:
            if not self._pending_bg_actions:
                return
            actions = list(self._pending_bg_actions)
            self._pending_bg_actions.clear()
            run_in_bg = self.async_mode

        for fn in actions:
            def _run_action(callback: Callable[[], None] = fn) -> None:
                try:
                    callback()
                finally:
                    self._flush_deferred_sync_actions()
                    with self.lock:
                        self.dirty = True

            if run_in_bg:
                worker = threading.Thread(
                    target=_run_action,
                    name="gitseed-tui-action",
                    daemon=True,
                )
                with self.lock:
                    self._action_threads = [t for t in self._action_threads if t.is_alive()]
                    self._action_threads.append(worker)
                worker.start()
            else:
                _run_action()

    def wait_for_actions(self, timeout: float = 2.0) -> None:
        """Wait for in-flight background action threads (useful for async TUI tests)."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self.lock:
                alive = [t for t in self._action_threads if t.is_alive()]
                self._action_threads = alive
            if not alive:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            alive[0].join(timeout=min(0.05, remaining))

    def refresh_all(self) -> None:
        """Refresh workspace data outside self.lock and update status message."""
        self.refresh_data(invalidate=True)
        with self.lock:
            if self.state.last_refresh_ok:
                self.state.status_message = "Refreshed workspace data from Control Plane."
            self.dirty = True

    def handle_key(self, key: str) -> bool:
        """Process a single keystroke, special key token, or mouse event. Return False to exit."""
        with self.lock:
            keep_going = self._handle_key_unlocked(key)
        self._flush_deferred_sync_actions()
        self._dispatch_pending_network_actions()
        return keep_going

    def _handle_key_unlocked(self, key: str) -> bool:
        if not key:
            return True

        # Mouse events encoded as MOUSE_<KIND>:<col>:<row>
        if key.startswith("MOUSE_"):
            return self._handle_mouse_token(key)

        # Handle active modal/wizard input mode first
        if self.state.input_mode is not None:
            return self._handle_input_mode_key(key)

        # Help overlay toggle: consume ALL keys while Help is open so hidden actions cannot fire
        if self.state.show_help:
            if key == "\x03":
                return False
            if key in {"?", "Escape", "esc", "q", "Enter", "\r", "\n"}:
                self.state.show_help = False
            return True

        # Quit
        if key in {"q", "Q", "\x03"}:
            return False

        # Help
        if key == "?":
            self.state.show_help = not self.state.show_help
            return True

        # Top-level view switching (1-4)
        if key == "1":
            self.state.active_view = "seeds"
            self.state.scroll_offset = 0
            self._on_selection_or_tab_changed()
            self.state.status_message = "Switched to [1] Seeds view."
            return True
        if key == "2":
            self.state.active_view = "projects"
            self.state.scroll_offset = 0
            self._on_selection_or_tab_changed()
            self.state.status_message = "Switched to [2] Projects view."
            return True
        if key == "3":
            self.state.active_view = "runs"
            self.state.scroll_offset = 0
            self.state.status_message = "Switched to [3] Runs & Sandboxes view."
            return True
        if key == "4":
            self.state.active_view = "backends"
            self.state.scroll_offset = 0
            self.state.status_message = "Switched to [4] Backends & Agents view."
            return True

        # List selection navigation (j/k, Up/Down, g/G)
        if key in {"j", "DOWN"}:
            count = self._current_view_len()
            if count > 0:
                new_idx = min(count - 1, self.state.selected_index + 1)
                if new_idx != self.state.selected_index:
                    self.state.selected_index = new_idx
                    max_rows = max(5, self.state.terminal_height - 16)
                    start_idx, _ = compute_list_viewport(count, new_idx, max_rows=max_rows)
                    self.state.list_scroll_offsets[self.state.active_view] = start_idx
                    self.state.scroll_offset = 0
                    self.state.diff_file_index = 0
                    self._on_selection_or_tab_changed()
            return True
        if key in {"k", "UP"}:
            count = self._current_view_len()
            if count > 0:
                new_idx = max(0, self.state.selected_index - 1)
                if new_idx != self.state.selected_index:
                    self.state.selected_index = new_idx
                    max_rows = max(5, self.state.terminal_height - 16)
                    start_idx, _ = compute_list_viewport(count, new_idx, max_rows=max_rows)
                    self.state.list_scroll_offsets[self.state.active_view] = start_idx
                    self.state.scroll_offset = 0
                    self.state.diff_file_index = 0
                    self._on_selection_or_tab_changed()
            return True
        if key in {"g", "HOME"}:
            if self.state.selected_index != 0:
                self.state.selected_index = 0
                self.state.list_scroll_offsets[self.state.active_view] = 0
                self.state.scroll_offset = 0
                self.state.diff_file_index = 0
                self._on_selection_or_tab_changed()
            return True
        if key in {"G", "END"}:
            count = self._current_view_len()
            if count > 0 and self.state.selected_index != count - 1:
                self.state.selected_index = count - 1
                max_rows = max(5, self.state.terminal_height - 16)
                start_idx, _ = compute_list_viewport(count, count - 1, max_rows=max_rows)
                self.state.list_scroll_offsets[self.state.active_view] = start_idx
                self.state.scroll_offset = 0
                self.state.diff_file_index = 0
                self._on_selection_or_tab_changed()
            return True

        # File picker navigation inside the Git Diff inspector tab
        if (
            self.state.active_view == "seeds"
            and self.state.inspector_tab == "diff"
            and key in {"f", "F", "LEFT", "RIGHT", "<", ">", "0"}
        ):
            self._handle_diff_file_picker_key(key)
            return True

        # Inspector pane scrolling
        if key == "J":
            self._scroll_inspector(4)
            return True
        if key == "U":
            self._scroll_inspector(-4)
            return True
        if key == "PGDN":
            self._scroll_inspector(12)
            return True
        if key == "PGUP":
            self._scroll_inspector(-12)
            return True

        # Seed Inspector sub-tab switching
        if key in {"\t", "TAB", "]"}:
            idx = INSPECTOR_TABS.index(self.state.inspector_tab)
            self.state.inspector_tab = INSPECTOR_TABS[(idx + 1) % len(INSPECTOR_TABS)]
            self.state.scroll_offset = 0
            self._on_selection_or_tab_changed()
            return True
        if key in {"[", "SHIFT_TAB"}:
            idx = INSPECTOR_TABS.index(self.state.inspector_tab)
            self.state.inspector_tab = INSPECTOR_TABS[(idx - 1) % len(INSPECTOR_TABS)]
            self.state.scroll_offset = 0
            self._on_selection_or_tab_changed()
            return True
        if key == "o":
            self.state.inspector_tab = "overview"
            self.state.scroll_offset = 0
            self._on_selection_or_tab_changed()
            return True
        if key == "t":
            self.state.inspector_tab = "tasks"
            self.state.scroll_offset = 0
            self._on_selection_or_tab_changed()
            return True
        if key == "l":
            self.state.inspector_tab = "logs"
            self.state.scroll_offset = 0
            self._on_selection_or_tab_changed()
            return True
        if key == "d":
            self.state.inspector_tab = "diff"
            self.state.scroll_offset = 0
            self._on_selection_or_tab_changed()
            return True
        if key == "m":
            self.state.inspector_tab = "timeline"
            self.state.scroll_offset = 0
            self._on_selection_or_tab_changed()
            return True

        # Project filter cycling
        if key == "p":
            self._cycle_project_filter()
            return True

        # Search / filter prompt
        if key == "/":
            self.state.input_mode = "filter"
            self.state.input_buffer = self.state.search_query
            return True

        # Manual refresh
        if key == "R":
            self.state.status_message = "Refreshing workspace data from Control Plane..."
            self.state.error_message = None
            self.dirty = True
            self._enqueue_network_action(self.refresh_all)
            return True

        # Operator workflow actions on the selected seed
        if key == "a":
            if self.state.selected_seed is None:
                self.state.error_message = "No seed selected to approve."
                return True
            self.state.input_mode = "approve"
            self.state.input_buffer = ""
            self.state.error_message = None
            return True

        if key == "x":
            if self.state.selected_seed is None:
                self.state.error_message = "No seed selected to request changes on."
                return True
            self.state.input_mode = "request_changes"
            self.state.input_buffer = ""
            self.state.error_message = None
            return True

        if key == "X":
            if self.state.selected_seed is None:
                self.state.error_message = "No seed selected to reject."
                return True
            self.state.input_mode = "reject"
            self.state.input_buffer = ""
            self.state.error_message = None
            return True

        if key == "c":
            if self.state.selected_seed is None:
                self.state.error_message = "No seed selected to steer."
                return True
            self.state.input_mode = "steer"
            self.state.input_buffer = ""
            self.state.error_message = None
            return True

        if key == "C":
            if self.state.selected_seed is None:
                self.state.error_message = "No seed selected to comment on."
                return True
            self.state.input_mode = "comment"
            self.state.input_buffer = ""
            self.state.error_message = None
            return True

        if key == "n":
            self.start_new_seed_flow()
            return True

        if key == "K":
            self.state.input_mode = "ask_coordinator"
            self.state.input_buffer = ""
            self.state.error_message = None
            return True

        if key == "r":
            self._action_retry_seed()
            return True

        if key == "z":
            self._action_cancel_run()
            return True

        return True

    def _max_inspector_scroll_offset(self) -> int:
        """Compute upper bound for scroll_offset in the active inspector tab so diff scrolling never overshoots."""
        if self.state.inspector_tab == "diff":
            changes = self.state.seed_changes
            if changes is None or not changes.files:
                return 0
            idx = self.state.diff_file_index
            if idx <= 0 or idx > len(changes.files):
                total_lines = 0
                for f in changes.files:
                    total_lines += 2 + len((f.patch or "").splitlines())
            else:
                chosen = changes.files[idx - 1]
                total_lines = max(1, len((chosen.patch or "").splitlines()))
            return max(0, total_lines - 1)
        return 10000

    def _scroll_inspector(self, delta: int) -> None:
        upper = self._max_inspector_scroll_offset()
        self.state.scroll_offset = max(0, min(upper, self.state.scroll_offset + delta))

    def _handle_diff_file_picker_key(self, key: str) -> None:
        changes = self.state.seed_changes
        files = changes.files if changes and changes.files else []
        total_choices = len(files) + 1  # 0 = All Files, 1..len(files) = single file
        if key == "0" or total_choices <= 1:
            self.state.diff_file_index = 0
            self.state.scroll_offset = 0
            self.state.status_message = "Diff: showing all changed files."
            return
        if key in {"f", "RIGHT", ">"}:
            self.state.diff_file_index = (self.state.diff_file_index + 1) % total_choices
        elif key in {"F", "LEFT", "<"}:
            self.state.diff_file_index = (self.state.diff_file_index - 1) % total_choices
        self.state.scroll_offset = 0
        if self.state.diff_file_index == 0:
            self.state.status_message = f"Diff: showing all {len(files)} changed files."
        else:
            f = files[self.state.diff_file_index - 1]
            self.state.status_message = (
                f"Diff ({self.state.diff_file_index}/{len(files)}): {f.path}"
            )

    def _cycle_project_filter(self) -> None:
        slugs = [p.slug for p in self.state.projects]
        if not slugs:
            self.state.project_filter = None
        elif self.state.project_filter is None:
            self.state.project_filter = slugs[0]
        else:
            try:
                cur_idx = slugs.index(self.state.project_filter)
                if cur_idx + 1 < len(slugs):
                    self.state.project_filter = slugs[cur_idx + 1]
                else:
                    self.state.project_filter = None
            except ValueError:
                self.state.project_filter = None
        self._clamp_selection()
        self._on_selection_or_tab_changed()
        label = self.state.project_filter or "all projects"
        self.state.status_message = f"Project filter: {label}"

    def _handle_mouse_token(self, token: str) -> bool:
        """Handle MOUSE_CLICK:col:row, MOUSE_WHEEL_UP:col:row, MOUSE_WHEEL_DOWN:col:row."""
        parts = token.split(":")
        if len(parts) != 3:
            return True
        kind = parts[0]
        try:
            col = int(parts[1])
            row = int(parts[2])
        except ValueError:
            return True

        width = max(80, self.state.terminal_width)
        height = max(24, self.state.terminal_height)
        split_col = (
            (width * 5) // 12
            if self.state.active_view in {"seeds", "projects"}
            else width // 2
        )

        if kind == "MOUSE_WHEEL_UP":
            if self.state.show_help:
                return True
            if self.state.input_mode == "new_seed_project":
                self._handle_input_mode_key("UP")
            elif self.state.input_mode == "new_seed_autonomy":
                self._handle_input_mode_key("UP")
            elif col < split_col:
                self._handle_key_unlocked("UP")
            else:
                self._scroll_inspector(-3)
            return True

        if kind == "MOUSE_WHEEL_DOWN":
            if self.state.show_help:
                return True
            if self.state.input_mode == "new_seed_project":
                self._handle_input_mode_key("DOWN")
            elif self.state.input_mode == "new_seed_autonomy":
                self._handle_input_mode_key("DOWN")
            elif col < split_col:
                self._handle_key_unlocked("DOWN")
            else:
                self._scroll_inspector(3)
            return True

        if kind != "MOUSE_CLICK":
            return True

        if self.state.show_help:
            self.state.show_help = False
            return True

        # Header Row 2: Project filter click
        if row == 2 and 35 <= col <= 65:
            self._cycle_project_filter()
            return True

        # Header Row 3: View navigation tabs
        if row in {3, 4}:
            if col < 18:
                return self._handle_key_unlocked("1")
            if col < 35:
                return self._handle_key_unlocked("2")
            if col < 60:
                return self._handle_key_unlocked("3")
            if col < 90:
                return self._handle_key_unlocked("4")

        # Interactive New Seed Wizard click handling in body pane
        if self.state.input_mode == "new_seed_project":
            clicked_idx = row - 10
            if 0 <= clicked_idx < len(self.state.projects):
                self.state.new_seed_project_idx = clicked_idx
                self.state.new_seed_project_id = self.state.projects[clicked_idx].id
                return self._handle_input_mode_key("Enter")
            return True

        if self.state.input_mode == "new_seed_autonomy":
            clicked_idx = row - 10
            if 0 <= clicked_idx < len(AUTONOMY_CHOICES):
                self.state.new_seed_autonomy_idx = clicked_idx
                self.state.new_seed_autonomy = AUTONOMY_CHOICES[clicked_idx][0]
                return self._handle_input_mode_key("Enter")
            return True

        # Footer Bar Clicks (bottom 3 rows)
        if row >= height - 3 and self.state.input_mode is None:
            footer_actions = [
                (0, 12, "j"),
                (12, 23, "1"),
                (23, 33, "TAB"),
                (33, 45, "a"),
                (45, 57, "x"),
                (57, 68, "c"),
                (68, 78, "r"),
                (78, 89, "z"),
                (89, 98, "n"),
                (98, 107, "p"),
                (107, 118, "/"),
                (118, 129, "K"),
                (129, 138, "?"),
                (138, 160, "q"),
            ]
            for c_start, c_end, act_key in footer_actions:
                if c_start <= col < c_end:
                    return self._handle_key_unlocked(act_key)
            return True

        # Main Body Area (rows 5 .. height - 4)
        if 5 <= row < height - 3:
            if col < split_col:
                count = self._current_view_len()
                max_rows = max(5, self.state.terminal_height - 16)
                default_offset, _ = compute_list_viewport(
                    count, self.state.selected_index, max_rows=max_rows
                )
                offset = self.state.list_scroll_offsets.get(
                    self.state.active_view, default_offset
                )
                row_idx = offset + (row - 8)
                if 0 <= row_idx < count and row_idx != self.state.selected_index:
                    self.state.selected_index = row_idx
                    new_offset, _ = compute_list_viewport(count, row_idx, max_rows=max_rows)
                    self.state.list_scroll_offsets[self.state.active_view] = new_offset
                    self.state.scroll_offset = 0
                    self.state.diff_file_index = 0
                    self._on_selection_or_tab_changed()
                return True
            else:
                if self.state.active_view == "seeds" and row in {8, 9, 10}:
                    rel_col = col - split_col
                    if rel_col < 22:
                        self.state.inspector_tab = "overview"
                    elif rel_col < 38:
                        self.state.inspector_tab = "tasks"
                    elif rel_col < 54:
                        self.state.inspector_tab = "logs"
                    elif rel_col < 69:
                        self.state.inspector_tab = "diff"
                    else:
                        self.state.inspector_tab = "timeline"
                    self.state.scroll_offset = 0
                    self._on_selection_or_tab_changed()
                    return True
                # Click on file picker rows inside the Git Diff inspector tab
                if (
                    self.state.active_view == "seeds"
                    and self.state.inspector_tab == "diff"
                    and self.state.seed_changes is not None
                    and self.state.seed_changes.files
                ):
                    files = self.state.seed_changes.files
                    # File selector rows start right below the summary & hint lines (around row 14)
                    clicked_choice = row - 14
                    if 0 <= clicked_choice <= min(len(files), 6):
                        self.state.diff_file_index = clicked_choice
                        self.state.scroll_offset = 0
                        return True
                return True

        return True

    def _handle_input_mode_key(self, key: str) -> bool:
        mode = self.state.input_mode

        if key in {"Escape", "esc", "\x1b"}:
            self.state.input_mode = None
            self.state.input_buffer = ""
            self.state.status_message = "Cancelled."
            return True

        # Step 1 of New Seed Wizard: Select Project
        if mode == "new_seed_project":
            proj_count = len(self.state.projects)
            if proj_count == 0:
                self.state.input_mode = None
                return True
            if key in {"j", "DOWN"}:
                self.state.new_seed_project_idx = (self.state.new_seed_project_idx + 1) % proj_count
                self.state.new_seed_project_id = self.state.projects[
                    self.state.new_seed_project_idx
                ].id
                return True
            if key in {"k", "UP"}:
                self.state.new_seed_project_idx = (self.state.new_seed_project_idx - 1) % proj_count
                self.state.new_seed_project_id = self.state.projects[
                    self.state.new_seed_project_idx
                ].id
                return True
            if key == "HOME":
                self.state.new_seed_project_idx = 0
                self.state.new_seed_project_id = self.state.projects[0].id
                return True
            if key == "END":
                self.state.new_seed_project_idx = proj_count - 1
                self.state.new_seed_project_id = self.state.projects[proj_count - 1].id
                return True
            if len(key) == 1 and key.isdigit() and key != "0":
                idx = int(key) - 1
                if 0 <= idx < proj_count:
                    self.state.new_seed_project_idx = idx
                    self.state.new_seed_project_id = self.state.projects[idx].id
                    key = "Enter"
                else:
                    return True
            if key in {"Enter", "\r", "\n"}:
                chosen = self.state.projects[self.state.new_seed_project_idx]
                self.state.new_seed_project_id = chosen.id
                self.state.input_mode = "new_seed_autonomy"
                self.state.status_message = (
                    f"Project '{chosen.slug}' selected · Step 2/3: Choose autonomy mode."
                )
            return True

        # Step 2 of New Seed Wizard: Choose Autonomy Mode
        if mode == "new_seed_autonomy":
            mode_count = len(AUTONOMY_CHOICES)
            if key in {"j", "DOWN"}:
                self.state.new_seed_autonomy_idx = (
                    self.state.new_seed_autonomy_idx + 1
                ) % mode_count
                self.state.new_seed_autonomy = AUTONOMY_CHOICES[
                    self.state.new_seed_autonomy_idx
                ][0]
                return True
            if key in {"k", "UP"}:
                self.state.new_seed_autonomy_idx = (
                    self.state.new_seed_autonomy_idx - 1
                ) % mode_count
                self.state.new_seed_autonomy = AUTONOMY_CHOICES[
                    self.state.new_seed_autonomy_idx
                ][0]
                return True
            if key == "HOME":
                self.state.new_seed_autonomy_idx = 0
                self.state.new_seed_autonomy = AUTONOMY_CHOICES[0][0]
                return True
            if key == "END":
                self.state.new_seed_autonomy_idx = mode_count - 1
                self.state.new_seed_autonomy = AUTONOMY_CHOICES[mode_count - 1][0]
                return True
            if key in {"1", "2", "3"}:
                idx = int(key) - 1
                self.state.new_seed_autonomy_idx = idx
                self.state.new_seed_autonomy = AUTONOMY_CHOICES[idx][0]
                key = "Enter"
            if key in {"Enter", "\r", "\n"}:
                self.state.new_seed_autonomy = AUTONOMY_CHOICES[
                    self.state.new_seed_autonomy_idx
                ][0]
                self.state.input_mode = "new_seed_prompt"
                self.state.input_buffer = ""
                self.state.status_message = (
                    f"Autonomy '{self.state.new_seed_autonomy}' selected · "
                    "Enter quick prompt / seed requirement."
                )
            return True

        if key in {"Enter", "ENTER", "\r", "\n"}:
            text = self.state.input_buffer.strip()
            self.state.input_mode = None
            self.state.input_buffer = ""
            self._submit_input(mode or "", text)
            return True

        if key in {"Backspace", "BACKSPACE", "\x08", "\x7f"}:
            self.state.input_buffer = self.state.input_buffer[:-1]
            return True

        if key in {"DELETE", "HOME", "END", "SHIFT_TAB"}:
            return True

        if key not in _SPECIAL_KEY_TOKENS and not key.startswith(("MOUSE_", "MOUSE:")):
            if len(key) == 1 and key.isprintable():
                self.state.input_buffer += key
            elif len(key) > 1:
                printable_chars = "".join(
                    ch for ch in key if ch.isprintable() and ch not in {"\r", "\n", "\x1b"}
                )
                if printable_chars:
                    self.state.input_buffer += printable_chars
        return True

    _handle_input_mode = _handle_input_mode_key

    def _submit_approval(
        self,
        seed_id: str,
        seed_number: int,
        decision: ApprovalDecision,
        comment: str | None,
    ) -> None:
        try:
            approval = self.client.create_seed_approval(
                seed_id,
                CreateApprovalRequest(decision=decision, comment=comment),
            )
            self.refresh_data(invalidate=True)
            with self.lock:
                if decision == ApprovalDecision.APPROVED:
                    self.state.status_message = (
                        f"Approved stage {approval.stage} on seed #{seed_number}."
                    )
                elif decision == ApprovalDecision.CHANGES_REQUESTED:
                    self.state.status_message = (
                        f"Requested changes on stage {approval.stage} for seed #{seed_number}."
                    )
                else:
                    self.state.status_message = (
                        f"Rejected seed #{seed_number} at stage {approval.stage}."
                    )
                self.dirty = True
        except CLIError as exc:
            with self.lock:
                self.state.error_message = exc.message
                self.dirty = True

    def _submit_seed_comment(
        self,
        seed_id: str,
        seed_number: int,
        body: str,
        *,
        steer_handle: str | None = None,
    ) -> None:
        try:
            self.client.create_seed_comment(
                seed_id,
                CreateSeedCommentRequest(body=body),
            )
            self.invalidate_cache(seed_id)
            self.refresh_data(invalidate=False)
            with self.lock:
                self.state.inspector_tab = "timeline"
                if steer_handle is not None:
                    self.state.status_message = (
                        f"Sent steering directive to @{steer_handle} on seed #{seed_number}."
                    )
                else:
                    self.state.status_message = f"Posted comment on seed #{seed_number}."
                self.dirty = True
            self._on_selection_or_tab_changed()
        except CLIError as exc:
            with self.lock:
                self.state.error_message = exc.message
                self.dirty = True

    def _submit_create_seed(
        self,
        proj_id: str,
        title: str,
        description: str,
        autonomy: SeedAutonomy,
    ) -> None:
        try:
            created = self.client.create_seed(
                CreateSeedRequest(
                    project_id=proj_id,
                    title=title,
                    description=description,
                    autonomy=autonomy,
                )
            )
            self.refresh_data(invalidate=True)
            selected_changed = False
            with self.lock:
                for idx, s in enumerate(self.state.filtered_seeds):
                    if s.id == created.id:
                        self.state.selected_indices["seeds"] = idx
                        selected_changed = True
                        break
                proj_slug = self.state.project_slug_by_id.get(
                    created.project_id, created.project_id
                )
                self.state.status_message = (
                    f"Created seed {proj_slug}#{created.number} "
                    f"({created.autonomy}): {created.title}"
                )
                self.dirty = True
            if selected_changed:
                self._on_selection_or_tab_changed()
        except CLIError as exc:
            with self.lock:
                self.state.error_message = exc.message
                self.dirty = True

    def _submit_coordinator_question(
        self,
        question: str,
        proj_id: str | None,
        seed_id: str | None,
    ) -> None:
        try:
            resp = self.client.chat_coordinator(
                question, project_id=proj_id, seed_id=seed_id
            )
            with self.lock:
                self.state.coordinator_reply = resp.reply
                self.state.status_message = f"@coordinator replied ({resp.model})."
                self.dirty = True
        except CLIError as exc:
            with self.lock:
                self.state.error_message = exc.message
                self.dirty = True

    def _submit_input(self, mode: str, text: str) -> None:
        if mode == "filter":
            self.state.search_query = text
            self._clamp_selection()
            self._on_selection_or_tab_changed()
            if text:
                self.state.status_message = f"Filter applied: '{text}'"
            else:
                self.state.status_message = "Cleared search filter."
            return

        seed = self.state.selected_seed
        if mode == "approve" and seed is not None:
            seed_id = seed.id
            seed_num = seed.number
            comment_val = text if text else None
            self.state.error_message = None
            self.state.status_message = f"Approving stage on seed #{seed_num}..."
            self.dirty = True
            self._enqueue_network_action(
                lambda: self._submit_approval(
                    seed_id, seed_num, ApprovalDecision.APPROVED, comment_val
                )
            )
            return

        if mode == "request_changes" and seed is not None:
            if not text:
                self.state.error_message = (
                    "Feedback comment is required when requesting changes."
                )
                return
            seed_id = seed.id
            seed_num = seed.number
            self.state.error_message = None
            self.state.status_message = f"Requesting changes on seed #{seed_num}..."
            self.dirty = True
            self._enqueue_network_action(
                lambda: self._submit_approval(
                    seed_id, seed_num, ApprovalDecision.CHANGES_REQUESTED, text
                )
            )
            return

        if mode == "reject" and seed is not None:
            if not text:
                self.state.error_message = "Rejection reason is required."
                return
            seed_id = seed.id
            seed_num = seed.number
            self.state.error_message = None
            self.state.status_message = f"Rejecting seed #{seed_num}..."
            self.dirty = True
            self._enqueue_network_action(
                lambda: self._submit_approval(
                    seed_id, seed_num, ApprovalDecision.REJECTED, text
                )
            )
            return

        if mode == "steer" and seed is not None:
            if not text:
                self.state.error_message = "Steering directive cannot be empty."
                return
            seed_id = seed.id
            seed_num = seed.number
            handle = self._resolve_stage_agent_handle(seed.project_id, str(seed.stage))
            existing = {m.group(1).lower() for m in _MENTION_RE.finditer(text)}
            body = text if handle.lower() in existing else f"@{handle} {text}"
            self.state.error_message = None
            self.state.status_message = (
                f"Sending steering directive to @{handle} on seed #{seed_num}..."
            )
            self.dirty = True
            self._enqueue_network_action(
                lambda: self._submit_seed_comment(
                    seed_id, seed_num, body, steer_handle=handle
                )
            )
            return

        if mode == "comment" and seed is not None:
            if not text:
                self.state.error_message = "Comment text cannot be empty."
                return
            seed_id = seed.id
            seed_num = seed.number
            self.state.error_message = None
            self.state.status_message = f"Posting comment on seed #{seed_num}..."
            self.dirty = True
            self._enqueue_network_action(
                lambda: self._submit_seed_comment(seed_id, seed_num, text)
            )
            return

        if mode in {"new_seed_prompt", "new_seed_title"}:
            if not text:
                self.state.error_message = "Seed prompt/title cannot be empty."
                return
            proj_id = (
                self.state.new_seed_project_id
                or self.state.project_filter
                or (seed.project_id if seed else None)
                or (self.state.projects[0].id if self.state.projects else None)
            )
            if not proj_id:
                self.state.error_message = "No project available to create seed in."
                return
            resolved_proj = self._resolve_project_by_ref(proj_id)
            if resolved_proj is not None:
                proj_id = resolved_proj.id

            title = text if len(text) <= 80 else f"{text[:77].rstrip()}..."
            autonomy_val = SeedAutonomy(
                self.state.new_seed_autonomy or SeedAutonomy.SUPERVISED
            )
            target_proj_id = proj_id
            self.state.error_message = None
            self.state.status_message = "Creating seed..."
            self.dirty = True
            self._enqueue_network_action(
                lambda: self._submit_create_seed(
                    target_proj_id, title, text, autonomy_val
                )
            )
            return

        if mode == "ask_coordinator":
            if not text:
                self.state.error_message = "Question for @coordinator cannot be empty."
                return
            coord_proj_id = seed.project_id if seed else None
            coord_seed_id = seed.id if seed else None
            self.state.error_message = None
            self.state.status_message = "Waiting for @coordinator reply..."
            self.dirty = True
            self._enqueue_network_action(
                lambda: self._submit_coordinator_question(
                    text, coord_proj_id, coord_seed_id
                )
            )
            return

    def _execute_retry_seed(
        self,
        seed_id: str,
        seed_number: int,
        seed_status: SeedStatus | str,
    ) -> None:
        try:
            if seed_status == SeedStatus.STAGED:
                started = self.client.start_seed(seed_id)
                self.refresh_data(invalidate=True)
                with self.lock:
                    self.state.status_message = (
                        f"Started staged seed #{started.number} in stage {started.stage}."
                    )
                    self.dirty = True
                return
            if seed_status == SeedStatus.STOPPED:
                resumed = self.client.resume_seed(seed_id)
                self.refresh_data(invalidate=True)
                with self.lock:
                    self.state.status_message = (
                        f"Resumed stopped seed #{resumed.number} in stage {resumed.stage}."
                    )
                    self.dirty = True
                return
            run = self.client.create_run(CreateRunRequest(seed_id=seed_id))
            self.refresh_data(invalidate=True)
            with self.lock:
                self.state.status_message = (
                    f"Queued retry run {run.id} (attempt #{run.attempt}) for seed #{seed_number}."
                )
                self.dirty = True
        except CLIError as exc:
            with self.lock:
                self.state.error_message = exc.message
                self.dirty = True

    def _action_retry_seed(self) -> None:
        seed = self.state.selected_seed
        if seed is None:
            self.state.error_message = "No seed selected to retry."
            return
        if seed.status not in {
            SeedStatus.FAILED,
            SeedStatus.BLOCKED,
            SeedStatus.STOPPED,
            SeedStatus.STAGED,
        }:
            self.state.error_message = (
                f"Seed #{seed.number} has status '{seed.status}'. "
                "Only 'failed', 'blocked', 'stopped', or 'staged' seeds can be started/retried."
            )
            return
        seed_id = seed.id
        seed_number = seed.number
        seed_status = seed.status
        self.state.error_message = None
        self.state.status_message = f"Starting/retrying seed #{seed_number}..."
        self.dirty = True
        self._enqueue_network_action(
            lambda: self._execute_retry_seed(seed_id, seed_number, seed_status)
        )

    def _execute_cancel_run(
        self,
        *,
        run_id: str | None,
        seed_id: str | None,
        seed_number: int | None,
        cached_active_run_id: str | None,
    ) -> None:
        try:
            if run_id is not None:
                cancelled = self.client.cancel_run(run_id)
                self.refresh_data(invalidate=True)
                with self.lock:
                    self.state.status_message = f"Cancelled run {cancelled.id}."
                    self.dirty = True
                return

            assert seed_id is not None and seed_number is not None
            target_run_id = cached_active_run_id
            if target_run_id is None:
                active_runs = self.client.list_runs(
                    seed_id=seed_id, active_only=True, limit=1
                )
                if not active_runs:
                    with self.lock:
                        self.state.error_message = (
                            f"No active run found for seed #{seed_number}."
                        )
                        self.dirty = True
                    return
                target_run_id = active_runs[0].id

            cancelled = self.client.cancel_run(target_run_id)
            self.refresh_data(invalidate=True)
            with self.lock:
                self.state.status_message = (
                    f"Cancelled run {cancelled.id} on seed #{seed_number}."
                )
                self.dirty = True
        except CLIError as exc:
            with self.lock:
                self.state.error_message = exc.message
                self.dirty = True

    def _action_cancel_run(self) -> None:
        if self.state.active_view == "runs" and self.state.selected_run is not None:
            run_id = self.state.selected_run.id
            self.state.error_message = None
            self.state.status_message = f"Cancelling run {run_id}..."
            self.dirty = True
            self._enqueue_network_action(
                lambda: self._execute_cancel_run(
                    run_id=run_id,
                    seed_id=None,
                    seed_number=None,
                    cached_active_run_id=None,
                )
            )
            return

        seed = self.state.selected_seed
        if seed is None:
            self.state.error_message = "No seed or run selected to cancel."
            return
        active_runs = [
            r
            for r in self.state.runs
            if r.seed_id == seed.id and r.status in RUN_ACTIVE_STATUSES
        ]
        cached_run_id = active_runs[0].id if active_runs else None
        seed_id = seed.id
        seed_number = seed.number
        self.state.error_message = None
        self.state.status_message = f"Cancelling active run on seed #{seed_number}..."
        self.dirty = True
        self._enqueue_network_action(
            lambda: self._execute_cancel_run(
                run_id=None,
                seed_id=seed_id,
                seed_number=seed_number,
                cached_active_run_id=cached_run_id,
            )
        )
