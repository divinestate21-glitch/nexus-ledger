"""Nexus Ledger tool for LangChain — agent-to-agent trust layer."""

from typing import Optional, Type
from pydantic import BaseModel, Field

try:
    from langchain_core.tools import BaseTool
except ImportError:
    raise ImportError("Install langchain-core: pip install langchain-core")

from nexus_ledger import Agent


class NexusTaskInput(BaseModel):
    """Input for requesting a task from another agent."""
    agent_name: str = Field(description="Name of the agent to hire")
    description: str = Field(description="What work needs to be done")
    budget: float = Field(description="Budget in USDC", default=0)


class NexusRequestTaskTool(BaseTool):
    """Request work from another AI agent via Nexus Ledger.
    
    Creates a cryptographically signed task request with receipt chain.
    The receiving agent can accept, deliver, and confirm.
    """
    name: str = "nexus_request_task"
    description: str = (
        "Hire another AI agent to do work. Creates a signed, verifiable "
        "task request. Use when you need another agent to perform research, "
        "analysis, content creation, or any delegatable task."
    )
    args_schema: Type[BaseModel] = NexusTaskInput
    agent: Agent = None

    def __init__(self, agent_name: str = "Agent", **kwargs):
        super().__init__(**kwargs)
        self.agent = Agent(agent_name)

    def _run(self, agent_name: str, description: str, budget: float = 0) -> str:
        result = self.agent.request_task(
            agent_name,
            description=description,
            budget=budget,
        )
        task_id = result.get("data", {}).get("task_id", "unknown")
        return f"Task {task_id} requested from {agent_name}. Description: {description}. Budget: ${budget}"


class NexusTrustScoreTool(BaseTool):
    """Check trust score of the current agent."""
    name: str = "nexus_trust_score"
    description: str = "Check your agent's trust score (0.0 to 1.0) based on transaction history."
    agent: Agent = None

    def __init__(self, agent_name: str = "Agent", **kwargs):
        super().__init__(**kwargs)
        self.agent = Agent(agent_name)

    def _run(self) -> str:
        score = self.agent.trust_score()
        return f"Trust score: {score:.2f}"


class NexusAnchorTool(BaseTool):
    """Anchor receipts to Ethereum."""
    name: str = "nexus_anchor"
    description: str = "Anchor the latest receipt to Ethereum (Base L2) for immutable proof."
    agent: Agent = None

    def __init__(self, agent_name: str = "Agent", **kwargs):
        super().__init__(**kwargs)
        self.agent = Agent(agent_name)

    def _run(self) -> str:
        result = self.agent.anchor_to_eth(chain="base")
        return f"Anchored: {result['status']} — hash: {result['receipt_hash'][:32]}..."


# Usage:
# from integrations.langchain_tool import NexusRequestTaskTool, NexusTrustScoreTool
# tools = [NexusRequestTaskTool("Mercury"), NexusTrustScoreTool("Mercury")]
