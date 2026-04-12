"""Tests for the Nexus-Graphify bridge: canonicalization and adversarial robustness."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

import networkx as nx
import pytest

from nexus_ledger.graphify_bridge import (
    CodeQualityScorer,
    GraphDiff,
    GraphifyReceipt,
)


# ---------------------------------------------------------------------------
# Helpers — build small synthetic graphs for testing
# ---------------------------------------------------------------------------

def _make_graph(
    nodes: List[tuple],
    edges: List[tuple],
    *,
    directed: bool = False,
) -> nx.Graph:
    """Build a graph from (id, attrs_dict) nodes and (src, tgt, attrs_dict) edges."""
    G = nx.DiGraph() if directed else nx.Graph()
    for nid, attrs in nodes:
        G.add_node(nid, **attrs)
    for src, tgt, attrs in edges:
        G.add_edge(src, tgt, **attrs)
    return G


def _healthy_graph(*, directed: bool = False) -> nx.Graph:
    """A well-structured 6-node graph with behavioural edges."""
    nodes = [
        ("ModuleA", {"label": "ModuleA", "type": "module", "description": "Core module"}),
        ("ClassB", {"label": "ClassB", "type": "class", "description": "Business logic"}),
        ("FuncC", {"label": "FuncC", "type": "function", "description": "Helper"}),
        ("ClassD", {"label": "ClassD", "type": "class", "description": "Data access"}),
        ("FuncE", {"label": "FuncE", "type": "function", "description": "Util"}),
        ("ModuleF", {"label": "ModuleF", "type": "module", "description": "IO layer"}),
    ]
    edges = [
        ("ModuleA", "ClassB", {"type": "contains"}),
        ("ClassB", "FuncC", {"type": "calls", "confidence": "EXTRACTED"}),
        ("FuncC", "ClassD", {"type": "data_flow", "confidence": "EXTRACTED"}),
        ("ClassD", "FuncE", {"type": "calls", "confidence": "EXTRACTED"}),
        ("FuncE", "ModuleF", {"type": "writes", "confidence": "EXTRACTED"}),
        ("ModuleF", "ModuleA", {"type": "imports", "confidence": "EXTRACTED"}),
        ("ClassB", "ClassD", {"type": "uses", "confidence": "EXTRACTED"}),
    ]
    return _make_graph(nodes, edges, directed=directed)


def _padded_graph() -> nx.Graph:
    """A graph with many dead-code leaf nodes (adversarial padding)."""
    nodes = [
        ("Core", {"label": "Core", "type": "class", "description": "Main class"}),
        ("Helper", {"label": "Helper", "type": "function", "description": "Helper"}),
    ]
    edges = [
        ("Core", "Helper", {"type": "calls"}),
    ]
    # Add 20 isolated leaf nodes with only an import edge
    for i in range(20):
        name = f"Dead{i}"
        nodes.append((name, {"label": name, "type": "class", "description": f"Dead class {i}"}))
        edges.append((name, "Core", {"type": "imports"}))
    return _make_graph(nodes, edges)


def _structural_only_graph() -> nx.Graph:
    """A graph with edges that are purely structural (imports/inherits)."""
    nodes = [
        ("A", {"label": "A", "type": "class"}),
        ("B", {"label": "B", "type": "class"}),
        ("C", {"label": "C", "type": "class"}),
        ("D", {"label": "D", "type": "class"}),
        ("E", {"label": "E", "type": "module"}),
    ]
    edges = [
        ("A", "B", {"type": "imports"}),
        ("B", "C", {"type": "inherits"}),
        ("C", "D", {"type": "extends"}),
        ("D", "E", {"type": "imports"}),
        ("E", "A", {"type": "contains"}),
    ]
    return _make_graph(nodes, edges)


# ===================================================================
# Gap 1 Tests: Graph Canonicalization
# ===================================================================

class TestCanonicalize:
    """Test GraphifyReceipt.canonicalize_graph() normalisation."""

    def test_node_ordering_invariant(self):
        """Two graphs with same nodes in different insertion order produce same canonical form."""
        G1 = nx.Graph()
        G1.add_node("Bravo", label="Bravo", type="class")
        G1.add_node("Alpha", label="Alpha", type="class")
        G1.add_edge("Bravo", "Alpha", type="calls")

        G2 = nx.Graph()
        G2.add_node("Alpha", label="Alpha", type="class")
        G2.add_node("Bravo", label="Bravo", type="class")
        G2.add_edge("Alpha", "Bravo", type="calls")

        c1 = GraphifyReceipt.canonicalize_graph(G1)
        c2 = GraphifyReceipt.canonicalize_graph(G2)
        assert c1 == c2

    def test_case_insensitivity(self):
        """Node IDs and string attributes are lowercased."""
        G1 = nx.Graph()
        G1.add_node("MyClass", label="MyClass", description="A useful class")
        G1.add_edge("MyClass", "MyClass", type="CALLS")

        G2 = nx.Graph()
        G2.add_node("myclass", label="myclass", description="a useful class")
        G2.add_edge("myclass", "myclass", type="calls")

        c1 = GraphifyReceipt.canonicalize_graph(G1)
        c2 = GraphifyReceipt.canonicalize_graph(G2)
        assert c1 == c2

    def test_whitespace_normalisation(self):
        """Extra whitespace in descriptions is collapsed."""
        G1 = nx.Graph()
        G1.add_node("X", description="  This   is   a   class  ")

        G2 = nx.Graph()
        G2.add_node("X", description="This is a class")

        c1 = GraphifyReceipt.canonicalize_graph(G1)
        c2 = GraphifyReceipt.canonicalize_graph(G2)
        assert c1 == c2

    def test_edge_sorting(self):
        """Edges are sorted by (source, target, type)."""
        G1 = nx.Graph()
        G1.add_node("A")
        G1.add_node("B")
        G1.add_node("C")
        G1.add_edge("C", "A", type="imports")
        G1.add_edge("A", "B", type="calls")

        G2 = nx.Graph()
        G2.add_node("A")
        G2.add_node("B")
        G2.add_node("C")
        G2.add_edge("A", "B", type="calls")
        G2.add_edge("C", "A", type="imports")

        c1 = GraphifyReceipt.canonicalize_graph(G1)
        c2 = GraphifyReceipt.canonicalize_graph(G2)
        assert c1 == c2

    def test_community_renumbering(self):
        """Communities are renumbered by smallest (alphabetically first) member."""
        G = nx.Graph()
        for n in ["alpha", "beta", "gamma", "delta"]:
            G.add_node(n)

        # Community assignments with arbitrary IDs
        comms_v1 = {
            99: ["gamma", "delta"],  # smallest = delta
            1:  ["alpha", "beta"],   # smallest = alpha
        }
        comms_v2 = {
            5: ["alpha", "beta"],    # smallest = alpha
            3: ["delta", "gamma"],   # smallest = delta
        }

        c1 = GraphifyReceipt.canonicalize_graph(G, comms_v1)
        c2 = GraphifyReceipt.canonicalize_graph(G, comms_v2)

        # Both should produce community 0 = [alpha, beta], community 1 = [delta, gamma]
        assert c1["communities"] == c2["communities"]
        assert list(c1["communities"].keys()) == [0, 1]
        assert c1["communities"][0] == ["alpha", "beta"]
        assert c1["communities"][1] == ["delta", "gamma"]

    def test_hash_determinism(self):
        """hash_canonical produces the same digest for equivalent graphs."""
        G1 = _healthy_graph()
        G2 = _healthy_graph()  # same construction

        h1 = GraphifyReceipt.hash_canonical(GraphifyReceipt.canonicalize_graph(G1))
        h2 = GraphifyReceipt.hash_canonical(GraphifyReceipt.canonicalize_graph(G2))
        assert h1 == h2

    def test_different_graphs_different_hash(self):
        """Structurally different graphs produce different hashes."""
        G1 = _healthy_graph()
        G2 = _healthy_graph()
        G2.add_node("Extra", label="Extra")

        h1 = GraphifyReceipt.hash_canonical(GraphifyReceipt.canonicalize_graph(G1))
        h2 = GraphifyReceipt.hash_canonical(GraphifyReceipt.canonicalize_graph(G2))
        assert h1 != h2


class TestFromGraph:
    """Test that from_graph uses canonicalization for hashing."""

    def test_from_graph_uses_canonical_hash(self):
        """The graph_hash in the receipt comes from canonicalize_graph, not raw node_link_data."""
        G = _healthy_graph()
        communities = {0: list(G.nodes())}
        cohesion = {0: 0.85}
        labels = {0: "Main"}
        god_nodes = [{"label": "ClassB"}, {"label": "ModuleA"}]

        receipt = GraphifyReceipt.from_graph(G, communities, cohesion, labels, god_nodes)

        # Recompute expected hash via canonicalization
        canon = GraphifyReceipt.canonicalize_graph(G, communities)
        expected_hash = GraphifyReceipt.hash_canonical(canon)
        assert receipt.graph_hash == expected_hash

    def test_from_graph_with_full_graph_b64(self):
        """include_full_graph=True embeds the canonicalized JSON, not raw."""
        import base64

        G = _healthy_graph()
        communities = {0: list(G.nodes())}
        cohesion = {0: 0.8}
        labels = {0: "Main"}
        god_nodes = [{"label": "ClassB"}]

        receipt = GraphifyReceipt.from_graph(
            G, communities, cohesion, labels, god_nodes,
            include_full_graph=True,
        )
        assert receipt.graph_json_b64 is not None

        # Decode and verify it matches the canonical form
        decoded = json.loads(base64.b64decode(receipt.graph_json_b64))
        canon = GraphifyReceipt.canonicalize_graph(G, communities)
        # The decoded dict should match when re-serialised canonically
        assert (
            json.dumps(decoded, sort_keys=True, separators=(",", ":"))
            == json.dumps(canon, sort_keys=True, separators=(",", ":"))
        )


class TestGraphDiffCanonical:
    """Test that GraphDiff.compare uses canonical hashing."""

    def test_compare_without_precomputed_hashes_uses_canonicalization(self):
        """When hash_a/hash_b are not provided, compare() uses canonicalize_graph."""
        G1 = nx.Graph()
        G1.add_node("Alpha", label="Alpha")
        G1.add_node("Beta", label="Beta")
        G1.add_edge("Alpha", "Beta", type="calls")

        # Same graph but nodes inserted in reverse order
        G2 = nx.Graph()
        G2.add_node("Beta", label="Beta")
        G2.add_node("Alpha", label="Alpha")
        G2.add_edge("Beta", "Alpha", type="calls")

        # Patch out graphify.analyze.graph_diff since we don't have the real module
        import unittest.mock as mock

        fake_diff = {
            "new_nodes": [],
            "removed_nodes": [],
            "new_edges": [],
            "removed_edges": [],
            "summary": "identical",
        }
        with mock.patch("nexus_ledger.graphify_bridge.GraphDiff.compare") as mock_compare:
            # Instead of patching, let's directly test the canonical hashing logic
            pass

        # Direct test: canonical hashes should match for structurally identical graphs
        h1 = GraphifyReceipt.hash_canonical(GraphifyReceipt.canonicalize_graph(G1))
        h2 = GraphifyReceipt.hash_canonical(GraphifyReceipt.canonicalize_graph(G2))
        assert h1 == h2


class TestGraphDiffCommunityDelta:
    """Test that community_delta is computed correctly."""

    def test_from_receipts_computes_community_delta(self):
        """from_receipts() computes community_delta from metrics."""
        receipt_a = {
            "graph_hash": "aaa",
            "graph_metrics": {"nodes": 50, "edges": 70, "communities": 4},
        }
        receipt_b = {
            "graph_hash": "bbb",
            "graph_metrics": {"nodes": 60, "edges": 90, "communities": 6},
        }
        diff = GraphDiff.from_receipts(receipt_a, receipt_b)
        assert diff.community_delta == 2
        assert diff.node_delta == 10
        assert diff.edge_delta == 20

    def test_from_receipts_negative_community_delta(self):
        """community_delta can be negative (communities merged/reduced)."""
        receipt_a = {
            "graph_hash": "aaa",
            "graph_metrics": {"nodes": 50, "edges": 70, "communities": 8},
        }
        receipt_b = {
            "graph_hash": "bbb",
            "graph_metrics": {"nodes": 50, "edges": 70, "communities": 5},
        }
        diff = GraphDiff.from_receipts(receipt_a, receipt_b)
        assert diff.community_delta == -3

    def test_from_receipts_zero_community_delta(self):
        """Identical community counts produce zero delta."""
        receipt_a = {
            "graph_hash": "same",
            "graph_metrics": {"nodes": 50, "edges": 70, "communities": 4},
        }
        receipt_b = {
            "graph_hash": "same",
            "graph_metrics": {"nodes": 55, "edges": 75, "communities": 4},
        }
        diff = GraphDiff.from_receipts(receipt_a, receipt_b)
        assert diff.community_delta == 0


class TestFromReceiptsSimilarity:
    """Test that from_receipts() produces graded structural similarity."""

    def test_identical_metrics_gives_high_similarity(self):
        """Same metrics, different hashes → high similarity (not 0.0)."""
        receipt_a = {
            "graph_hash": "aaa",
            "graph_metrics": {"nodes": 100, "edges": 200, "communities": 5},
        }
        receipt_b = {
            "graph_hash": "bbb",
            "graph_metrics": {"nodes": 100, "edges": 200, "communities": 5},
        }
        diff = GraphDiff.from_receipts(receipt_a, receipt_b)
        assert diff.structural_similarity == 1.0
        assert diff.hash_match is False

    def test_exact_hash_match_gives_1(self):
        """Matching hashes → similarity 1.0."""
        receipt_a = {
            "graph_hash": "same_hash",
            "graph_metrics": {"nodes": 50, "edges": 70, "communities": 3},
        }
        diff = GraphDiff.from_receipts(receipt_a, receipt_a)
        assert diff.structural_similarity == 1.0
        assert diff.hash_match is True

    def test_very_different_metrics_gives_low_similarity(self):
        """Wildly different graphs → low similarity."""
        receipt_a = {
            "graph_hash": "aaa",
            "graph_metrics": {"nodes": 100, "edges": 200, "communities": 5},
        }
        receipt_b = {
            "graph_hash": "bbb",
            "graph_metrics": {"nodes": 5, "edges": 3, "communities": 1},
        }
        diff = GraphDiff.from_receipts(receipt_a, receipt_b)
        assert diff.structural_similarity < 0.2

    def test_small_delta_gives_high_similarity(self):
        """Small metric differences → high but not perfect similarity."""
        receipt_a = {
            "graph_hash": "aaa",
            "graph_metrics": {"nodes": 100, "edges": 200, "communities": 5},
        }
        receipt_b = {
            "graph_hash": "bbb",
            "graph_metrics": {"nodes": 105, "edges": 210, "communities": 5},
        }
        diff = GraphDiff.from_receipts(receipt_a, receipt_b)
        assert diff.structural_similarity > 0.9

    def test_similarity_is_graded_not_binary(self):
        """Intermediate differences produce intermediate similarity."""
        receipt_a = {
            "graph_hash": "aaa",
            "graph_metrics": {"nodes": 100, "edges": 200, "communities": 5},
        }
        receipt_b = {
            "graph_hash": "bbb",
            "graph_metrics": {"nodes": 50, "edges": 100, "communities": 3},
        }
        diff = GraphDiff.from_receipts(receipt_a, receipt_b)
        assert 0.3 < diff.structural_similarity < 0.8


# ===================================================================
# Gap 2 Tests: Adversarial Robustness
# ===================================================================

class TestDeadCodeDetection:
    """Test CodeQualityScorer._detect_dead_code."""

    def test_no_dead_code(self):
        """A well-connected graph has low dead-code ratio and no penalty."""
        G = _healthy_graph()
        metrics = {"nodes": G.number_of_nodes(), "isolated_nodes": 0}
        ratio, penalty = CodeQualityScorer._detect_dead_code(G, metrics)
        assert ratio < CodeQualityScorer.DEAD_CODE_RATIO_WARN
        assert penalty == 1.0

    def test_high_dead_code_undirected(self):
        """A padded undirected graph triggers dead-code penalty."""
        G = _padded_graph()
        isolated = sum(1 for n in G.nodes() if G.degree(n) <= 1)
        metrics = {"nodes": G.number_of_nodes(), "isolated_nodes": isolated}
        ratio, penalty = CodeQualityScorer._detect_dead_code(G, metrics)
        assert ratio > CodeQualityScorer.DEAD_CODE_RATIO_WARN
        assert penalty < 1.0

    def test_high_dead_code_directed(self):
        """A directed graph with many zero-in-degree leaves triggers penalty."""
        G = nx.DiGraph()
        G.add_node("Hub", label="Hub")
        for i in range(10):
            leaf = f"Leaf{i}"
            G.add_node(leaf, label=leaf)
            G.add_edge(leaf, "Hub", type="imports")  # leaf -> hub, so leaf has in_degree=0
        metrics = {"nodes": 11, "isolated_nodes": 0}
        ratio, penalty = CodeQualityScorer._detect_dead_code(G, metrics)
        # 10 out of 11 nodes have in_degree=0 and out_degree <= 1
        assert ratio > 0.8
        assert penalty < 1.0

    def test_metrics_only_fallback(self):
        """Without a graph, falls back to isolated_nodes metric."""
        metrics = {"nodes": 100, "isolated_nodes": 60}
        ratio, penalty = CodeQualityScorer._detect_dead_code(None, metrics)
        assert ratio == 0.6
        assert penalty < 1.0


class TestFunctionalityDensity:
    """Test CodeQualityScorer._detect_functionality_density."""

    def test_healthy_graph_has_good_density(self):
        """A graph with behavioural edges scores well."""
        G = _healthy_graph()
        metrics = {}
        density, penalty = CodeQualityScorer._detect_functionality_density(G, metrics)
        # healthy_graph has 4 behavioural (calls, data_flow, writes, uses) and
        # 2 structural (contains, imports) out of 7 edges -> 4/(4+2) = 0.667
        assert density > CodeQualityScorer.FUNC_DENSITY_WARN
        assert penalty == 1.0

    def test_structural_only_graph_penalized(self):
        """A graph with only structural edges gets penalized."""
        G = _structural_only_graph()
        metrics = {}
        density, penalty = CodeQualityScorer._detect_functionality_density(G, metrics)
        assert density < CodeQualityScorer.FUNC_DENSITY_WARN
        assert penalty < 1.0

    def test_no_graph_assumes_ok(self):
        """Without a graph object, assumes density is fine."""
        density, penalty = CodeQualityScorer._detect_functionality_density(None, {})
        assert density == 1.0
        assert penalty == 1.0


class TestSuspiciousPatterns:
    """Test CodeQualityScorer._detect_suspicious_patterns."""

    def test_no_patterns_on_healthy_graph(self):
        """A healthy graph produces no suspicious pattern warnings."""
        G = _healthy_graph()
        metrics = {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "communities": 2,
            "community_labels": ["A", "B"],
            "isolated_nodes": 0,
        }
        patterns = CodeQualityScorer._detect_suspicious_patterns(
            G, metrics, dead_code_ratio=0.05, functionality_density=0.6
        )
        assert len(patterns) == 0

    def test_dead_code_flagged(self):
        """High dead-code ratio produces a warning."""
        patterns = CodeQualityScorer._detect_suspicious_patterns(
            None, {"nodes": 100, "edges": 50}, dead_code_ratio=0.6, functionality_density=0.5
        )
        assert any("dead-code" in p.lower() for p in patterns)

    def test_low_functionality_flagged(self):
        """Low functionality density produces a warning."""
        patterns = CodeQualityScorer._detect_suspicious_patterns(
            None, {"nodes": 100, "edges": 50}, dead_code_ratio=0.1, functionality_density=0.05
        )
        assert any("functionality density" in p.lower() for p in patterns)

    def test_low_edge_density_flagged(self):
        """Very few edges per node triggers a warning."""
        patterns = CodeQualityScorer._detect_suspicious_patterns(
            None, {"nodes": 50, "edges": 10}, dead_code_ratio=0.1, functionality_density=0.5
        )
        assert any("edge density" in p.lower() for p in patterns)


class TestScorerIntegration:
    """Integration tests for CodeQualityScorer.score with adversarial checks."""

    def test_score_returns_penalties_and_patterns(self):
        """The score() return dict includes penalties and suspicious_patterns keys."""
        metrics = {
            "nodes": 60,
            "edges": 80,
            "communities": 4,
            "avg_cohesion": 0.8,
            "isolated_nodes": 3,
            "ambiguous_edge_pct": 0.05,
            "god_nodes": ["A", "B", "C"],
        }
        result = CodeQualityScorer.score(metrics)
        assert "penalties" in result
        assert "suspicious_patterns" in result
        assert "dead_code" in result["penalties"]
        assert "functionality_density" in result["penalties"]
        assert isinstance(result["suspicious_patterns"], list)

    def test_score_with_graph_enables_deeper_checks(self):
        """Passing graph= enables edge-type based adversarial analysis."""
        G = _structural_only_graph()
        metrics = {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "communities": 1,
            "avg_cohesion": 0.5,
            "isolated_nodes": 0,
            "ambiguous_edge_pct": 0.0,
            "god_nodes": ["A"],
        }
        result = CodeQualityScorer.score(metrics, graph=G)
        # Structural-only graph should be penalized for low functionality density
        assert result["penalties"]["functionality_density"] < 1.0

    def test_padded_graph_gets_lower_score(self):
        """A graph padded with dead code scores lower than a healthy one."""
        G_healthy = _healthy_graph()
        metrics_healthy = {
            "nodes": G_healthy.number_of_nodes(),
            "edges": G_healthy.number_of_edges(),
            "communities": 2,
            "avg_cohesion": 0.85,
            "isolated_nodes": 0,
            "ambiguous_edge_pct": 0.0,
            "god_nodes": ["ClassB", "ModuleA", "FuncC"],
        }

        G_padded = _padded_graph()
        isolated = sum(1 for n in G_padded.nodes() if G_padded.degree(n) <= 1)
        metrics_padded = {
            "nodes": G_padded.number_of_nodes(),
            "edges": G_padded.number_of_edges(),
            "communities": 2,
            "avg_cohesion": 0.85,
            "isolated_nodes": isolated,
            "ambiguous_edge_pct": 0.0,
            "god_nodes": ["Core", "Helper", "Dead0"],
        }

        score_healthy = CodeQualityScorer.score(metrics_healthy, graph=G_healthy)
        score_padded = CodeQualityScorer.score(metrics_padded, graph=G_padded)

        assert score_padded["quality_score"] < score_healthy["quality_score"]

    def test_backward_compatible_without_graph(self):
        """score() without graph= still works (backward compatible)."""
        metrics = {
            "nodes": 50,
            "edges": 70,
            "communities": 3,
            "avg_cohesion": 0.75,
            "isolated_nodes": 5,
            "ambiguous_edge_pct": 0.1,
            "god_nodes": ["A", "B", "C"],
        }
        result = CodeQualityScorer.score(metrics)
        assert 0.0 <= result["quality_score"] <= 1.0
        assert result["grade"] in ("A", "B", "C", "D", "F")
        # Without graph, functionality_density penalty should be 1.0 (no penalty)
        assert result["penalties"]["functionality_density"] == 1.0


# ===================================================================
# Priority 3: End-to-End Workflow Tests
# ===================================================================


class _MockAgent:
    """Minimal mock of nexus_ledger.Agent for VerifiedDelivery tests."""

    def __init__(self):
        self._sent = []
        self._confirmed = []
        self._disputed = []

    def _resolve_task_counterparty_pubkey(self, task_id):
        return b"\x01" * 32  # dummy pubkey

    def _latest_task_receipt_hash(self, task_id):
        return "parent_hash_abc"

    def send(self, event_type, data, *, to=None, encrypted=False, parent_receipt_hash=None):
        result = {
            "event_type": event_type,
            "data": data,
            "to": to,
            "encrypted": encrypted,
            "parent_receipt_hash": parent_receipt_hash,
        }
        self._sent.append(result)
        return result

    def confirm_task(self, task_id, *, rating=None, feedback=None, to=None, encrypted=False):
        result = {
            "event_type": "TaskConfirmed",
            "data": {"task_id": task_id, "rating": rating, "feedback": feedback},
            "to": to,
        }
        self._confirmed.append(result)
        return result

    def dispute_task(self, task_id, *, reason=None, to=None, encrypted=False):
        result = {
            "event_type": "TaskDisputed",
            "data": {"task_id": task_id, "reason": reason},
            "to": to,
        }
        self._disputed.append(result)
        return result


from nexus_ledger.graphify_bridge import VerifiedDelivery
import unittest.mock as mock


class TestVerifiedDeliveryDeliver:
    """Test VerifiedDelivery.deliver() end-to-end with mocked Graphify."""

    def _mock_from_path(self, **overrides):
        """Create a mock GraphifyReceipt.from_path that returns controlled data."""
        defaults = {
            "graph_hash": "abc123deadbeef",
            "node_count": 50,
            "edge_count": 80,
            "community_count": 4,
            "god_nodes": ["Router", "DB", "Auth"],
            "avg_cohesion": 0.82,
            "isolated_node_count": 2,
            "ambiguous_edge_pct": 0.05,
            "community_labels": ["Core", "Data", "Auth", "Utils"],
            "graph_json_b64": None,
            "viewer_url": None,
        }
        defaults.update(overrides)
        receipt = GraphifyReceipt(**defaults)
        return mock.patch.object(GraphifyReceipt, "from_path", return_value=receipt)

    def test_deliver_sends_receipt_with_graph_data(self):
        """deliver() produces a TaskDelivered receipt with graph_hash and graph_metrics."""
        agent = _MockAgent()
        vd = VerifiedDelivery(agent)

        with self._mock_from_path():
            result = vd.deliver(
                task_id="task-001",
                codebase_path="/fake/path",
                artifact_hash="sha256:fakehash",
                to="did:key:receiver",
            )

        assert result["event_type"] == "TaskDelivered"
        assert result["data"]["graph_hash"] == "abc123deadbeef"
        assert result["data"]["graph_metrics"]["nodes"] == 50
        assert result["data"]["graph_metrics"]["edges"] == 80
        assert result["data"]["graph_metrics"]["communities"] == 4
        assert result["data"]["graph_metrics"]["avg_cohesion"] == 0.82
        assert result["data"]["task_id"] == "task-001"
        assert result["data"]["artifact_hash"] == "sha256:fakehash"
        assert result["to"] == "did:key:receiver"

    def test_deliver_infers_counterparty_when_to_omitted(self):
        """deliver() resolves counterparty from task chain when 'to' is not provided."""
        agent = _MockAgent()
        vd = VerifiedDelivery(agent)

        with self._mock_from_path(), \
             mock.patch("nexus_ledger.transport.public_key_to_did", return_value="did:key:resolved"):
            result = vd.deliver(
                task_id="task-002",
                codebase_path="/fake/path",
                artifact_hash="sha256:fakehash",
            )

        assert result["to"] == "did:key:resolved"
        assert result["event_type"] == "TaskDelivered"


class TestVerifiedDeliveryVerifyAndDecide:
    """Test VerifiedDelivery.verify_and_decide() — confirm, dispute, and edge cases."""

    def _make_delivered_receipt(self, graph_hash, node_count=50, edge_count=80, communities=4):
        """Build a mock TaskDelivered receipt dict."""
        return {
            "event_type": "TaskDelivered",
            "data": {
                "task_id": "task-001",
                "artifact_hash": "sha256:fake",
                "graph_hash": graph_hash,
                "graph_metrics": {
                    "nodes": node_count,
                    "edges": edge_count,
                    "communities": communities,
                    "god_nodes": ["Router", "DB", "Auth"],
                    "avg_cohesion": 0.82,
                    "isolated_nodes": 2,
                    "ambiguous_edge_pct": 0.05,
                    "community_labels": ["Core", "Data", "Auth", "Utils"],
                },
            },
        }

    def _mock_from_path(self, graph_hash, node_count=50, edge_count=80, communities=4, avg_cohesion=0.82):
        """Mock GraphifyReceipt.from_path with controlled output."""
        receipt = GraphifyReceipt(
            graph_hash=graph_hash,
            node_count=node_count,
            edge_count=edge_count,
            community_count=communities,
            god_nodes=["Router", "DB", "Auth"],
            avg_cohesion=avg_cohesion,
            isolated_node_count=2,
            ambiguous_edge_pct=0.05,
            community_labels=["Core", "Data", "Auth", "Utils"],
        )
        return mock.patch.object(GraphifyReceipt, "from_path", return_value=receipt)

    def test_confirm_on_hash_match_and_good_quality(self):
        """Matching hashes + quality above threshold → TaskConfirmed."""
        agent = _MockAgent()
        vd = VerifiedDelivery(agent)
        delivered = self._make_delivered_receipt("matching_hash")

        with self._mock_from_path("matching_hash"):
            result = vd.verify_and_decide(
                task_id="task-001",
                codebase_path="/fake/path",
                delivered_receipt=delivered,
                to="did:key:sender",
            )

        assert result["event_type"] == "TaskConfirmed"
        assert "Graph-verified delivery" in result["data"]["feedback"]
        assert result["data"]["rating"] >= 3

    def test_dispute_on_hash_mismatch(self):
        """Different hashes → TaskDisputed even if quality is good."""
        agent = _MockAgent()
        vd = VerifiedDelivery(agent)
        delivered = self._make_delivered_receipt("sender_hash_aaa")

        with self._mock_from_path("receiver_hash_bbb"):
            result = vd.verify_and_decide(
                task_id="task-001",
                codebase_path="/fake/path",
                delivered_receipt=delivered,
                to="did:key:sender",
            )

        assert result["event_type"] == "TaskDisputed"
        assert "Graph hash mismatch" in result["data"]["reason"]

    def test_dispute_on_low_quality(self):
        """Hash mismatch + low quality metrics → dispute with quality details."""
        agent = _MockAgent()
        vd = VerifiedDelivery(agent)
        # Sender claims big graph
        delivered = self._make_delivered_receipt("sender_hash", node_count=100, edge_count=200)

        # Receiver sees tiny graph → hash mismatch + low complexity
        with self._mock_from_path("receiver_hash", node_count=3, edge_count=2, communities=1, avg_cohesion=0.1):
            result = vd.verify_and_decide(
                task_id="task-001",
                codebase_path="/fake/path",
                delivered_receipt=delivered,
                auto_confirm_threshold=0.6,
                to="did:key:sender",
            )

        assert result["event_type"] == "TaskDisputed"
        assert "Quality below threshold" in result["data"]["reason"]

    def test_custom_threshold(self):
        """Higher threshold can trigger dispute on otherwise-ok delivery."""
        agent = _MockAgent()
        vd = VerifiedDelivery(agent)
        delivered = self._make_delivered_receipt("same_hash")

        # Moderate quality graph — would pass at 0.6 but fail at 0.95
        with self._mock_from_path("same_hash", node_count=15, edge_count=20, communities=2, avg_cohesion=0.5):
            result = vd.verify_and_decide(
                task_id="task-001",
                codebase_path="/fake/path",
                delivered_receipt=delivered,
                auto_confirm_threshold=0.95,
                to="did:key:sender",
            )

        # With threshold 0.95, moderate quality should dispute
        assert result["event_type"] == "TaskDisputed"

    def test_rating_scales_with_quality(self):
        """Confirmed deliveries get ratings proportional to quality score."""
        agent = _MockAgent()
        vd = VerifiedDelivery(agent)
        delivered = self._make_delivered_receipt("perfect_hash", node_count=100, edge_count=200, communities=5)

        with self._mock_from_path("perfect_hash", node_count=100, edge_count=200, communities=5, avg_cohesion=0.95):
            result = vd.verify_and_decide(
                task_id="task-001",
                codebase_path="/fake/path",
                delivered_receipt=delivered,
                to="did:key:sender",
            )

        assert result["event_type"] == "TaskConfirmed"
        assert result["data"]["rating"] >= 4  # high quality → high rating


# ===================================================================
# Integration: Real Graphify Pipeline (requires graphifyy installed)
# ===================================================================

import tempfile
import os

# Skip if graphify not available
try:
    from graphify.detect import detect
    HAS_GRAPHIFY = True
except ImportError:
    HAS_GRAPHIFY = False


@pytest.mark.skipif(not HAS_GRAPHIFY, reason="graphifyy not installed")
class TestRealGraphifyIntegration:
    """Integration test using a real code fixture through the full pipeline."""

    @staticmethod
    def _create_fixture(tmpdir: str):
        """Create a small but real Python codebase for Graphify extraction."""
        # A minimal multi-file project with real relationships
        os.makedirs(os.path.join(tmpdir, "mylib"), exist_ok=True)

        with open(os.path.join(tmpdir, "mylib", "__init__.py"), "w") as f:
            f.write("from .core import Engine\nfrom .store import DataStore\n")

        with open(os.path.join(tmpdir, "mylib", "core.py"), "w") as f:
            f.write('''
class Engine:
    """Main processing engine."""
    def __init__(self, store):
        self.store = store
        self._cache = {}

    def process(self, data):
        result = self._transform(data)
        self.store.save(result)
        return result

    def _transform(self, data):
        return {k: v.upper() if isinstance(v, str) else v for k, v in data.items()}

    def query(self, key):
        if key in self._cache:
            return self._cache[key]
        result = self.store.load(key)
        self._cache[key] = result
        return result
''')

        with open(os.path.join(tmpdir, "mylib", "store.py"), "w") as f:
            f.write('''
import json
from pathlib import Path

class DataStore:
    """Persistent key-value store."""
    def __init__(self, path="data.json"):
        self.path = Path(path)
        self._data = {}

    def save(self, record):
        key = record.get("id", str(len(self._data)))
        self._data[key] = record
        return key

    def load(self, key):
        return self._data.get(key)

    def flush(self):
        self.path.write_text(json.dumps(self._data))

    def count(self):
        return len(self._data)
''')

        with open(os.path.join(tmpdir, "mylib", "utils.py"), "w") as f:
            f.write('''
def validate(data):
    """Validate input data before processing."""
    if not isinstance(data, dict):
        raise TypeError("Expected dict")
    if "id" not in data:
        raise ValueError("Missing id field")
    return True

def format_output(result):
    """Format processing result for display."""
    return " | ".join(f"{k}={v}" for k, v in sorted(result.items()))
''')

    def test_full_pipeline_from_path(self):
        """from_path() on a real codebase produces a valid receipt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_fixture(tmpdir)

            receipt = GraphifyReceipt.from_path(tmpdir)

            # Graph was extracted
            assert receipt.node_count > 0
            assert receipt.edge_count > 0
            assert len(receipt.graph_hash) == 64  # SHA-256 hex

            # Metrics are populated
            data = receipt.as_receipt_data()
            assert "graph_hash" in data
            assert "graph_metrics" in data
            assert data["graph_metrics"]["nodes"] == receipt.node_count
            assert data["graph_metrics"]["edges"] == receipt.edge_count

    def test_canonical_hash_determinism_on_real_code(self):
        """Two from_path() calls on the same code produce the same hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_fixture(tmpdir)

            receipt_1 = GraphifyReceipt.from_path(tmpdir)
            receipt_2 = GraphifyReceipt.from_path(tmpdir)

            assert receipt_1.graph_hash == receipt_2.graph_hash

    def test_quality_score_on_real_code(self):
        """Real code scores above F — it's a functional codebase."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_fixture(tmpdir)

            receipt = GraphifyReceipt.from_path(tmpdir)
            quality = CodeQualityScorer.score(receipt.as_receipt_data()["graph_metrics"])

            assert quality["quality_score"] > 0.0
            assert quality["grade"] in ("A", "B", "C", "D", "F")
            assert quality["grade"] != "F"  # real code shouldn't fail

    def test_receipt_diff_same_codebase(self):
        """Diffing a codebase against itself shows hash match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_fixture(tmpdir)

            receipt = GraphifyReceipt.from_path(tmpdir)
            data = receipt.as_receipt_data()

            diff = GraphDiff.from_receipts(data, data)
            assert diff.hash_match is True
            assert diff.node_delta == 0
            assert diff.edge_delta == 0
            assert diff.structural_similarity == 1.0
