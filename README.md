# enclava-canary

A minimal healthy workload for exercising **custom-image** confidential deploys on
enclava (SEV-SNP). It exists to prove a fully-green custom deploy end-to-end —
`enclava-init` reaches ready **and** the workload actually serves — and to make
the TEE's attestation visible in the browser.

## What it does

- `GET /health` → `200 ok` (for anyone curling; the platform's readiness probe is a
  bare TCP-socket connect on the `EXPOSE` port, so this isn't strictly required).
- `GET /` → a polished page that server-side fetches the in-pod `attestation-proxy`
  sidecar (`http://127.0.0.1:8081`) and renders:
  - `/v1/attestation/info` — SEV-SNP attestation type, runtime class, the **signed
    policy** the VM was admitted under (url + sha256), AMD endorsement chain, trust
    model. Mode-independent.
  - `/status` — `claims_verified`, ownership `state`, instance/tenant identity hashes
    (password-unlock mode only; 404s gracefully in auto mode).
- Bumps a visit counter at `/app/data/visits` when that path is a writable mount
  (i.e. when the deploy sets `storage.paths=["/app/data"]`) — proving the bind is
  writable by the workload. Degrades to "no /app/data mount" otherwise.

The attestation data is fetched **server-side** (the sidecar is only reachable
inside the pod, not from the visitor's browser). The page therefore shows what
the TEE's attested sidecar reports; an out-of-band nonce challenge (shown at the
bottom of the page) is how a third party would independently verify it.

## Run locally

```bash
python3 server.py   # serves http://localhost:8080 (no sidecar → attestation cards show "unavailable")
```

## Build, sign, deploy

A GitHub Actions workflow (`.github/workflows/build-sign.yml`) builds, pushes to
GHCR, and **signs keyless** on every push to `main` (or via `workflow_dispatch`).
It uses the built-in `GITHUB_TOKEN` (no PAT) and GitHub OIDC for cosign (no manual
IdP login). The job summary prints the image digest + the exact signer subject.

enclava verifies the signature against the **public Sigstore** trust root, with
the signer pinned per-app at `create` time (issuer defaults to GitHub Actions'
`https://token.actions.githubusercontent.com`).

1. Push to `main` → CI builds + signs. Grab the **digest** (`sha256:…`) from the
   Actions run summary.
2. Flip the GHCR package to **public** so the cluster can pull without credentials
   (GitHub → Packages → `enclava-canary` → Package settings → Change visibility).
3. Verify the signature locally (confirms the exact subject to pin):
   ```bash
   cosign verify \
     --certificate-identity https://github.com/gzuuus/enclava-canary/.github/workflows/build-sign.yml@refs/heads/main \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com \
     ghcr.io/gzuuus/enclava-canary@sha256:<digest> && echo "signature verified ✅"
   ```
4. Create + deploy (**password mode** — the working first-deploy path; see Gotchas):
   ```bash
   enclava create \
     --image ghcr.io/gzuuus/enclava-canary:latest \
     --signer-subject https://github.com/gzuuus/enclava-canary/.github/workflows/build-sign.yml@refs/heads/main
   enclava deploy --image ghcr.io/gzuuus/enclava-canary@sha256:<digest> \
     --storage-password-file <path-to-password>
   ```
   Password mode also surfaces `/status` → `claims_verified` and the ownership
   identity in the page, and exercises the TEE bootstrap-claim handoff.

## Gotchas (learned the hard way)

- **Use `unlock.mode = "password"` for the first deploy.** `mode = "auto"`
  stalls: `enclava create` sends no bootstrap identity for auto mode, and
  `enclava-init`'s auto path awaits a KBS wrap-key the standalone path never
  provisions → the app hangs at `TEE: unclaimed` indefinitely. Password mode
  derives the owner seed from the claim password (no KBS dependency). After the
  first deploy, `enclava auto-unlock enable` can seal the seed for restarts.
- **`storage.paths` targets must be a `VOLUME` in the image.** The workload
  rootfs is read-only, so `enclava-init`'s bind fails (`Read-only file system`)
  unless the path is declared as a volume — hence `VOLUME ["/app/data"]` in this
  Dockerfile. `enclava status` surfaces the exact failure via `tee_error`. Omit
  `storage.paths` if your app needs no persistent storage.
- **Redeploy over a *failed* deploy goes "drifted."** If a deploy fails (e.g. the
  VOLUME issue above), don't `enclava deploy` a fix over it — the pod won't roll
  (`Status: drifted`). `enclava destroy --app <name> --force`, then `create` +
  `deploy` from clean.
- **Some enclava envs serve a Let's Encrypt *staging* cert** — browsers reject
  it (`ERR_CERT_AUTHORITY_INVALID`). This is a platform-level tshooting toggle
  (rate-limit avoidance), not per-app; the canary itself has no TLS knob. Browse
  with `curl -sk`, snapshot the page, or trust the LE staging root locally. On an
  env using production LE, certs are browser-trusted automatically.
