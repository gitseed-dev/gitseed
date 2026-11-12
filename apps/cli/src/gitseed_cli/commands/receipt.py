from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import typer
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from gitseed_cli.config import get_opts, make_client
from gitseed_cli.formatting import console, dump_json, err_console

receipt_app = typer.Typer(
    name="receipt",
    help="Fetch and verify signed Ed25519 governance receipts for Seeds.",
    no_args_is_help=True,
)


def _canonicalize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _b64url_decode(data: str) -> bytes:
    padded = data.strip() + "=" * ((4 - len(data.strip()) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def verify_receipt_envelope_dict(
    envelope: dict[str, Any],
    *,
    public_key_pem: str | None = None,
) -> tuple[bool, str]:
    """Verify a governance receipt envelope locally using Ed25519."""
    if not isinstance(envelope, dict):
        return False, "Receipt envelope is not a JSON object."
    if envelope.get("algorithm") not in (None, "Ed25519"):
        return False, f"Unsupported signature algorithm '{envelope.get('algorithm')}'."

    receipt = envelope.get("receipt")
    if not isinstance(receipt, dict):
        return False, "Missing 'receipt' payload object in envelope."

    expected_canonical = _canonicalize_payload(receipt)
    provided_canonical = envelope.get("canonical_json")
    if isinstance(provided_canonical, str) and provided_canonical != expected_canonical:
        return False, "Tamper detected: 'canonical_json' does not match 'receipt' payload."

    sig_b64 = envelope.get("signature")
    if not isinstance(sig_b64, str) or not sig_b64.strip():
        return False, "Missing 'signature' in envelope."

    pem_to_use = (public_key_pem or "").strip()
    if not pem_to_use:
        return (
            False,
            "No trusted Ed25519 public key provided "
            "(offline verification requires --public-key-file <path> or --public-key <pem>).",
        )

    try:
        signature = _b64url_decode(sig_b64)
        loaded_key = serialization.load_pem_public_key(pem_to_use.encode("utf-8"))
        if not isinstance(loaded_key, Ed25519PublicKey):
            return False, "Provided public key is not an Ed25519 public key."
        expected_key_id = hashlib.sha256(
            loaded_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).hexdigest()[:16]
        env_key_id = envelope.get("key_id")
        if env_key_id and str(env_key_id).strip() != expected_key_id:
            return False, "Tamper detected: 'key_id' does not match the Ed25519 public key."
        loaded_key.verify(signature, expected_canonical.encode("utf-8"))
        return True, f"Valid Ed25519 signature (key_id={envelope.get('key_id', 'unknown')})."
    except (InvalidSignature, ValueError, TypeError) as exc:
        return False, f"Signature verification failed: {exc}"


def _append_artifacts_rows(table: Table, artifacts: Any) -> None:
    """Append locked artifact digest and task count rows when present on a receipt."""
    if artifacts is None:
        return
    if isinstance(artifacts, dict):
        spec_sha = artifacts.get("specification_sha256")
        criteria_sha = artifacts.get("acceptance_criteria_sha256")
        plan_sha = artifacts.get("plan_sha256")
        task_count = artifacts.get("task_count")
    else:
        spec_sha = getattr(artifacts, "specification_sha256", None)
        criteria_sha = getattr(artifacts, "acceptance_criteria_sha256", None)
        plan_sha = getattr(artifacts, "plan_sha256", None)
        task_count = getattr(artifacts, "task_count", None)

    if spec_sha is not None:
        table.add_row("Spec SHA-256", escape(str(spec_sha)))
    if criteria_sha is not None:
        table.add_row("Criteria SHA-256", escape(str(criteria_sha)))
    if plan_sha is not None:
        table.add_row("Plan SHA-256", escape(str(plan_sha)))
    if task_count is not None:
        table.add_row("Task Count", escape(str(task_count)))


@receipt_app.command("get")
def receipt_get(
    ctx: typer.Context,
    seed_id: str = typer.Argument(
        ...,
        help="Seed ID (sed_...) or reference (<project>#<number> or #<number>).",
    ),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Optional file path to write the signed receipt JSON envelope.",
    ),
) -> None:
    """Fetch a signed governance receipt for a Seed."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    envelope = client.get_seed_receipt(seed_id, project_override=opts.project)
    payload_dict = envelope.model_dump(mode="json")

    if file is not None:
        out_dict = {k: v for k, v in payload_dict.items() if k != "markdown"}
        file.write_text(json.dumps(out_dict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        console.print(f"[green]Wrote signed governance receipt to[/green] {escape(str(file))}")
        return

    if opts.output == "md" and envelope.markdown:
        typer.echo(envelope.markdown)
        return

    if opts.output == "json":
        dump_json(payload_dict)
        return

    # Default / table view: print summary table
    receipt_dict = payload_dict.get("receipt")
    if not isinstance(receipt_dict, dict):
        receipt_dict = {}
    seed_info = receipt_dict.get("seed")
    if not isinstance(seed_info, dict):
        seed_info = {}
    policy_info = receipt_dict.get("policy")
    if not isinstance(policy_info, dict):
        policy_info = {}
    runs_list = receipt_dict.get("runs")
    approvals_list = receipt_dict.get("approvals")

    table = Table(
        title=(
            f"Governance Receipt: {escape(str(seed_info.get('project_slug', '?')))}#"
            f"{escape(str(seed_info.get('number', '?')))} "
            f"({escape(str(seed_info.get('id', '?')))})"
        )
    )
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Schema", escape(str(receipt_dict.get("schema_version", "gitseed.receipt.v1"))))
    table.add_row("Title", escape(str(seed_info.get("title", ""))))
    table.add_row(
        "Stage / Status",
        f"{escape(str(seed_info.get('stage', '')))} / {escape(str(seed_info.get('status', '')))}",
    )
    table.add_row("Autonomy", escape(str(policy_info.get("autonomy", ""))))
    table.add_row("Min Approver Role", escape(str(policy_info.get("min_approver_role", ""))))
    table.add_row(
        "Prevent Self-Approval",
        escape(str(policy_info.get("prevent_self_approval", ""))),
    )
    _append_artifacts_rows(table, receipt_dict.get("artifacts"))
    table.add_row("Runs", str(len(runs_list) if isinstance(runs_list, list) else 0))
    table.add_row(
        "Approvals",
        str(len(approvals_list) if isinstance(approvals_list, list) else 0),
    )
    table.add_row(
        "Key ID",
        f"{escape(str(envelope.key_id))} ({escape(str(envelope.algorithm))})",
    )
    sig_short = (
        f"{envelope.signature[:20]}...{envelope.signature[-10:]}"
        if len(envelope.signature) > 34
        else envelope.signature
    )
    table.add_row("Signature", escape(str(sig_short)))
    console.print(table)


@receipt_app.command("verify")
def receipt_verify(
    ctx: typer.Context,
    path_or_stdin: str = typer.Argument(
        ...,
        help=(
            "Path to a signed receipt JSON file, '-' for stdin, or a Seed reference "
            "(<project>#<number>, #<number>, or sed_...)."
        ),
    ),
    public_key_file: Path | None = typer.Option(
        None,
        "--public-key-file",
        "-k",
        help="Path to a trusted Ed25519 public key PEM file (required with --offline).",
    ),
    public_key: str | None = typer.Option(
        None,
        "--public-key",
        help="Trusted Ed25519 public key PEM string (alternative to --public-key-file).",
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help=(
            "Verify offline without contacting the GitSeed server "
            "(requires --public-key-file <path> or --public-key <pem>)."
        ),
    ),
) -> None:
    """Verify a signed governance receipt JSON envelope offline or by Seed reference."""
    opts = get_opts(ctx)
    has_explicit_key = public_key_file is not None or bool(public_key and public_key.strip())
    if offline and not has_explicit_key:
        msg = (
            "Offline verification (--offline) requires --public-key-file <path> "
            "(or --public-key <pem>); embedded receipt public_key_pem is never trusted."
        )
        if opts.output == "json":
            dump_json({"valid": False, "detail": msg, "key_id": None})
        else:
            err_console.print(f"[red]Error:[/red] {escape(msg)}")
        raise typer.Exit(1)

    arg = path_or_stdin.strip()
    envelope: Any = None
    if arg == "-":
        raw_text = sys.stdin.read()
        try:
            envelope = json.loads(raw_text)
        except Exception as exc:
            err_console.print(f"[red]Error:[/red] Failed to parse receipt JSON: {escape(str(exc))}")
            raise typer.Exit(1) from exc
    else:
        target = Path(arg)
        if target.is_file():
            raw_text = target.read_text(encoding="utf-8")
            try:
                envelope = json.loads(raw_text)
            except Exception as exc:
                err_console.print(
                    f"[red]Error:[/red] Failed to parse receipt JSON: {escape(str(exc))}"
                )
                raise typer.Exit(1) from exc
        elif not arg.lower().endswith(".json") and (
            "#" in arg or arg.startswith("sed_") or arg.lstrip("#").isdigit()
        ):
            if offline:
                err_console.print(
                    "[red]Error:[/red] Cannot fetch Seed receipt from server in --offline mode; "
                    "pass a local receipt JSON file or '-' for stdin."
                )
                raise typer.Exit(1)
            client = make_client(ctx)
            env_model = client.get_seed_receipt(arg, project_override=opts.project)
            envelope = env_model.model_dump(mode="json")
        else:
            err_console.print(f"[red]Error:[/red] File not found: {escape(str(path_or_stdin))}")
            raise typer.Exit(1)

    pub_pem: str | None = None
    untrusted_key_error: str | None = None
    if public_key_file is not None:
        if not public_key_file.is_file():
            err_console.print(
                f"[red]Error:[/red] Public key file not found: {escape(str(public_key_file))}"
            )
            raise typer.Exit(1)
        pub_pem = public_key_file.read_text(encoding="utf-8")
    elif public_key and public_key.strip():
        pub_pem = public_key.strip()
    elif isinstance(envelope, dict):
        try:
            client = make_client(ctx)
            pk_resp = client.get_receipt_public_key()
            env_key_id = str(envelope.get("key_id") or "").strip()
            if not env_key_id or env_key_id == pk_resp.key_id:
                pub_pem = pk_resp.public_key_pem
            else:
                matched_pem: str | None = None
                for prev in getattr(pk_resp, "previous_public_keys", None) or []:
                    prev_kid = (
                        prev.get("key_id")
                        if isinstance(prev, dict)
                        else getattr(prev, "key_id", None)
                    )
                    prev_pem = (
                        prev.get("public_key_pem")
                        if isinstance(prev, dict)
                        else getattr(prev, "public_key_pem", None)
                    )
                    if prev_kid == env_key_id and prev_pem:
                        matched_pem = str(prev_pem)
                        break
                if matched_pem:
                    pub_pem = matched_pem
                else:
                    untrusted_key_error = (
                        f"Untrusted receipt key_id '{env_key_id}': "
                        "not found in server's active or previous public keys."
                    )
        except Exception as exc:
            untrusted_key_error = (
                f"Failed to fetch trusted Ed25519 public key from server ({exc}); "
                "pass --offline --public-key-file <path> to verify offline."
            )

    if untrusted_key_error is not None:
        ok, detail = False, untrusted_key_error
    else:
        ok, detail = verify_receipt_envelope_dict(envelope, public_key_pem=pub_pem)
    if opts.output == "json":
        receipt_dict = envelope.get("receipt") if isinstance(envelope, dict) else None
        artifacts_dict = (
            receipt_dict.get("artifacts") if isinstance(receipt_dict, dict) else None
        )
        dump_json(
            {
                "valid": ok,
                "detail": detail,
                "key_id": envelope.get("key_id") if isinstance(envelope, dict) else None,
                "artifacts": artifacts_dict,
            }
        )
        if not ok:
            raise typer.Exit(1)
        return

    if ok:
        console.print(
            Panel(
                f"[bold green]VERIFIED[/bold green] - {escape(str(detail))}",
                title="Governance Receipt",
            )
        )
        receipt_payload = envelope.get("receipt") if isinstance(envelope, dict) else None
        artifacts_payload = (
            receipt_payload.get("artifacts") if isinstance(receipt_payload, dict) else None
        )
        if artifacts_payload is not None:
            art_table = Table(title="Verified Receipt Artifacts")
            art_table.add_column("Field", style="bold cyan")
            art_table.add_column("Value")
            _append_artifacts_rows(art_table, artifacts_payload)
            console.print(art_table)
    else:
        err_console.print(
            Panel(
                f"[bold red]INVALID[/bold red] - {escape(str(detail))}",
                title="Governance Receipt",
            )
        )
        raise typer.Exit(1)
