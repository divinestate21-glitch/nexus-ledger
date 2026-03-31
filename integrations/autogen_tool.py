"""Nexus Ledger integration for AutoGen — agent-to-agent trust layer."""

from nexus_ledger import Agent


def create_nexus_functions(agent_name: str = "AutoGenAgent"):
    """Create function definitions for AutoGen function calling."""
    agent = Agent(agent_name)

    def request_task(target_agent: str, description: str, budget: float = 0) -> str:
        """Request work from another AI agent with signed receipt.
        
        Args:
            target_agent: Name of the agent to hire
            description: What work needs to be done
            budget: Budget amount in USDC
        """
        result = agent.request_task(target_agent, description=description, budget=budget)
        task_id = result.get("data", {}).get("task_id", "unknown")
        return f"Task {task_id} requested from {target_agent}"

    def check_trust_score() -> str:
        """Check current agent's trust score (0.0 to 1.0)."""
        return f"Trust score: {agent.trust_score():.4f}"

    def check_inbox() -> str:
        """Check for incoming receipts from other agents."""
        receipts = agent.check_inbox()
        return f"Received {len(receipts)} receipts"

    def anchor_receipt() -> str:
        """Anchor latest receipt to Ethereum (Base L2)."""
        result = agent.anchor_to_eth(chain="base")
        return f"Status: {result['status']}, Hash: {result['receipt_hash'][:32]}..."

    def get_trust_report() -> str:
        """Get detailed trust report with Sybil resistance metrics."""
        report = agent.get_trust_report(agent.public_key)
        return (
            f"Score: {report['score']:.4f}, "
            f"Diversity: {report['factors'].get('diversity_score', 'N/A')}, "
            f"Counterparties: {report['factors'].get('unique_counterparties', 'N/A')}"
        )

    return {
        "request_task": request_task,
        "check_trust_score": check_trust_score,
        "check_inbox": check_inbox,
        "anchor_receipt": anchor_receipt,
        "get_trust_report": get_trust_report,
    }


# Usage with AutoGen:
# functions = create_nexus_functions("MyAgent")
# assistant = AssistantAgent("assistant", llm_config={...})
# assistant.register_function(function_map=functions)
