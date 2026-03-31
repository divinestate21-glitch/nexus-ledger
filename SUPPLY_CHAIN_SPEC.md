# Nexus Ledger — Supply Chain Trust Module
## Spec v1.0 — March 31, 2026
### Triggered by: axios npm compromise (100M+ weekly downloads, malicious payload)

---

## Problem Statement

When agents install dependencies, execute code from external sources, or pull packages from registries, there's ZERO cryptographic verification that what was installed matches what was expected. The axios attack proved this — malicious versions published to npm, automatically pulled by millions of projects, no verification layer.

## Feature: Dependency Verification Receipts

### Core API

```python
from nexus_ledger import Agent

agent = Agent("builder")

# Record a dependency installation with cryptographic proof
receipt = agent.record_dependency(
    package="axios",
    version="1.7.2",
    registry="npm",
    source_hash=sha256(downloaded_tarball),
    expected_hash=registry_published_hash,
    verified=source_hash == expected_hash
)

# Verify a previously recorded dependency
is_safe = agent.verify_dependency(
    package="axios",
    version="1.7.2",
    against="known_good_registry"  # or a pinned hash
)

# Get full dependency audit trail
audit = agent.dependency_audit()
# Returns: all packages installed, when, by which agent, hash match status
```

### What Gets Recorded Per Receipt

```json
{
    "receipt_type": "DependencyInstall",
    "timestamp": "2026-03-31T10:00:00Z",
    "agent_pubkey": "...",
    "data": {
        "package": "axios",
        "version": "1.7.2",
        "registry": "npm",
        "registry_url": "https://registry.npmjs.org/axios/-/axios-1.7.2.tgz",
        "source_hash": "sha256:abc123...",
        "expected_hash": "sha256:abc123...",
        "hash_match": true,
        "install_command": "npm install axios@1.7.2",
        "installed_by": "agent:builder",
        "environment": "Mac Studio M2 Ultra"
    },
    "signature": "ed25519:...",
    "proof_hash": "sha256:..."
}
```

### Verification Modes

1. **Hash Match** — Compare downloaded tarball hash against registry's published hash
2. **Known-Good Ledger** — Compare against a curated list of verified-safe hashes (community-maintained)
3. **Cross-Agent Verification** — If Agent A installed the same package before, Agent B can verify against A's receipt
4. **Temporal Anomaly Detection** — Flag packages where the hash changed AFTER initial publication (exactly what happened with axios)

### Detection Scenarios

| Scenario | What Nexus Catches |
|----------|-------------------|
| **axios attack** | Hash of 1.14.1 doesn't match any previously recorded receipt for axios 1.x — ALERT |
| **Typosquatting** | `axois` has no prior receipts in any agent's ledger — FLAG as unknown |
| **Version injection** | Package 0.30.4 published after 1.7.2 — temporal anomaly — FLAG |
| **Dependency confusion** | Private package name claimed on public registry — namespace mismatch — ALERT |

### Integration Points

**npm/pip/cargo hooks:**
```bash
# Pre-install hook that records + verifies
nexus-ledger verify-dep --package axios --version 1.14.1 --registry npm
# Returns: SAFE / ALERT / UNKNOWN
```

**CI/CD Pipeline:**
```yaml
# GitHub Actions step
- name: Verify dependencies
  run: nexus-ledger audit-deps --lockfile package-lock.json --fail-on alert
```

**Agent-to-Agent:**
```python
# Agent B asks Agent A: "Is this package safe?"
receipt = agent_b.query_dependency(
    peer="agent_a",
    package="axios",
    version="1.7.2"
)
# Returns Agent A's signed receipt with hash verification
```

### Architecture

```
Agent installs package
        │
        ▼
nexus.record_dependency()
        │
        ├── Hash the downloaded tarball (SHA-256)
        ├── Fetch expected hash from registry API
        ├── Compare: match = SAFE, mismatch = ALERT
        ├── Check temporal: was this version published after a newer one? FLAG
        ├── Sign receipt with agent's Ed25519 key
        └── Store in local ledger (SQLite)
                │
                ├── Optional: anchor to Ethereum (Base L2)
                └── Optional: broadcast to relay for cross-agent verification
```

### Why This Wins

1. **Zero workflow change** — 1 line added to install scripts, or automatic via pre-install hooks
2. **Retroactive detection** — if a package gets compromised AFTER you installed the safe version, your receipt proves you have the clean copy
3. **Cross-agent trust** — in multi-agent systems where different agents install dependencies, any agent can verify against any other agent's receipts
4. **On-chain anchoring** — receipts can be anchored to Ethereum for immutable proof of what was installed when

### Marketing Angle

> "100 million downloads compromised. Zero verification. One npm publish turned the most trusted HTTP client into malware. What if every `npm install` produced a cryptographic receipt? What if your agents verified dependencies the way they verify handoffs? Nexus Ledger v5.0 — trust the supply chain."

### Build Estimate

- **Core API** (record_dependency, verify_dependency, dependency_audit): 1 day
- **npm/pip hooks**: 1 day
- **Cross-agent verification**: already built (receipt exchange protocol)
- **Temporal anomaly detection**: 0.5 day
- **CLI commands** (verify-dep, audit-deps): 0.5 day
- **Tests**: 0.5 day

**Total: 3.5 days to ship v5.0 with supply chain trust**

### Positioning Shift

v4.2.2: "Trust layer for agent handoffs"
v5.0: "Trust layer for everything agents touch — handoffs, dependencies, data, and code"

The TAM expands from "people building multi-agent systems" to "anyone running agents that install packages or execute external code" — which is everyone.

---

*Spec by Mercury ☿️ — triggered by the axios supply chain attack, March 31, 2026*
