"""Nexus Ledger + OpenClaw — Add trust to ACP agent handoffs.

Drop this into any OpenClaw multi-agent setup to get signed receipts
when agents communicate via ACP. Zero changes to your existing agents.
"""

from nexus_ledger import Agent
import hashlib

# 1. Create a Nexus identity for your OpenClaw gateway
gateway_ledger = Agent("OpenClawGateway")


def on_acp_handoff(source_agent: str, target_agent: str, task: str, result: str = "") -> dict:
    """Call this when an OpenClaw agent delegates via sessions_send or ACP.
    
    In your agent's AGENTS.md or skill, add:
    
    Before any sessions_send or sessions_spawn:
        receipt = on_acp_handoff("Mercury", "Iris", task_description)
    
    After receiving result:
        on_acp_complete(receipt["task_id"], result)
    """
    receipt = gateway_ledger.request_task(
        target_agent,
        description=task,
        budget=0,
    )
    
    return {
        "task_id": receipt["data"]["task_id"],
        "source": source_agent,
        "target": target_agent,
        "trust_score": gateway_ledger.trust_score(),
    }


def on_acp_complete(task_id: str, result: str) -> dict:
    """Record task completion after ACP response."""
    result_hash = hashlib.sha256(result.encode()).hexdigest()
    
    return {
        "task_id": task_id,
        "result_hash": result_hash,
        "total_receipts": len(gateway_ledger.history()),
        "trust_score": gateway_ledger.trust_score(),
    }


# Example: OpenClaw multi-agent workflow
#
# # Agent Mercury delegates to Agent Iris via ACP
# receipt = on_acp_handoff("Mercury", "Iris", "Analyze competitor pricing")
#
# # ... Iris does the work via sessions_send ...
#
# # When result comes back:
# completion = on_acp_complete(receipt["task_id"], result_text)
#
# # Full audit trail:
# print(f"Receipts: {len(gateway_ledger.history())}")
# print(f"Trust: {gateway_ledger.trust_score()}")
#
# # Anchor everything to Ethereum:
# gateway_ledger.anchor_to_eth(chain="base")

print("OpenClaw + Nexus Ledger: trusted ACP handoffs in 5 lines ✅")
