"""Nexus-Graphify bridge: knowledge graph receipts for code delivery trust."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import networkx as nx


# ---------------------------------------------------------------------------
# GraphifyReceipt — wraps Graphify output as a Nexus receipt payload
# ---------------------------------------------------------------------------

@dataclass
class GraphifyReceipt:
    """Wraps a Graphify knowledge graph as structured receipt payload data.

    Embeds into the ``data`` dict of a TaskDelivered receipt alongside
    the existing artifact_hash field. The graph_hash is a SHA-256 of
    the canonicalized graph (via canonicalize_graph()), enabling
    independent verification even across LLM extraction variance.
    """

    graph_hash: str                          # SHA-256 hex of canonicalized graph
    node_count: int
    edge_count: int
    community_count: int
    god_nodes: List[str]                     # labels of top-5 most-connected entities
    avg_cohesion: float                      # mean community cohesion score
    isolated_node_count: int                 # nodes with degree == 0 (truly disconnected)
    ambiguous_edge_pct: float                # fraction of AMBIGUOUS-confidence edges
    community_labels: List[str]              # human-readable community names
    graph_json_b64: Optional[str] = None     # optional: base64-encoded full graph.json
    viewer_url: Optional[str] = None         # path/URL to Graphify HTML visualization

    @staticmethod
    def canonicalize_graph(G: nx.Graph, communities: Optional[Dict[int, List[str]]] = None) -> Dict[str, Any]:
        """Normalize a Graphify knowledge graph for deterministic hashing.

        LLM-based extraction can produce slightly different graphs on the same
        code (different node ordering, edge descriptions, etc.).  This method
        produces a canonical dict that will hash identically regardless of
        surface-level ordering/formatting differences.

        Normalization steps:
            1. Lowercase all node identifiers for comparison.
            2. Sort nodes alphabetically by lowercased id.
            3. Strip whitespace/formatting from description attributes.
            4. Sort edges by (source, target, type) tuple (all lowercased).
            5. Renumber community assignments so that the community whose
               smallest (alphabetically first) member sorts first gets id 0,
               the next gets id 1, etc.

        Args:
            G: the NetworkX graph to canonicalize.
            communities: optional {community_id: [node_ids]} mapping.  If
                provided, the renumbered communities are included in the
                canonical output under ``"communities"``.

        Returns:
            A plain dict with ``"nodes"``, ``"edges"``, and optionally
            ``"communities"`` that can be JSON-serialised deterministically.
        """
        # -- Nodes --
        canon_nodes: List[Dict[str, Any]] = []
        for nid in G.nodes():
            attrs = dict(G.nodes[nid])
            canon = {"id": str(nid).lower()}
            for key, val in sorted(attrs.items()):
                if isinstance(val, str):
                    canon[key.lower()] = " ".join(val.split()).lower()
                else:
                    canon[key.lower()] = val
            canon_nodes.append(canon)
        canon_nodes.sort(key=lambda n: n["id"])

        # -- Edges --
        is_directed = isinstance(G, (nx.DiGraph, nx.MultiDiGraph))
        canon_edges: List[Dict[str, Any]] = []
        for src, tgt, attrs in G.edges(data=True):
            src_low = str(src).lower()
            tgt_low = str(tgt).lower()
            # For undirected graphs, normalise edge direction so that the
            # alphabetically smaller node is always "source".
            if not is_directed and src_low > tgt_low:
                src_low, tgt_low = tgt_low, src_low
            edge: Dict[str, Any] = {
                "source": src_low,
                "target": tgt_low,
                "type": str(attrs.get("type", attrs.get("relation", ""))).strip().lower(),
            }
            for key, val in sorted(attrs.items()):
                lk = key.lower()
                if lk in ("type", "relation"):
                    continue  # already captured
                if isinstance(val, str):
                    edge[lk] = " ".join(val.split()).lower()
                else:
                    edge[lk] = val
            canon_edges.append(edge)
        canon_edges.sort(key=lambda e: (e["source"], e["target"], e["type"]))

        result: Dict[str, Any] = {"nodes": canon_nodes, "edges": canon_edges}

        # -- Communities (renumber by alphabetically smallest member) --
        if communities is not None:
            ranked: List[tuple] = []
            for cid, members in communities.items():
                lowest = min((str(m).lower() for m in members), default="")
                ranked.append((lowest, cid, members))
            ranked.sort(key=lambda t: t[0])
            renumbered: Dict[int, List[str]] = {}
            for new_id, (_, _old_id, members) in enumerate(ranked):
                renumbered[new_id] = sorted(str(m).lower() for m in members)
            result["communities"] = renumbered

        return result

    @staticmethod
    def hash_canonical(canonical_dict: Dict[str, Any]) -> str:
        """SHA-256 hex digest of a canonical graph dict."""
        canonical_json = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    @staticmethod
    def from_graph(
        G: nx.Graph,
        communities: Dict[int, List[str]],
        cohesion_scores: Dict[int, float],
        community_labels: Dict[int, str],
        god_node_list: List[Dict],
        *,
        include_full_graph: bool = False,
        viewer_url: Optional[str] = None,
    ) -> "GraphifyReceipt":
        """Build a GraphifyReceipt from Graphify pipeline outputs.

        Args:
            G: NetworkX graph from graphify.build_from_json()
            communities: {community_id: [node_ids]} from graphify.cluster()
            cohesion_scores: {community_id: float} from graphify.score_all()
            community_labels: {community_id: label_str}
            god_node_list: from graphify.god_nodes()
            include_full_graph: if True, embed the full graph.json (base64)
            viewer_url: optional path/URL to the Graphify HTML visualization
        """
        # Canonicalize graph for deterministic hashing across LLM runs
        canon = GraphifyReceipt.canonicalize_graph(G, communities)
        graph_hash = GraphifyReceipt.hash_canonical(canon)

        # Confidence breakdown
        confidences = [d.get("confidence", "EXTRACTED") for _, _, d in G.edges(data=True)]
        total_edges = len(confidences) or 1
        ambiguous_pct = round(confidences.count("AMBIGUOUS") / total_edges, 4)

        # Isolated nodes: truly disconnected (degree 0) only.
        # Leaf nodes (degree 1) are normal code patterns (utilities, helpers)
        # and should not be penalized as "isolated".
        isolated = sum(1 for n in G.nodes() if G.degree(n) == 0)

        # Average cohesion
        avg_cohesion = round(
            sum(cohesion_scores.values()) / max(len(cohesion_scores), 1), 4
        )

        graph_b64 = None
        if include_full_graph:
            import base64
            canon_json = json.dumps(canon, sort_keys=True, separators=(",", ":"))
            graph_b64 = base64.b64encode(canon_json.encode("utf-8")).decode("ascii")

        return GraphifyReceipt(
            graph_hash=graph_hash,
            node_count=G.number_of_nodes(),
            edge_count=G.number_of_edges(),
            community_count=len(communities),
            god_nodes=[n["label"] for n in god_node_list[:5]],
            avg_cohesion=avg_cohesion,
            isolated_node_count=isolated,
            ambiguous_edge_pct=ambiguous_pct,
            community_labels=[community_labels.get(cid, f"Community {cid}") for cid in sorted(communities.keys())],
            graph_json_b64=graph_b64,
            viewer_url=viewer_url,
        )

    @staticmethod
    def from_path(
        codebase_path: str,
        *,
        include_full_graph: bool = False,
    ) -> "GraphifyReceipt":
        """Convenience: run the full Graphify pipeline on a directory and build the receipt.

        Imports graphify at call time so the bridge module doesn't hard-depend
        on graphifyy at import time.
        """
        from graphify.detect import detect
        from graphify.extract import extract
        from graphify.build import build_from_json
        from graphify.cluster import cluster, score_all
        from graphify.analyze import god_nodes

        root = Path(codebase_path).resolve()
        detection = detect(root)
        files = [Path(f) for f in detection.get("files", {}).get("code", [])]
        extraction = extract(files)
        G = build_from_json(extraction)
        communities = cluster(G)
        cohesion_scores = score_all(G, communities)
        god_node_list = god_nodes(G)

        # Generate community labels (first god node label per community, or "Community N")
        comm_labels: Dict[int, str] = {}
        for cid, nodes in communities.items():
            degrees = {n: G.degree(n) for n in nodes}
            top = max(degrees, key=degrees.get) if degrees else nodes[0]
            comm_labels[cid] = G.nodes[top].get("label", f"Community {cid}")

        # Detect Graphify HTML visualization for one-click audit
        html_viz = root / "graphify-out" / "graph.html"
        viz_url: Optional[str] = str(html_viz) if html_viz.exists() else None

        return GraphifyReceipt.from_graph(
            G, communities, cohesion_scores, comm_labels, god_node_list,
            include_full_graph=include_full_graph,
            viewer_url=viz_url,
        )

    def as_receipt_data(self) -> Dict[str, Any]:
        """Return a dict suitable for merging into TaskDelivered.as_data().

        Usage:
            delivered_data = typed.as_data()
            delivered_data["graph_hash"] = receipt.graph_hash
            delivered_data["graph_metrics"] = receipt.as_receipt_data()["graph_metrics"]
        """
        payload: Dict[str, Any] = {
            "graph_hash": self.graph_hash,
            "graph_metrics": {
                "nodes": self.node_count,
                "edges": self.edge_count,
                "communities": self.community_count,
                "god_nodes": self.god_nodes,
                "avg_cohesion": self.avg_cohesion,
                "isolated_nodes": self.isolated_node_count,
                "ambiguous_edge_pct": self.ambiguous_edge_pct,
                "community_labels": self.community_labels,
            },
        }
        if self.graph_json_b64:
            payload["graph_json_b64"] = self.graph_json_b64
        if self.viewer_url:
            payload["viewer_url"] = self.viewer_url
        return payload


# ---------------------------------------------------------------------------
# GraphDiff — compares before/after knowledge graphs
# ---------------------------------------------------------------------------

@dataclass
class GraphDiff:
    """Result of comparing two Graphify knowledge graphs.

    Used by Agent B to verify a delivery against their own independent
    extraction, or to compare successive deliveries over time.
    """

    hash_match: bool                         # do the graph_hash values match?
    node_delta: int                           # new.nodes - old.nodes (positive = growth)
    edge_delta: int                           # new.edges - old.edges
    community_delta: int                      # new.communities - old.communities
    new_nodes: List[Dict[str, str]]           # [{"id": ..., "label": ...}]
    removed_nodes: List[Dict[str, str]]
    new_edges: List[Dict[str, str]]
    removed_edges: List[Dict[str, str]]
    structural_similarity: float              # 0.0-1.0 (Jaccard when graphs available, metric ratio estimate otherwise)
    summary: str                              # human-readable one-liner

    @staticmethod
    def compare(
        graph_a: nx.Graph,
        graph_b: nx.Graph,
        *,
        hash_a: str = "",
        hash_b: str = "",
        communities_a: Optional[Dict[int, List[str]]] = None,
        communities_b: Optional[Dict[int, List[str]]] = None,
    ) -> "GraphDiff":
        """Compare two NetworkX graphs and produce a structured diff.

        Delegates to graphify.analyze.graph_diff for node/edge deltas,
        then layers on hash comparison and structural similarity.

        Args:
            graph_a: the "before" or "sender's" graph
            graph_b: the "after" or "receiver's" graph
            hash_a: optional pre-computed graph_hash for A
            hash_b: optional pre-computed graph_hash for B
            communities_a: optional community dict for graph A
            communities_b: optional community dict for graph B
        """
        from graphify.analyze import graph_diff

        raw = graph_diff(graph_a, graph_b)

        nodes_a = set(graph_a.nodes())
        nodes_b = set(graph_b.nodes())
        union = nodes_a | nodes_b
        intersection = nodes_a & nodes_b
        jaccard = len(intersection) / max(len(union), 1)

        # Hash comparison (uses canonicalization to tolerate LLM variance)
        if hash_a and hash_b:
            hashes_match = hash_a == hash_b
        else:
            def _hash(G: nx.Graph) -> str:
                canon = GraphifyReceipt.canonicalize_graph(G)
                return GraphifyReceipt.hash_canonical(canon)
            hashes_match = _hash(graph_a) == _hash(graph_b)

        # Community delta: compute from community dicts when available
        if communities_a is not None and communities_b is not None:
            comm_delta = len(communities_b) - len(communities_a)
        else:
            comm_delta = 0

        return GraphDiff(
            hash_match=hashes_match,
            node_delta=graph_b.number_of_nodes() - graph_a.number_of_nodes(),
            edge_delta=graph_b.number_of_edges() - graph_a.number_of_edges(),
            community_delta=comm_delta,
            new_nodes=raw["new_nodes"],
            removed_nodes=raw["removed_nodes"],
            new_edges=raw["new_edges"],
            removed_edges=raw["removed_edges"],
            structural_similarity=round(jaccard, 4),
            summary=raw["summary"],
        )

    @staticmethod
    def _estimate_similarity(
        nodes_a: int, nodes_b: int,
        edges_a: int, edges_b: int,
        comms_a: int, comms_b: int,
    ) -> float:
        """Estimate structural similarity from metric deltas alone.

        Uses a weighted average of per-metric similarity ratios:
          - node similarity:      min(a,b) / max(a,b)   weight 0.4
          - edge similarity:      min(a,b) / max(a,b)   weight 0.4
          - community similarity: min(a,b) / max(a,b)   weight 0.2

        Returns 1.0 for identical metrics, degrades smoothly as
        counts diverge. Not as precise as Jaccard on actual node sets,
        but vastly better than binary 0/1.
        """
        def _ratio(a: int, b: int) -> float:
            if a == 0 and b == 0:
                return 1.0
            if a == 0 or b == 0:
                return 0.0
            return min(a, b) / max(a, b)

        return round(
            0.4 * _ratio(nodes_a, nodes_b)
            + 0.4 * _ratio(edges_a, edges_b)
            + 0.2 * _ratio(comms_a, comms_b),
            4,
        )

    @staticmethod
    def from_receipts(
        receipt_a: Dict[str, Any],
        receipt_b: Dict[str, Any],
    ) -> "GraphDiff":
        """Quick diff from two GraphifyReceipt.as_receipt_data() dicts.

        Does NOT require the full graphs — only compares metrics.
        For full structural diff, use compare() with actual NetworkX graphs.

        structural_similarity is estimated from metric ratios when hashes
        don't match (graded 0.0-1.0), or 1.0 on exact hash match.
        """
        ma = receipt_a.get("graph_metrics", {})
        mb = receipt_b.get("graph_metrics", {})
        ha = receipt_a.get("graph_hash", "")
        hb = receipt_b.get("graph_hash", "")

        nodes_a, nodes_b = ma.get("nodes", 0), mb.get("nodes", 0)
        edges_a, edges_b = ma.get("edges", 0), mb.get("edges", 0)
        comms_a, comms_b = ma.get("communities", 0), mb.get("communities", 0)

        node_delta = nodes_b - nodes_a
        edge_delta = edges_b - edges_a
        comm_delta = comms_b - comms_a

        hash_match = (ha == hb and ha != "")
        if hash_match:
            similarity = 1.0
        else:
            similarity = GraphDiff._estimate_similarity(
                nodes_a, nodes_b, edges_a, edges_b, comms_a, comms_b,
            )

        parts = []
        if node_delta: parts.append(f"{node_delta:+d} nodes")
        if edge_delta: parts.append(f"{edge_delta:+d} edges")
        if comm_delta: parts.append(f"{comm_delta:+d} communities")

        return GraphDiff(
            hash_match=hash_match,
            node_delta=node_delta,
            edge_delta=edge_delta,
            community_delta=comm_delta,
            new_nodes=[],
            removed_nodes=[],
            new_edges=[],
            removed_edges=[],
            structural_similarity=similarity,
            summary=", ".join(parts) if parts else "identical metrics",
        )


# ---------------------------------------------------------------------------
# CodeQualityScorer — extracts quality metrics from Graphify for trust
# ---------------------------------------------------------------------------

class CodeQualityScorer:
    """Extracts a 0.0-1.0 quality score from Graphify output.

    This score feeds into Nexus Ledger's TrustScorer as an additional
    signal beyond delivery volume, on-time rate, and ratings.

    Scoring formula (weights sum to 1.0):
        0.25 * cohesion_score     -- well-structured communities
        0.20 * low_isolation      -- few orphaned nodes
        0.20 * low_ambiguity      -- confident edge extraction
        0.15 * structural_match   -- sender/receiver graphs agree
        0.10 * complexity_norm    -- non-trivial graph (not hello-world)
        0.10 * god_node_health    -- no single node dominates

    Adversarial penalty factors (applied as multipliers to the raw score):
        dead_code_penalty         -- penalises high ratio of isolated leaf
                                     nodes with no incoming edges
        functionality_density     -- penalises graphs dominated by pure
                                     structural edges (imports, inherits)
                                     rather than behavioural edges (calls,
                                     data_flow, uses, reads, writes)
    """

    WEIGHTS = {
        "cohesion":          0.25,
        "low_isolation":     0.20,
        "low_ambiguity":     0.20,
        "structural_match":  0.15,
        "complexity":        0.10,
        "god_node_health":   0.10,
    }

    # Edge types considered *behavioural* (actual functionality).
    BEHAVIORAL_EDGE_TYPES = frozenset({
        "calls", "invokes", "data_flow", "dataflow", "uses",
        "reads", "writes", "emits", "subscribes", "triggers",
        "sends", "receives", "mutates", "returns",
    })

    # Edge types considered purely *structural* (not evidence of behaviour).
    STRUCTURAL_EDGE_TYPES = frozenset({
        "imports", "inherits", "extends", "implements",
        "contains", "defines", "declares", "belongs_to",
        "part_of", "has_member",
    })

    # Thresholds that trigger suspicious-pattern flags.
    #
    # DEAD_CODE_RATIO_WARN is set at 50% rather than the original 25% because
    # LLM-based extraction (Graphify) naturally produces ~40-43% isolated/leaf
    # nodes due to missed connections — these are extraction artifacts, not
    # actual dead code.  A 50% threshold still catches genuine dead-code
    # padding attacks (which typically show 60%+ leaf nodes) while avoiding
    # false penalties on real codebases processed through LLM extraction.
    DEAD_CODE_RATIO_WARN = 0.50     # > 50 % zero-in-degree leaf nodes
    FUNC_DENSITY_WARN = 0.15        # < 15 % behavioural edges

    @classmethod
    def _detect_dead_code(
        cls,
        G: Optional[nx.Graph],
        graph_metrics: Dict[str, Any],
    ) -> tuple:
        """Return (dead_code_ratio, penalty_multiplier).

        dead_code_ratio: fraction of nodes with in-degree 0 and out-degree <= 1.
        penalty_multiplier: 1.0 (no penalty) down to 0.5 (heavy penalty).
        """
        if G is not None and isinstance(G, (nx.DiGraph, nx.MultiDiGraph)):
            # Precise: use actual in-degree from directed graph
            zero_in_leaves = sum(
                1 for n in G.nodes()
                if G.in_degree(n) == 0 and G.out_degree(n) <= 1
            )
            total = G.number_of_nodes() or 1
        elif G is not None:
            # Undirected graph: approximate — degree-1 nodes with no
            # neighbour that points back to them.  Fall back to simple
            # degree-based heuristic.
            zero_in_leaves = sum(
                1 for n in G.nodes()
                if G.degree(n) <= 1
            )
            total = G.number_of_nodes() or 1
        else:
            # No full graph — use metrics-only heuristic
            isolated = graph_metrics.get("isolated_nodes", 0)
            total = graph_metrics.get("nodes", 0) or 1
            zero_in_leaves = isolated

        ratio = zero_in_leaves / total
        # Linear penalty: ratio above threshold reduces multiplier toward 0.5
        if ratio > cls.DEAD_CODE_RATIO_WARN:
            excess = min(ratio - cls.DEAD_CODE_RATIO_WARN, 0.5)
            penalty = 1.0 - excess  # at 75 % ratio => penalty = 0.5
        else:
            penalty = 1.0
        return round(ratio, 4), round(penalty, 4)

    @classmethod
    def _detect_functionality_density(
        cls,
        G: Optional[nx.Graph],
        graph_metrics: Dict[str, Any],
    ) -> tuple:
        """Return (functionality_density, penalty_multiplier).

        functionality_density: fraction of edges that are *behavioural*
            among all edges whose type is recognized (behavioural + structural).
        penalty_multiplier: 1.0 (no penalty) down to 0.6 (heavy penalty).
        """
        if G is not None:
            behavioral = 0
            structural = 0
            for _, _, attrs in G.edges(data=True):
                etype = str(
                    attrs.get("type", attrs.get("relation", ""))
                ).strip().lower()
                if etype in cls.BEHAVIORAL_EDGE_TYPES:
                    behavioral += 1
                elif etype in cls.STRUCTURAL_EDGE_TYPES:
                    structural += 1
            recognized = behavioral + structural
            density = behavioral / max(recognized, 1)
        else:
            # Without the full graph we cannot classify edges; assume ok
            return (1.0, 1.0)

        if density < cls.FUNC_DENSITY_WARN:
            # Very low behavioural density — likely padded with imports/inherits
            penalty = max(0.6, density / cls.FUNC_DENSITY_WARN)
        else:
            penalty = 1.0
        return round(density, 4), round(penalty, 4)

    @classmethod
    def _detect_suspicious_patterns(
        cls,
        G: Optional[nx.Graph],
        graph_metrics: Dict[str, Any],
        dead_code_ratio: float,
        functionality_density: float,
    ) -> List[str]:
        """Return a list of human-readable warnings about potential gaming."""
        patterns: List[str] = []

        if dead_code_ratio > cls.DEAD_CODE_RATIO_WARN:
            patterns.append(
                f"High dead-code ratio ({dead_code_ratio:.0%}): many leaf nodes "
                f"with no incoming edges — possible padding with unused code"
            )

        if functionality_density < cls.FUNC_DENSITY_WARN:
            patterns.append(
                f"Low functionality density ({functionality_density:.0%}): most "
                f"edges are structural (imports/inherits) rather than behavioural "
                f"(calls/data-flow) — possible gaming via trivial scaffolding"
            )

        # Detect suspiciously uniform community sizes (sign of synthetic structure)
        if G is not None:
            communities_count = graph_metrics.get("communities", 0)
            nodes = graph_metrics.get("nodes", 0)
            if communities_count >= 3 and nodes >= 10:
                # If every community has exactly the same size, flag it
                labels = graph_metrics.get("community_labels", [])
                if len(labels) == communities_count:
                    avg_size = nodes / communities_count
                    # Perfect uniformity is suspicious for real code
                    if avg_size == int(avg_size) and nodes % communities_count == 0:
                        patterns.append(
                            f"Suspiciously uniform community sizes "
                            f"({communities_count} communities of ~{int(avg_size)} nodes each)"
                        )

        # High node count but very few edges per node suggests padding
        nodes = graph_metrics.get("nodes", 0)
        edges = graph_metrics.get("edges", 0)
        if nodes >= 20 and edges > 0:
            edge_density = edges / nodes
            if edge_density < 0.5:
                patterns.append(
                    f"Very low edge density ({edge_density:.2f} edges/node): "
                    f"nodes may have been added without real relationships"
                )

        return patterns

    @staticmethod
    def score(
        graph_metrics: Dict[str, Any],
        diff: Optional[GraphDiff] = None,
        *,
        graph: Optional[nx.Graph] = None,
    ) -> Dict[str, Any]:
        """Compute quality score from graph metrics and optional diff.

        Args:
            graph_metrics: the "graph_metrics" dict from GraphifyReceipt
            diff: optional GraphDiff from comparing sender/receiver graphs
            graph: optional full NetworkX graph for deeper adversarial checks.
                When provided, dead-code and functionality-density analysis
                use actual edge types and in-degree data.

        Returns:
            {
                "quality_score": float (0.0-1.0),
                "factors": {factor_name: component_score, ...},
                "penalties": {penalty_name: multiplier, ...},
                "suspicious_patterns": [str, ...],
                "grade": "A" | "B" | "C" | "D" | "F"
            }
        """
        m = graph_metrics
        nodes = m.get("nodes", 0)
        edges = m.get("edges", 0)

        # 1. Cohesion: avg_cohesion directly (already 0.0-1.0)
        cohesion = min(max(m.get("avg_cohesion", 0.0), 0.0), 1.0)

        # 2. Low isolation: penalize high isolated-node ratio
        isolated_ratio = m.get("isolated_nodes", 0) / max(nodes, 1)
        low_isolation = max(0.0, 1.0 - (isolated_ratio * 3.0))  # 33%+ isolated = 0

        # 3. Low ambiguity: penalize high ambiguous-edge percentage
        low_ambiguity = max(0.0, 1.0 - (m.get("ambiguous_edge_pct", 0.0) * 4.0))  # 25%+ = 0

        # 4. Structural match: how well sender/receiver graphs agree
        if diff is not None:
            structural_match = diff.structural_similarity
        else:
            structural_match = 1.0  # no diff available = assume match

        # 5. Complexity: normalized graph size (rewards non-trivial codebases)
        #    50+ nodes = full score, scales linearly below
        complexity = min(nodes / 50.0, 1.0)

        # 6. God node health: penalize if top god node has > 40% of all edges
        god_node_labels = m.get("god_nodes", [])
        if nodes > 0 and edges > 0:
            # Heuristic: if <= 3 god nodes listed, the graph is likely well-distributed
            # More precise scoring requires the full graph (degree distribution)
            god_node_health = min(len(god_node_labels) / 3.0, 1.0)
        else:
            god_node_health = 0.0

        factors = {
            "cohesion": round(cohesion, 4),
            "low_isolation": round(low_isolation, 4),
            "low_ambiguity": round(low_ambiguity, 4),
            "structural_match": round(structural_match, 4),
            "complexity": round(complexity, 4),
            "god_node_health": round(god_node_health, 4),
        }

        w = CodeQualityScorer.WEIGHTS
        quality = sum(w[k] * factors[k] for k in w)

        # -- Adversarial penalty factors --
        dead_code_ratio, dead_code_penalty = CodeQualityScorer._detect_dead_code(
            graph, m
        )
        func_density, func_density_penalty = CodeQualityScorer._detect_functionality_density(
            graph, m
        )

        penalties = {
            "dead_code": round(dead_code_penalty, 4),
            "functionality_density": round(func_density_penalty, 4),
        }

        # Apply penalties as multipliers
        quality *= dead_code_penalty * func_density_penalty
        quality = round(min(max(quality, 0.0), 1.0), 4)

        # -- Suspicious pattern detection --
        suspicious = CodeQualityScorer._detect_suspicious_patterns(
            graph, m, dead_code_ratio, func_density,
        )

        if quality >= 0.85:
            grade = "A"
        elif quality >= 0.70:
            grade = "B"
        elif quality >= 0.55:
            grade = "C"
        elif quality >= 0.40:
            grade = "D"
        else:
            grade = "F"

        return {
            "quality_score": quality,
            "factors": factors,
            "penalties": penalties,
            "suspicious_patterns": suspicious,
            "grade": grade,
        }


# ---------------------------------------------------------------------------
# VerifiedDelivery — end-to-end flow orchestrator
# ---------------------------------------------------------------------------

class VerifiedDelivery:
    """Orchestrates the full graphify-verified delivery flow.

    Ties together Graphify extraction, receipt creation, delivery,
    independent verification, and confirm/dispute decision.
    """

    def __init__(self, agent: Any) -> None:
        """
        Args:
            agent: a nexus_ledger.Agent instance
        """
        self._agent = agent

    def deliver(
        self,
        task_id: str,
        codebase_path: str,
        *,
        artifact_hash: str,
        artifact_url: Optional[str] = None,
        to: Optional[str] = None,
        encrypted: bool = False,
        include_full_graph: bool = False,
    ) -> Dict[str, Any]:
        """Run Graphify, build receipt payload, and deliver via Nexus Ledger.

        This replaces the direct agent.deliver_task() call when you want
        graph-verified delivery.

        Args:
            task_id: existing task ID from the request/accept chain
            codebase_path: path to the deliverable codebase directory
            artifact_hash: SHA-256 of the tarball/zip (existing field)
            artifact_url: optional download URL
            to: recipient agent name or DID (inferred from task chain if omitted)
            encrypted: encrypt the receipt payload
            include_full_graph: embed full graph.json in the receipt (large!)

        Returns:
            The signed TaskDelivered receipt dict with graph_hash and graph_metrics
        """
        # 1. Run Graphify
        graph_receipt = GraphifyReceipt.from_path(
            codebase_path, include_full_graph=include_full_graph
        )

        # 2. Build augmented delivery data
        #    We use the existing TaskManager flow but inject graph data
        from .receipt_types import TaskDelivered

        typed = TaskDelivered(
            task_id=task_id,
            artifact_hash=artifact_hash,
            artifact_url=artifact_url,
        )
        data = typed.as_data()
        graph_data = graph_receipt.as_receipt_data()
        data["graph_hash"] = graph_data["graph_hash"]
        data["graph_metrics"] = graph_data["graph_metrics"]
        if graph_data.get("graph_json_b64"):
            data["graph_json_b64"] = graph_data["graph_json_b64"]

        # 3. Send via existing relay infrastructure
        counterparty = to
        if not counterparty:
            pubkey = self._agent._resolve_task_counterparty_pubkey(task_id)
            if not pubkey:
                raise ValueError("Could not infer task counterparty; provide 'to'")
            from .transport import public_key_to_did
            counterparty = public_key_to_did(pubkey)

        parent = self._agent._latest_task_receipt_hash(task_id)
        return self._agent.send(
            "TaskDelivered",
            data,
            to=counterparty,
            encrypted=encrypted,
            parent_receipt_hash=parent,
        )

    def verify_and_decide(
        self,
        task_id: str,
        codebase_path: str,
        delivered_receipt: Dict[str, Any],
        *,
        auto_confirm_threshold: float = 0.6,
        to: Optional[str] = None,
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        """Independently verify a delivery and confirm or dispute.

        Called by Agent B after receiving a TaskDelivered receipt.

        Args:
            task_id: the task being verified
            codebase_path: path where Agent B has the delivered code
            delivered_receipt: the TaskDelivered receipt from Agent A
            auto_confirm_threshold: quality score above which we auto-confirm
            to: sender agent name or DID
            encrypted: encrypt the response receipt

        Returns:
            The TaskConfirmed or TaskDisputed receipt dict
        """
        # 1. Extract delivered receipt data
        receipt_data = delivered_receipt.get("data", {})
        sender_graph_hash = receipt_data.get("graph_hash", "")
        sender_metrics = receipt_data.get("graph_metrics", {})

        # 2. Run Graphify independently
        our_receipt = GraphifyReceipt.from_path(codebase_path)
        our_metrics = our_receipt.as_receipt_data()

        # 3. Compare
        diff = GraphDiff.from_receipts(
            {"graph_hash": sender_graph_hash, "graph_metrics": sender_metrics},
            {"graph_hash": our_receipt.graph_hash, "graph_metrics": our_metrics["graph_metrics"]},
        )

        # 4. Score quality
        quality = CodeQualityScorer.score(
            our_metrics["graph_metrics"], diff=diff
        )

        # 5. Decide
        if quality["quality_score"] >= auto_confirm_threshold and diff.hash_match:
            # Confirm with quality metrics as feedback
            rating = _quality_to_rating(quality["quality_score"])
            feedback = (
                f"Graph-verified delivery. "
                f"Quality: {quality['grade']} ({quality['quality_score']:.2f}). "
                f"Graph hash match: YES. "
                f"{our_receipt.node_count} nodes, {our_receipt.edge_count} edges, "
                f"{our_receipt.community_count} communities."
            )
            return self._agent.confirm_task(
                task_id, rating=rating, feedback=feedback,
                to=to, encrypted=encrypted,
            )
        else:
            # Dispute with evidence
            reasons = []
            if not diff.hash_match:
                reasons.append(
                    f"Graph hash mismatch: sender={sender_graph_hash[:16]}... "
                    f"vs receiver={our_receipt.graph_hash[:16]}..."
                )
            if quality["quality_score"] < auto_confirm_threshold:
                reasons.append(
                    f"Quality below threshold: {quality['quality_score']:.2f} < "
                    f"{auto_confirm_threshold:.2f} (grade: {quality['grade']})"
                )
                for factor, value in quality["factors"].items():
                    if value < 0.4:
                        reasons.append(f"  - {factor}: {value:.2f}")
            reason = "Graph-verified dispute. " + "; ".join(reasons)
            return self._agent.dispute_task(
                task_id, reason=reason, to=to, encrypted=encrypted,
            )


def _quality_to_rating(quality_score: float) -> int:
    """Map a 0.0-1.0 quality score to a 1-5 rating for TaskConfirmed."""
    if quality_score >= 0.9:
        return 5
    elif quality_score >= 0.75:
        return 4
    elif quality_score >= 0.6:
        return 3
    elif quality_score >= 0.4:
        return 2
    else:
        return 1


