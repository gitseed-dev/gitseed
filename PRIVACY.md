# GitSeed Open-Source Privacy Policy

**Effective Date:** November 11, 2026  
**Last Updated:** November 11, 2026

GitSeed is built on a simple privacy principle: **your source code, prompts, credentials, and engineering data belong to you and stay on your infrastructure.**

This Privacy Policy explains what information is (and is not) collected across the open-source **Self-Hosted GitSeed Control Plane & Tooling** (`gitseed-core`, `gitseed-runner`, `gitseed-cli`, `gitseed-mcp`, `gitseed-common`, and `apps/web`) and the public [`gitseed-dev/gitseed`](https://github.com/gitseed-dev/gitseed) repository.

---

## 1. Self-Hosted GitSeed Control Plane & Runner (Zero Telemetry)

When you run the self-hosted GitSeed stack (via Docker Compose, Kubernetes/Helm, or source):

- **No Phoning Home:** The self-hosted GitSeed Control Plane, Runner, Web UI, CLI (`gitseed`), and MCP server (`gitseed-mcp`) **do not** send telemetry, analytics, source code, prompts, repository metadata, or usage statistics to Trent Seed / GitSeed.
- **Local Secret Encryption:** API keys, Git tokens, and CLI OAuth bundles configured in your control plane are encrypted at rest inside your own PostgreSQL database using `HKDF-SHA256` Fernet keys derived from your instance's `GITSEED_SECRET_KEY`.
- **Direct Third-Party Connections:** When your self-hosted instance communicates with external model providers (e.g., Anthropic, OpenAI, Google Gemini, xAI) or Git hosts (e.g., GitHub, GitLab, Bitbucket), those requests go directly from your infrastructure to those providers and are governed by your respective accounts and their privacy policies.

---

## 2. Public Repository, Package Registries & Communications

- **GitHub & Package Registry Metrics:** When you star, clone, open issues/PRs on, or download packages from GitHub (`github.com/gitseed-dev/gitseed`), GitHub Container Registry (`ghcr.io/gitseed-dev`), or PyPI (`gitseed-cli`, `gitseed-mcp`, `gitseed-common`), standard aggregate download counts and repository traffic metrics are provided by those platforms under their respective privacy policies.
- **Voluntary Communications:** If you email us or participate in GitHub issues, pull requests (including CLA signatures), or discussions, we receive the contact information, GitHub handle, and message content you choose to provide and use it solely to maintain the project, administer contributor licensing, or respond to your inquiry.

---

## 3. Data Sharing & Selling

- **We never sell your personal information, credentials, or source code** to third parties.
- **We never use your source code or prompts to train AI models.**
- We only disclose information if required to comply with a valid legal obligation (such as a court order or subpoena) or to protect the security and integrity of the project.

---

## 4. Data Retention & Your Rights

- **Self-Hosted Data:** You have complete control over all data stored in your self-hosted GitSeed PostgreSQL database, Valkey store, and container volumes, and may modify or delete it at any time.
- **Tokens & Sessions:** You can revoke Personal Access Tokens (`DELETE /api/v1/auth/tokens/{id}`) at any time from the GitSeed Web UI or CLI (`gitseed delete token`).
- **Privacy Requests (GDPR / CCPA / Global):** Depending on your jurisdiction, you may have the right to request access to, correction of, or deletion of any personal information we hold about you. To exercise these rights privately, email us at the address below.

---

## 5. Changes to This Policy & Contact

We may update this Privacy Policy from time to time. Material changes will be reflected by updating the "Last Updated" date at the top of this document.

For privacy questions or data rights requests, email **privacy@gitseed.dev** (or for general public questions, open a thread in [GitHub Discussions](https://github.com/gitseed-dev/gitseed/discussions)).
