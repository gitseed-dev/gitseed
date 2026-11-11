# GitSeed Helm Chart

Deploy the GitSeed AI-native SDLC platform into Kubernetes with automatic **In-Cluster Kubernetes** compute pre-seeding, authenticated Valkey/PostgreSQL state, cookie-less VS Code Sandbox origin isolation, and multi-namespace workload isolation.

## Highlights

- **Local Checkout & OCI Chart Support (`./charts/gitseed` / `oci://ghcr.io/gitseed-dev/charts/gitseed`)**: Install directly from `./charts/gitseed` in a local checkout with self-built images pushed to your cluster registry (or from an OCI chart registry).
- **Pre-Seeded In-Cluster Kubernetes Compute**: On first startup in Kubernetes (`compute.bootstrapProvider: "kubernetes"` or `"auto"`), GitSeed automatically registers an **In-Cluster Kubernetes** compute backend using the control plane's mounted ServiceAccount token instead of Local Docker.
- **Non-Root Pod Security & Secret Preservation**:
  - Control-plane pods run with explicit numeric UID/GID `1000:1000` (`runAsNonRoot: true`, `allowPrivilegeEscalation: false`), and `egress-proxy` runs without mounting control-plane secrets.
  - Auto-generated secrets carry `"helm.sh/resource-policy": keep` so `GITSEED_SECRET_KEY` is never lost on accidental uninstall. For **ArgoCD / Flux** GitOps syncs, pre-create a Secret and set `auth.existingSecret` (plus `gitops.requireExistingSecret=true`).
- **Configurable Namespace Isolation (`compute.namespaceIsolation`)**:
  - `workload` *(default)*: Isolates SDLC stage Jobs in `<release-namespace>-runners` and interactive Sandboxes/Agent Sessions in `<release-namespace>-sandboxes`.
  - `project`: Isolates workloads into per-project namespaces (`<runnerNamespace>-<project_slug>` and `<sandboxNamespace>-<project_slug>`).
  - `shared`: Runs all workloads in a single shared namespace.

## Quickstart

```bash
# 1. Build and push the Control Plane and Runner images to your cluster registry
docker build -t your-registry/gitseed:0.1.0 .
docker build -t your-registry/gitseed-runner:0.1.0 ./apps/runner
docker push your-registry/gitseed:0.1.0
docker push your-registry/gitseed-runner:0.1.0

# 2. Install from a local repository checkout into the gitseed namespace
helm upgrade --install gitseed ./charts/gitseed \
  --namespace gitseed \
  --create-namespace \
  --set image.repository=your-registry/gitseed \
  --set image.tag=0.1.0 \
  --set runnerImage.repository=your-registry/gitseed-runner \
  --set runnerImage.tag=0.1.0

# GitOps (ArgoCD / Flux): supply a pre-created Secret so syncs never rotate GITSEED_SECRET_KEY
helm upgrade --install gitseed ./charts/gitseed \
  --namespace gitseed \
  --create-namespace \
  --set image.repository=your-registry/gitseed \
  --set runnerImage.repository=your-registry/gitseed-runner \
  --set auth.existingSecret=gitseed-secrets \
  --set gitops.requireExistingSecret=true
```

Port-forward the Web UI (`8421`) and dedicated cookie-less VS Code Sandbox origin (`8423`):

```bash
kubectl -n gitseed port-forward svc/gitseed-web 8421:8421 8423:8423
```

Open `http://localhost:8421`.

## Production Ingress & Origin Isolation

When exposing GitSeed behind a custom TLS Ingress or reverse proxy instead of `localhost` port-forwarding, configure **two distinct origins** so embedded `openvscode-server` webviews never share the `gitseed_session` cookie origin:

- `web.publicUrl`: Your primary Web UI origin (e.g., `https://gitseed.example.com`).
- `web.sandboxOrigin`: A separate origin dedicated exclusively to `/api/v1/vscode/*` (e.g., `https://sandbox.gitseed.example.com`).
- `web.corsOrigins`: Allowed browser origins matching `web.publicUrl` (e.g., `["https://gitseed.example.com"]`).
