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

The intended journey is **cosign keyless** (Fulcio); enclava verifies against the
public Sigstore trust root, with the signer pinned per-app at `create` time.

```bash
# 1. build + push to GHCR
docker login ghcr.io -u <gh-user> -p <PAT-with-write:packages>
docker build -t ghcr.io/<gh-user>/enclava-canary:latest .
docker push ghcr.io/<gh-user>/enclava-canary:latest      # note the sha256 digest in the output

# 2. sign keyless (interactive IdP login → Fulcio; observe the identity email + issuer it uses)
cosign sign --yes ghcr.io/<gh-user>/enclava-canary@sha256:<digest>

# 3. create the app, pinning that signer identity
enclava create \
  --image ghcr.io/<gh-user>/enclava-canary:latest \
  --signer-subject <signer-email> \
  --signer-issuer <issuer>   # e.g. https://accounts.google.com ; omit if GitHub-Actions default

# 4. deploy (auto-unlock = cleanest autonomous green)
enclava deploy --image ghcr.io/<gh-user>/enclava-canary@sha256:<digest>
```

A password-mode deploy (instead of step 4) additionally surfaces `/status` →
`claims_verified` and the ownership identity in the page, and exercises the
TEE bootstrap-claim handoff.
