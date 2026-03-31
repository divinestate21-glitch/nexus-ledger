"""Nexus Ledger + AutoGen — Add trust to GroupChat message passing.

Drop this into any AutoGen GroupChat to get signed receipts
for every agent-to-agent message. Zero changes to your existing chat.
"""

from nexus_ledger import Agent
import hashlib

# 1. Create a Nexus identity for your chat
chat_ledger = Agent("GroupChatLedger")


def trusted_message_hook(sender_name: str, recipient_name: str, message: str) -> dict:
    """Hook into AutoGen's message flow.
    
    Add to your GroupChatManager or ConversableAgent:
    
    @agent.register_hook("process_message_before_send")
    def hook(sender, message, recipient, **kwargs):
        trusted_message_hook(sender.name, recipient.name, message)
    """
    receipt = chat_ledger.request_task(
        recipient_name,
        description=f"Message from {sender_name}: {message[:100]}",
        budget=0,
    )
    
    return {
        "task_id": receipt["data"]["task_id"],
        "message_hash": hashlib.sha256(message.encode()).hexdigest(),
        "sender": sender_name,
        "recipient": recipient_name,
        "trust_score": chat_ledger.trust_score(),
    }


# Example usage with AutoGen:
#
# from autogen import ConversableAgent, GroupChat, GroupChatManager
#
# alice = ConversableAgent("Alice", ...)
# bob = ConversableAgent("Bob", ...)
#
# group = GroupChat(agents=[alice, bob], ...)
# manager = GroupChatManager(groupchat=group, ...)
#
# # Add trust to every message:
# original_send = alice.send
# def trusted_send(message, recipient, **kwargs):
#     trusted_message_hook(alice.name, recipient.name, str(message))
#     return original_send(message, recipient, **kwargs)
# alice.send = trusted_send
#
# # After conversation:
# print(f"Messages verified: {len(chat_ledger.history())}")
# chat_ledger.anchor_to_eth(chain="base")

print("AutoGen + Nexus Ledger: trusted messages in 5 lines ✅")
