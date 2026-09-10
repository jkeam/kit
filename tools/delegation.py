"""
Delegation tool - lets an agent hand a task to another agent on the team.

Only agents whose configured `tools` include "agent_delegate" ever see this
tool's schema (see runtime/agent.py's per-agent tool filtering), and actual
dispatch requires a live SessionManager reference - see
PersonalAssistant._delegate in runtime/agent.py, which intercepts this tool
name before it ever reaches `execute_tool` below.
"""


def agent_delegate(agent_id: str, task: str) -> str:
    """
    Delegate a task to another agent on the team and get their reply.

    Args:
        agent_id: The id of the team member to delegate to
        task: The instruction/task to hand to that agent

    Returns:
        The delegated agent's reply
    """
    # This will be bound to a SessionManager instance
    return "Error: agent_delegate requires a SessionManager to be initialized"


# Tool definitions
DELEGATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "agent_delegate",
            "description": (
                "Delegate a task to another agent on the team and wait for their reply. "
                "Only that agent performs the task - it does not get broadcast to anyone else."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The id of the team member to delegate to (see the agent roster)"
                    },
                    "task": {
                        "type": "string",
                        "description": "The instruction to give that agent"
                    }
                },
                "required": ["agent_id", "task"]
            }
        }
    }
]

DELEGATION_TOOL_FUNCTIONS = {
    "agent_delegate": agent_delegate,
}
