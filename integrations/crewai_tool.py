"""Nexus Ledger tool for CrewAI — agent-to-agent trust layer."""

try:
    from crewai.tools import BaseTool
except ImportError:
    raise ImportError("Install crewai: pip install crewai")

from nexus_ledger import Agent


class NexusLedgerTool(BaseTool):
    name: str = "Nexus Ledger"
    description: str = (
        "Hire another AI agent, verify work, and build trust scores. "
        "Use this tool when you need to delegate work to another agent "
        "with cryptographic proof of delivery."
    )

    def __init__(self, agent_name: str = "CrewAgent"):
        super().__init__()
        self._nexus_agent = Agent(agent_name)

    def _run(self, action: str, **kwargs) -> str:
        if action == "request_task":
            result = self._nexus_agent.request_task(
                kwargs.get("to", ""),
                description=kwargs.get("description", ""),
                budget=kwargs.get("budget", 0),
            )
            return f"Task requested: {result.get('data', {}).get('task_id')}"

        elif action == "trust_score":
            return f"Trust: {self._nexus_agent.trust_score():.2f}"

        elif action == "check_inbox":
            receipts = self._nexus_agent.check_inbox()
            return f"Received {len(receipts)} receipts"

        elif action == "anchor":
            result = self._nexus_agent.anchor_to_eth(chain="base")
            return f"Anchored: {result['receipt_hash'][:32]}..."

        return f"Unknown action: {action}"
