"""Tests for Nexus Ledger v5.0 Supply Chain Trust Module."""

from __future__ import annotations

import hashlib
import os
import time
import tempfile
from typing import Any, Dict

import pytest

from nexus_ledger.agent import Agent
from nexus_ledger.ledger import Ledger
from nexus_ledger.receipt_types import DependencyInstall
from nexus_ledger.supply_chain import SupplyChainModule, _hash_match, _parse_semver


# ─── Helpers ────────────────────────────────────────────────────────────────

def _sha256(data: str) -> str:
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def _make_agent(tmp_path: str, name: str) -> Agent:
    return Agent(
        name,
        keys_dir=os.path.join(tmp_path, "keys"),
        db_path=os.path.join(tmp_path, f"{name}.db"),
        relays=["http://localhost:19999"],  # offline relay — no network needed
    )


# ─── Unit: DependencyInstall receipt type ───────────────────────────────────

class TestDependencyInstallReceiptType:
    def test_as_data_valid(self):
        dep = DependencyInstall(
            package="axios",
            version="1.7.2",
            registry="npm",
            source_hash="sha256:abc123",
            expected_hash="sha256:abc123",
            hash_match=True,
            install_command="npm install axios@1.7.2",
        )
        data = dep.as_data()
        assert data["package"] == "axios"
        assert data["version"] == "1.7.2"
        assert data["registry"] == "npm"
        assert data["hash_match"] is True
        assert data["install_command"] == "npm install axios@1.7.2"

    def test_validate_missing_package(self):
        dep = DependencyInstall(
            package="",
            version="1.7.2",
            registry="npm",
            source_hash="sha256:abc",
            expected_hash="sha256:abc",
            hash_match=True,
            install_command="npm install @1.7.2",
        )
        with pytest.raises(ValueError, match="package is required"):
            dep.validate()

    def test_validate_invalid_hash_match_type(self):
        dep = DependencyInstall(
            package="axios",
            version="1.7.2",
            registry="npm",
            source_hash="sha256:abc",
            expected_hash="sha256:abc",
            hash_match="yes",  # type: ignore[arg-type]
            install_command="npm install axios@1.7.2",
        )
        with pytest.raises(ValueError, match="hash_match must be a bool"):
            dep.validate()


# ─── Unit: Hash comparison ───────────────────────────────────────────────────

class TestHashMatch:
    def test_matching_hashes(self):
        h = hashlib.sha256(b"axios-1.7.2.tgz").hexdigest()
        assert _hash_match(h, h) is True

    def test_matching_with_prefix(self):
        h = hashlib.sha256(b"axios-1.7.2.tgz").hexdigest()
        assert _hash_match(f"sha256:{h}", h) is True
        assert _hash_match(h, f"sha256:{h}") is True
        assert _hash_match(f"sha256:{h}", f"sha256:{h}") is True

    def test_mismatching_hashes(self):
        h1 = hashlib.sha256(b"safe-package").hexdigest()
        h2 = hashlib.sha256(b"malicious-payload").hexdigest()
        assert _hash_match(h1, h2) is False

    def test_empty_hashes(self):
        assert _hash_match("", "") is True  # both empty = trivially match
        assert _hash_match("abc", "") is False


# ─── Unit: Semver parsing ────────────────────────────────────────────────────

class TestSemverParsing:
    def test_basic_semver(self):
        assert _parse_semver("1.7.2") == (1, 7, 2)
        assert _parse_semver("0.30.4") == (0, 30, 4)
        assert _parse_semver("2.0.0") == (2, 0, 0)

    def test_semver_ordering(self):
        assert _parse_semver("1.7.2") > _parse_semver("0.30.4")
        assert _parse_semver("2.0.0") > _parse_semver("1.99.99")

    def test_semver_with_v_prefix(self):
        assert _parse_semver("v1.2.3") == (1, 2, 3)

    def test_invalid_semver(self):
        with pytest.raises(ValueError):
            _parse_semver("not-semver")


# ─── Integration: Agent.record_dependency ───────────────────────────────────

