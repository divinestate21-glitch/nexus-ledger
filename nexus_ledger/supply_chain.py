"""Supply Chain Trust Module for Nexus Ledger v5.0.

Provides cryptographic verification of package dependencies to detect
compromised packages, hash mismatches, and temporal anomalies.

Triggered by the axios npm supply chain attack — March 31, 2026.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .ledger import Ledger


SUPPLY_CHAIN_EVENT_TYPE = "DependencyInstall"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_hash(h: str) -> str:
    """Normalize hash by stripping sha256: prefix if present."""
    h = str(h).strip()
    if h.startswith("sha256:"):
        return h[7:]
    return h


def _hash_match(source_hash: str, expected_hash: str) -> bool:
    """Compare two SHA-256 hashes, normalizing prefixes."""
    return _normalize_hash(source_hash) == _normalize_hash(expected_hash)


class SupplyChainModule:
    """Core supply chain verification module.

    Integrates with the Nexus Ledger SQLite store to record and verify
    dependency installation receipts.
    """

    def __init__(self, ledger: Ledger, agent_pubkey: str) -> None:
        self._ledger = ledger
        self._agent_pubkey = agent_pubkey
        self._ensure_supply_chain_table()

    def _ensure_supply_chain_table(self) -> None:
        """Create the supply_chain_deps table if it doesn't exist."""
        self._ledger._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS supply_chain_deps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_pubkey TEXT NOT NULL,
                package TEXT NOT NULL,
                version TEXT NOT NULL,
                registry TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                expected_hash TEXT NOT NULL,
                hash_match INTEGER NOT NULL,
                install_command TEXT,
                environment TEXT
            )
            """
        )
        self._ledger._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scd_package ON supply_chain_deps(package)"
        )
        self._ledger._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scd_package_version ON supply_chain_deps(package, version)"
        )
        self._ledger._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scd_timestamp ON supply_chain_deps(timestamp)"
        )
        self._ledger._conn.commit()

    def record_dependency(
        self,
        package: str,
        version: str,
        registry: str,
        source_hash: str,
        expected_hash: str,
        install_command: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a dependency installation with cryptographic proof.

        Args:
            package: Package name (e.g., "axios")
            version: Package version (e.g., "1.7.2")
            registry: Registry source (e.g., "npm", "pypi", "cargo")
            source_hash: SHA-256 hash of the downloaded tarball/artifact
            expected_hash: SHA-256 hash published by the registry
            install_command: The install command used (e.g., "npm install axios@1.7.2")
            environment: Environment description (e.g., "Mac Studio M2 Ultra")

        Returns:
            A receipt dict with all dependency installation details.
        """
        pkg = str(package).strip()
        ver = str(version).strip()
        reg = str(registry).strip()
        src_hash = str(source_hash).strip()
        exp_hash = str(expected_hash).strip()

        if not pkg:
            raise ValueError("package is required")
        if not ver:
            raise ValueError("version is required")
        if not reg:
            raise ValueError("registry is required")
        if not src_hash:
            raise ValueError("source_hash is required")
        if not exp_hash:
            raise ValueError("expected_hash is required")

        matched = _hash_match(src_hash, exp_hash)
        timestamp = _utc_now()

        # Normalize hashes to include sha256: prefix for storage
        src_norm = f"sha256:{_normalize_hash(src_hash)}"
        exp_norm = f"sha256:{_normalize_hash(exp_hash)}"

        cmd = str(install_command).strip() if install_command else f"{reg} install {pkg}@{ver}"
        env = str(environment).strip() if environment else ""

        cur = self._ledger._conn.execute(
            """
            INSERT INTO supply_chain_deps (
                timestamp, agent_pubkey, package, version, registry,
                source_hash, expected_hash, hash_match, install_command, environment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                self._agent_pubkey,
                pkg,
                ver,
                reg,
                src_norm,
                exp_norm,
                1 if matched else 0,
                cmd,
                env,
            ),
        )
        self._ledger._conn.commit()

        receipt = {
            "receipt_type": SUPPLY_CHAIN_EVENT_TYPE,
            "id": int(cur.lastrowid),
            "timestamp": timestamp,
            "agent_pubkey": self._agent_pubkey,
            "data": {
                "package": pkg,
                "version": ver,
                "registry": reg,
                "source_hash": src_norm,
                "expected_hash": exp_norm,
                "hash_match": matched,
                "install_command": cmd,
                "installed_by": f"agent:{self._agent_pubkey[:16]}",
                "environment": env,
            },
        }

        # Also write a summary entry to the main ledger log
        self._ledger.log(
            # Pass as a (private_key, public_key) equivalent — we use pubkey directly
            # by using a dict with just public_key for read-only log entry
            {"private_key": "00" * 32, "public_key": self._agent_pubkey},
            SUPPLY_CHAIN_EVENT_TYPE,
            receipt["data"],
        ) if False else None  # Skip ledger log — supply_chain table is the source of truth

        return receipt

    def verify_dependency(
        self,
        package: str,
        version: str,
        against: str = "registry",
    ) -> bool:
        """Verify a previously recorded dependency.

        Args:
            package: Package name to look up
            version: Package version to check
            against: Verification mode:
                     - "registry" (default): check hash_match from stored receipt
                     - A hex hash string: compare against that specific hash

        Returns:
            True if the dependency is verified safe, False otherwise.
        """
        pkg = str(package).strip()
        ver = str(version).strip()
        mode = str(against).strip()

        if not pkg or not ver:
            return False

        rows = self._ledger._conn.execute(
            """
            SELECT source_hash, expected_hash, hash_match
            FROM supply_chain_deps
            WHERE package = ? AND version = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (pkg, ver),
        ).fetchall()

        if not rows:
            return False

        row = dict(rows[0])

        if mode == "registry":
            return bool(row["hash_match"])

        # Against a specific known-good hash
        return _hash_match(str(row["source_hash"]), mode) or _hash_match(str(row["expected_hash"]), mode)

    def dependency_audit(self) -> List[Dict[str, Any]]:
        """Return all dependency installation receipts for this agent.

        Returns:
            List of receipt dicts for all recorded dependencies, newest last.
        """
        rows = self._ledger._conn.execute(
            """
            SELECT id, timestamp, agent_pubkey, package, version, registry,
                   source_hash, expected_hash, hash_match, install_command, environment
            FROM supply_chain_deps
            WHERE agent_pubkey = ?
            ORDER BY id ASC
            """,
            (self._agent_pubkey,),
        ).fetchall()

        results: List[Dict[str, Any]] = []
        for row in rows:
            r = dict(row)
            results.append({
                "receipt_type": SUPPLY_CHAIN_EVENT_TYPE,
                "id": r["id"],
                "timestamp": r["timestamp"],
                "agent_pubkey": r["agent_pubkey"],
                "data": {
                    "package": r["package"],
                    "version": r["version"],
                    "registry": r["registry"],
                    "source_hash": r["source_hash"],
                    "expected_hash": r["expected_hash"],
                    "hash_match": bool(r["hash_match"]),
                    "install_command": r["install_command"],
                    "environment": r["environment"] or "",
                },
            })
        return results

    def detect_temporal_anomaly(
        self,
        package: str,
        version: str,
    ) -> Dict[str, Any]:
        """Detect temporal anomalies in package version publication order.

        Checks if a given version was published after a semantically newer one,
        which would indicate a version injection attack (e.g., 0.30.4 published
        after 1.7.2 exists, retroactively targeting older pinned versions).

        Args:
            package: Package name to check
            version: Version to analyze

        Returns:
            A dict with:
            - anomaly (bool): True if a temporal anomaly was detected
            - reason (str): Human-readable explanation
            - version_timestamps (dict): Map of version → first_seen timestamp
        """
        pkg = str(package).strip()
        ver = str(version).strip()

        if not pkg or not ver:
            return {"anomaly": False, "reason": "Invalid package or version", "version_timestamps": {}}

        rows = self._ledger._conn.execute(
            """
            SELECT version, MIN(timestamp) as first_seen
            FROM supply_chain_deps
            WHERE package = ?
            GROUP BY version
            ORDER BY first_seen ASC
            """,
            (pkg,),
        ).fetchall()

        if not rows:
            return {
                "anomaly": False,
                "reason": f"No records found for package '{pkg}'",
                "version_timestamps": {},
            }

        version_timestamps: Dict[str, str] = {str(r["version"]): str(r["first_seen"]) for r in rows}

        if ver not in version_timestamps:
            return {
                "anomaly": False,
                "reason": f"Version '{ver}' not found in records for '{pkg}'",
                "version_timestamps": version_timestamps,
            }

        # Check if any semantically "older" version was FIRST SEEN after this version
        # This detects retroactive injection: e.g., 0.30.4 appearing after 1.7.2
        target_ts = version_timestamps[ver]
        anomalies: List[str] = []

        for other_ver, other_ts in version_timestamps.items():
            if other_ver == ver:
                continue
            # Anomaly pattern: a HIGHER (newer) version was already seen BEFORE
            # the current target version appeared.
            # Example: 1.7.2 existed at t1, then 0.30.4 appeared at t2 > t1.
            # This means an older version was published retroactively — version injection.
            if other_ts < target_ts:
                # other_ver appeared BEFORE target — check if it's semantically newer
                try:
                    target_parts = _parse_semver(ver)
                    other_parts = _parse_semver(other_ver)
                    if other_parts > target_parts:
                        # A semantically newer version already existed before this version appeared.
                        # This is suspicious: why publish an older version after a newer one exists?
                        anomalies.append(
                            f"Version {other_ver} (newer) was already seen at {other_ts}, "
                            f"before {ver} first appeared at {target_ts}"
                        )
                except ValueError:
                    # Non-semver: skip — can't determine ordering
                    pass

        if anomalies:
            return {
                "anomaly": True,
                "reason": "; ".join(anomalies),
                "version_timestamps": version_timestamps,
            }

        return {
            "anomaly": False,
            "reason": "No temporal anomalies detected",
            "version_timestamps": version_timestamps,
        }

    def query_dependency_from_agent(
        self,
        other_ledger: Ledger,
        other_pubkey: str,
        package: str,
        version: str,
    ) -> Optional[Dict[str, Any]]:
        """Cross-agent verification: query another agent's dependency records.

        Args:
            other_ledger: The other agent's Ledger instance
            other_pubkey: The other agent's public key
            package: Package name to look up
            version: Package version to check

        Returns:
            The most recent receipt from the other agent, or None if not found.
        """
        pkg = str(package).strip()
        ver = str(version).strip()

        rows = other_ledger._conn.execute(
            """
            SELECT id, timestamp, agent_pubkey, package, version, registry,
                   source_hash, expected_hash, hash_match, install_command, environment
            FROM supply_chain_deps
            WHERE agent_pubkey = ? AND package = ? AND version = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (other_pubkey, pkg, ver),
        ).fetchall()

        if not rows:
            return None

        r = dict(rows[0])
        return {
            "receipt_type": SUPPLY_CHAIN_EVENT_TYPE,
            "id": r["id"],
            "timestamp": r["timestamp"],
            "agent_pubkey": r["agent_pubkey"],
            "data": {
                "package": r["package"],
                "version": r["version"],
                "registry": r["registry"],
                "source_hash": r["source_hash"],
                "expected_hash": r["expected_hash"],
                "hash_match": bool(r["hash_match"]),
                "install_command": r["install_command"],
                "environment": r["environment"] or "",
            },
        }


def _parse_semver(version: str) -> tuple:
    """Parse a semver string into a comparable tuple.

    Raises ValueError if not parseable.
    """
    ver = str(version).strip().lstrip("v")
    # Handle pre-release suffixes (e.g., 1.0.0-alpha)
    base = ver.split("-")[0].split("+")[0]
    parts = base.split(".")
    if len(parts) < 2:
        raise ValueError(f"Cannot parse semver: {version}")
    result = []
    for p in parts[:3]:
        if not p.isdigit():
            raise ValueError(f"Non-numeric semver component: {p}")
        result.append(int(p))
    # Pad to 3 parts
    while len(result) < 3:
        result.append(0)
    return tuple(result)
