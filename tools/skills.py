"""
Skills tools for creating, managing, and executing learned skills.
"""

from typing import Dict, Any, Optional


def skill_create(
    name: str,
    description: str,
    code: str,
    parameters: Optional[str] = None,
    tags: Optional[str] = None
) -> str:
    """
    Create a new reusable skill.

    Args:
        name: Skill name (use kebab-case, e.g., 'analyze-code')
        description: What the skill does
        code: Python code that implements the skill
        parameters: JSON string of parameter descriptions
        tags: Comma-separated tags (e.g., 'code,analysis')

    Returns:
        Success message with skill details
    """
    # This will be bound to SkillsManager instance
    return "Error: skill_create requires SkillsManager to be initialized"


def skill_list(tag: Optional[str] = None) -> str:
    """
    List all available skills.

    Args:
        tag: Optional tag to filter by

    Returns:
        Formatted list of skills with usage stats
    """
    return "Error: skill_list requires SkillsManager to be initialized"


def skill_execute(name: str, args: Optional[str] = None) -> str:
    """
    Execute a saved skill.

    Args:
        name: Skill name to execute
        args: JSON string of arguments to pass to skill

    Returns:
        Skill execution result
    """
    return "Error: skill_execute requires SkillsManager to be initialized"


def skill_improve(name: str, changes: str, code: Optional[str] = None) -> str:
    """
    Improve an existing skill.

    Args:
        name: Skill name
        changes: Description of improvements
        code: New code (if replacing entirely)

    Returns:
        Success message with version info
    """
    return "Error: skill_improve requires SkillsManager to be initialized"


def skill_delete(name: str) -> str:
    """
    Delete a skill.

    Args:
        name: Skill name to delete

    Returns:
        Success message
    """
    return "Error: skill_delete requires SkillsManager to be initialized"


def skill_info(name: str) -> str:
    """
    Get detailed information about a skill.

    Args:
        name: Skill name

    Returns:
        Detailed skill information
    """
    return "Error: skill_info requires SkillsManager to be initialized"


# Tool definitions
SKILLS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "skill_create",
            "description": "Create a new reusable skill from Python code",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name in kebab-case (e.g., 'analyze-python-code')"
                    },
                    "description": {
                        "type": "string",
                        "description": "What the skill does"
                    },
                    "code": {
                        "type": "string",
                        "description": "Python code that implements the skill"
                    },
                    "parameters": {
                        "type": "string",
                        "description": "JSON string describing parameters"
                    },
                    "tags": {
                        "type": "string",
                        "description": "Comma-separated tags for categorization"
                    }
                },
                "required": ["name", "description", "code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": "List all available skills with usage statistics",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "Optional tag to filter by"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_execute",
            "description": "Execute a saved skill by name",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name to execute"
                    },
                    "args": {
                        "type": "string",
                        "description": "JSON string of arguments to pass"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_improve",
            "description": "Improve an existing skill with new code or changes",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name to improve"
                    },
                    "changes": {
                        "type": "string",
                        "description": "Description of improvements made"
                    },
                    "code": {
                        "type": "string",
                        "description": "New Python code (optional)"
                    }
                },
                "required": ["name", "changes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_delete",
            "description": "Delete a skill permanently",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name to delete"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_info",
            "description": "Get detailed information about a specific skill",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name"
                    }
                },
                "required": ["name"]
            }
        }
    }
]


# Function mapping
SKILLS_TOOL_FUNCTIONS = {
    "skill_create": skill_create,
    "skill_list": skill_list,
    "skill_execute": skill_execute,
    "skill_improve": skill_improve,
    "skill_delete": skill_delete,
    "skill_info": skill_info,
}