class TestRecordDependency:
    def test_record_dependency_basic(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        safe_hash = _sha256("axios-1.7.2-tarball-content")

        receipt = agent.record_dependency(
            package="axios",
            version="1.7.2",
            registry="npm",
            source_hash=safe_hash,
            expected_hash=safe_hash,
            install_command="npm install axios@1.7.2",
        )

        assert receipt["receipt_type"] == "DependencyInstall"
        assert receipt["data"]["package"] == "axios"
        assert receipt["data"]["version"] == "1.7.2"
        assert receipt["data"]["registry"] == "npm"
        assert receipt["data"]["hash_match"] is True
        assert "timestamp" in receipt
        assert "agent_pubkey" in receipt

    def test_record_dependency_missing_package(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        with pytest.raises(ValueError, match="package is required"):
            agent.record_dependency(
                package="",
                version="1.7.2",
                registry="npm",
                source_hash="sha256:abc",
                expected_hash="sha256:abc",
            )

    def test_record_dependency_stores_normalized_hash(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        raw_hash = hashlib.sha256(b"content").hexdigest()

        receipt = agent.record_dependency(
            package="lodash",
            version="4.17.21",
            registry="npm",
            source_hash=raw_hash,       # no prefix
            expected_hash=raw_hash,
        )
        # Should be stored with sha256: prefix
        assert receipt["data"]["source_hash"].startswith("sha256:")
        assert receipt["data"]["expected_hash"].startswith("sha256:")


# ─── Integration: verify_dependency ─────────────────────────────────────────

class TestVerifyDependency:
    def test_verify_matching_hash(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        safe_hash = _sha256("axios-safe-content")

        agent.record_dependency(
            package="axios",
            version="1.7.2",
            registry="npm",
            source_hash=safe_hash,
            expected_hash=safe_hash,
        )

        assert agent.verify_dependency("axios", "1.7.2") is True

    def test_verify_mismatched_hash_compromised_package(self, tmp_path):
        """Simulate the axios attack: downloaded hash ≠ expected registry hash."""
        agent = _make_agent(str(tmp_path), "builder")

        expected_hash = _sha256("axios-clean-1.14.1-content")
        malicious_hash = _sha256("axios-malicious-1.14.1-backdoor-payload")

        receipt = agent.record_dependency(
            package="axios",
            version="1.14.1",
            registry="npm",
            source_hash=malicious_hash,   # what we actually downloaded
            expected_hash=expected_hash,   # what registry says it should be
        )

        # hash_match should be False — ALERT
        assert receipt["data"]["hash_match"] is False
        # verify should return False
        assert agent.verify_dependency("axios", "1.14.1") is False

    def test_verify_not_recorded(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        # Never recorded — should return False (UNKNOWN)
        assert agent.verify_dependency("unknown-pkg", "9.9.9") is False

    def test_verify_against_specific_hash(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        safe_hash = _sha256("requests-2.28.0-content")

        agent.record_dependency(
            package="requests",
            version="2.28.0",
            registry="pypi",
            source_hash=safe_hash,
            expected_hash=safe_hash,
        )

        # Verify against the specific hash value
        raw = hashlib.sha256(b"requests-2.28.0-content").hexdigest()
        assert agent.verify_dependency("requests", "2.28.0", against=raw) is True
        assert agent.verify_dependency("requests", "2.28.0", against="sha256:wronghash") is False


# ─── Integration: temporal anomaly detection ────────────────────────────────

class TestTemporalAnomalyDetection:
    def test_no_anomaly_normal_versioning(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        h = _sha256("content")

        # Record versions in logical order: 1.0.0 → 1.7.2
        agent.record_dependency("axios", "1.0.0", "npm", h, h)
        time.sleep(0.01)
        agent.record_dependency("axios", "1.7.2", "npm", h, h)

        result = agent._supply_chain.detect_temporal_anomaly("axios", "1.7.2")
        assert result["anomaly"] is False
        assert "1.0.0" in result["version_timestamps"]
        assert "1.7.2" in result["version_timestamps"]

    def test_temporal_anomaly_version_injection(self, tmp_path):
        """Simulate: 1.7.2 exists, then 0.30.4 appears (version injection attack)."""
        agent = _make_agent(str(tmp_path), "builder")
        h = _sha256("content")

        # 1.7.2 is recorded first (exists in the wild)
        agent.record_dependency("axios", "1.7.2", "npm", h, h)
        time.sleep(0.05)
        # 0.30.4 appears AFTER 1.7.2 was already seen — anomalous!
        agent.record_dependency("axios", "0.30.4", "npm", h, h)

        result = agent._supply_chain.detect_temporal_anomaly("axios", "0.30.4")
        assert result["anomaly"] is True
        assert "1.7.2" in result["reason"] or "0.30.4" in result["reason"]

    def test_temporal_anomaly_no_records(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        result = agent._supply_chain.detect_temporal_anomaly("nonexistent", "1.0.0")
        assert result["anomaly"] is False
        assert "No records" in result["reason"]

    def test_temporal_anomaly_version_not_found(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        h = _sha256("content")
        agent.record_dependency("axios", "1.7.2", "npm", h, h)

        result = agent._supply_chain.detect_temporal_anomaly("axios", "9.9.9")
        assert result["anomaly"] is False
        assert "not found" in result["reason"]


# ─── Integration: dependency audit ──────────────────────────────────────────

class TestDependencyAudit:
    def test_audit_empty(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        audit = agent.dependency_audit()
        assert isinstance(audit, list)
        assert len(audit) == 0

    def test_audit_lists_all_dependencies(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        h = _sha256("content")

        agent.record_dependency("axios", "1.7.2", "npm", h, h)
        agent.record_dependency("requests", "2.28.0", "pypi", h, h)
        agent.record_dependency("serde", "1.0.0", "cargo", h, h)

        audit = agent.dependency_audit()
        assert len(audit) == 3

        packages = [r["data"]["package"] for r in audit]
        assert "axios" in packages
        assert "requests" in packages
        assert "serde" in packages

    def test_audit_receipt_structure(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        h = _sha256("content")

        agent.record_dependency("lodash", "4.17.21", "npm", h, h)
        audit = agent.dependency_audit()

        assert len(audit) == 1
        receipt = audit[0]
        assert receipt["receipt_type"] == "DependencyInstall"
        assert "timestamp" in receipt
        assert "agent_pubkey" in receipt
        data = receipt["data"]
        assert "package" in data
        assert "version" in data
        assert "registry" in data
        assert "hash_match" in data
        assert "source_hash" in data
        assert "expected_hash" in data
        assert "install_command" in data

    def test_audit_flags_compromised_packages(self, tmp_path):
        agent = _make_agent(str(tmp_path), "builder")
        clean_hash = _sha256("clean-content")
        malicious_hash = _sha256("malicious-backdoor")

        agent.record_dependency("axios", "1.7.2", "npm", clean_hash, clean_hash)
        agent.record_dependency("axios", "1.14.1", "npm", malicious_hash, clean_hash)  # COMPROMISED

        audit = agent.dependency_audit()
        assert len(audit) == 2

        safe = next(r for r in audit if r["data"]["version"] == "1.7.2")
        compromised = next(r for r in audit if r["data"]["version"] == "1.14.1")

        assert safe["data"]["hash_match"] is True
        assert compromised["data"]["hash_match"] is False


# ─── Integration: cross-agent verification ──────────────────────────────────

class TestCrossAgentVerification:
    def test_agent_b_verifies_against_agent_a_receipt(self, tmp_path):
        """Agent B verifies a package is safe because Agent A recorded a clean hash."""
        agent_a = _make_agent(str(tmp_path / "a"), "agent_a")
        agent_b_dir = tmp_path / "b"
        agent_b_dir.mkdir(exist_ok=True)
        agent_b = _make_agent(str(agent_b_dir), "agent_b")

        safe_hash = _sha256("axios-1.7.2-clean-tarball")

        # Agent A already installed axios and recorded it as safe
        agent_a.record_dependency(
            package="axios",
            version="1.7.2",
            registry="npm",
            source_hash=safe_hash,
            expected_hash=safe_hash,
        )

        # Agent B queries Agent A's ledger to cross-verify
        receipt_from_a = agent_b._supply_chain.query_dependency_from_agent(
            other_ledger=agent_a._ledger,
            other_pubkey=agent_a.public_key,
            package="axios",
            version="1.7.2",
        )

        assert receipt_from_a is not None
        assert receipt_from_a["data"]["package"] == "axios"
        assert receipt_from_a["data"]["version"] == "1.7.2"
        assert receipt_from_a["data"]["hash_match"] is True
        assert receipt_from_a["agent_pubkey"] == agent_a.public_key

    def test_cross_agent_no_receipt_returns_none(self, tmp_path):
        agent_a = _make_agent(str(tmp_path / "a"), "agent_a")
        agent_b_dir = tmp_path / "b"
        agent_b_dir.mkdir(exist_ok=True)
        agent_b = _make_agent(str(agent_b_dir), "agent_b")

        # Agent A never installed this package
        result = agent_b._supply_chain.query_dependency_from_agent(
            other_ledger=agent_a._ledger,
            other_pubkey=agent_a.public_key,
            package="never-installed",
            version="0.0.1",
        )
        assert result is None

    def test_cross_agent_detects_compromised_via_peer_receipt(self, tmp_path):
        """Agent B learns from Agent A's receipt that a package was compromised."""
        agent_a = _make_agent(str(tmp_path / "a"), "agent_a")
        agent_b_dir = tmp_path / "b"
        agent_b_dir.mkdir(exist_ok=True)
        agent_b = _make_agent(str(agent_b_dir), "agent_b")

        clean_hash = _sha256("axios-clean")
        malicious_hash = _sha256("axios-with-backdoor")

        # Agent A recorded the compromised package (hash mismatch detected)
        agent_a.record_dependency(
            package="axios",
            version="1.14.1",
            registry="npm",
            source_hash=malicious_hash,
            expected_hash=clean_hash,
        )

        # Agent B queries Agent A's ledger
        receipt_from_a = agent_b._supply_chain.query_dependency_from_agent(
            other_ledger=agent_a._ledger,
            other_pubkey=agent_a.public_key,
            package="axios",
            version="1.14.1",
        )

        assert receipt_from_a is not None
        assert receipt_from_a["data"]["hash_match"] is False  # Agent B sees the ALERT
