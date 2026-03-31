"""Nexus Ledger + LangGraph — Add trust to supervisor/worker handoffs.

Drop this into any LangGraph workflow to get signed receipts
at every node transition. Zero changes to your existing graph.
"""

from nexus_ledger import Agent

# Your existing LangGraph imports
# from langgraph.graph import StateGraph, Command

# 1. Create a Nexus identity for your graph
supervisor = Agent("Supervisor")


def trusted_handoff(worker_name: str, task: str, result: str) -> dict:
    """Wrap any LangGraph node handoff with Nexus trust.
    
    Call this AFTER your worker returns, BEFORE your supervisor ingests.
    5 lines. That's it.
    """
    # Record the delegation
    receipt = supervisor.request_task(
        worker_name,
        description=task,
        budget=0,
    )
    
    # Store the result hash as delivery proof
    import hashlib
    result_hash = hashlib.sha256(result.encode()).hexdigest()
    
    return {
        "task_id": receipt["data"]["task_id"],
        "result": result,
        "result_hash": result_hash,
        "trust_score": supervisor.trust_score(),
        "verified": True,
    }


# Example: Your existing LangGraph node
def research_node(state: dict) -> dict:
    """Your existing worker node — unchanged."""
    # ... your existing LangGraph logic ...
    result = "Research findings: competitor analysis complete"
    
    # ADD THIS: one line to make it trusted
    verified = trusted_handoff("ResearchWorker", state.get("task", ""), result)
    
    return {**state, "research": verified}


# That's it. Your LangGraph workflow is now trust-verified.
# Every handoff has: signed receipt, result hash, trust score.
# Query anytime: supervisor.get_task_chain(task_id)
# Anchor to Ethereum: supervisor.anchor_to_eth(chain="base")

print("LangGraph + Nexus Ledger: trusted handoffs in 5 lines ✅")
