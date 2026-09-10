"""
Knowledge tools - per-agent knowledge management.

All tools are intercepted by PersonalAssistant._execute_tool() and dispatched
to the agent's KnowledgeManager instance (same pattern as memory_search and
skill_* tools).
"""


def knowledge_teach(**kwargs) -> str:
    return "Error: knowledge_teach must be handled by PersonalAssistant"


def knowledge_ingest(**kwargs) -> str:
    return "Error: knowledge_ingest must be handled by PersonalAssistant"


def knowledge_search(**kwargs) -> str:
    return "Error: knowledge_search must be handled by PersonalAssistant"


def knowledge_list(**kwargs) -> str:
    return "Error: knowledge_list must be handled by PersonalAssistant"


def knowledge_ingest_url(**kwargs) -> str:
    return "Error: knowledge_ingest_url must be handled by PersonalAssistant"


def knowledge_forget(**kwargs) -> str:
    return "Error: knowledge_forget must be handled by PersonalAssistant"


KNOWLEDGE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "knowledge_teach",
            "description": "Add a fact to this agent's curated knowledge base. Facts are permanently stored and always available in the agent's context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact or information to remember permanently"
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_ingest",
            "description": "Ingest a document into this agent's knowledge base for semantic search. Use for large content that should be searchable but not loaded into every prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The document content to ingest"
                    },
                    "source_name": {
                        "type": "string",
                        "description": "A name for this knowledge source (e.g. 'openshift-install-guide')"
                    }
                },
                "required": ["text", "source_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_search",
            "description": "Search this agent's knowledge base for relevant information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of results to return (default: 3)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_list",
            "description": "List all sources in this agent's knowledge base.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_ingest_url",
            "description": "Fetch a public URL and ingest its content into this agent's knowledge base for semantic search. Handles the full page content without truncation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The public URL to fetch and ingest"
                    },
                    "source_name": {
                        "type": "string",
                        "description": "A name for this knowledge source (optional, derived from URL if omitted)"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_forget",
            "description": "Remove a source from this agent's knowledge base.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_name": {
                        "type": "string",
                        "description": "Name of the source to remove"
                    }
                },
                "required": ["source_name"]
            }
        }
    }
]

KNOWLEDGE_TOOL_FUNCTIONS = {
    "knowledge_teach": knowledge_teach,
    "knowledge_ingest": knowledge_ingest,
    "knowledge_ingest_url": knowledge_ingest_url,
    "knowledge_search": knowledge_search,
    "knowledge_list": knowledge_list,
    "knowledge_forget": knowledge_forget,
}
