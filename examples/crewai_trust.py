"""Nexus Ledger + CrewAI — Add trust to crew task delegation.

Drop this callback into any CrewAI crew to get signed receipts
when agents delegate work. Zero changes to your existing crew.
"""

from nexus_ledger import Agent
import hashlib

# 1. Create a Nexus identity for your crew
crew_ledger = Agent("CrewSupervisor")


def on_task_complete(agent_name: str, task_description: str, output: str) -> dict:
    """Call this in your CrewAI task callback.
    
    crew = Crew(
        agents=[researcher, writer],
        tasks=[research_task, write_task],
        step_callback=on_task_complete  # <-- add this
    )
    """
    receipt = crew_ledger.request_task(
        agent_name,
        description=task_description,
        budget=0,
    )
    
    return {
        "task_id": receipt["data"]["task_id"],
        "output_hash": hashlib.sha256(output.encode()).hexdigest(),
        "trust_score": crew_ledger.trust_score(),
        "receipt_chain": len(crew_ledger.history()),
    }


# Example usage with CrewAI:
#
# from crewai import Agent, Task, Crew
#
# researcher = Agent(role="Researcher", ...)
# writer = Agent(role="Writer", ...)
#
# research_task = Task(description="Research competitors", agent=researcher)
# write_task = Task(description="Write report", agent=writer)
#
# crew = Crew(
#     agents=[researcher, writer],
#     tasks=[research_task, write_task],
#     step_callback=lambda step: on_task_complete(
#         step.agent.role, step.task.description, step.output
#     )
# )
#
# result = crew.kickoff()
# print(f"Trust score: {crew_ledger.trust_score()}")
# print(f"Receipts: {len(crew_ledger.history())}")
# crew_ledger.anchor_to_eth(chain="base")  # Anchor to Ethereum

print("CrewAI + Nexus Ledger: trusted delegation in 5 lines ✅")
