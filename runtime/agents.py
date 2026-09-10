"""
Agent Registry - defines and resolves Kit's team of agents.

Kit itself is the built-in "chief of staff" agent (full tool/skill access,
persona = workspace/SOUL.md, for backward compatibility with the original
single-agent setup). Every other team member is created from a template
(built-in presets in templates/agents/, or user-authored ones in
workspace/agent_templates/) with optional tool/skill/persona overrides, and
is persisted under workspace/agents/.
"""

import json
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

TOOL_WILDCARD = "*"
KIT_AGENT_ID = "kit"

ToolList = Union[List[str], str]  # str is only ever the "*" wildcard


@dataclass
class AgentDefinition:
    """A fully-resolved team member: persona text plus tool/skill access."""

    id: str
    name: str
    description: str
    template_id: Optional[str]
    tools: ToolList
    skills: ToolList
    soul: str
    model: Optional[str] = None
    provider: Optional[str] = None

    @property
    def allowed_tools(self) -> Optional[set]:
        """None means unrestricted (full access, e.g. Kit)."""
        return None if self.tools == TOOL_WILDCARD else set(self.tools)

    @property
    def allowed_skills(self) -> Optional[set]:
        """None means unrestricted (full access, e.g. Kit)."""
        return None if self.skills == TOOL_WILDCARD else set(self.skills)


class AgentRegistry:
    """Loads agent templates and manages the roster of configured agents."""

    def __init__(self, workspace_dir: str = "workspace", templates_dir: str = "templates/agents"):
        self.workspace_dir = Path(workspace_dir)
        self.templates_dir = Path(templates_dir)
        self.agents_dir = self.workspace_dir / "agents"
        self.agent_templates_dir = self.workspace_dir / "agent_templates"
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.agent_templates_dir.mkdir(parents=True, exist_ok=True)

    # ---- templates ----

    @staticmethod
    def _read_json(path: Path) -> Optional[dict]:
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def list_templates(self) -> List[dict]:
        """Built-in templates, overridden/extended by user templates of the same id."""
        templates: Dict[str, dict] = {}
        if self.templates_dir.exists():
            for path in sorted(self.templates_dir.glob("*.json")):
                tpl = self._read_json(path)
                if tpl and "id" in tpl:
                    templates[tpl["id"]] = tpl
        for path in sorted(self.agent_templates_dir.glob("*.json")):
            tpl = self._read_json(path)
            if tpl and "id" in tpl:
                templates[tpl["id"]] = tpl
        return list(templates.values())

    def get_template(self, template_id: str) -> Optional[dict]:
        for tpl in self.list_templates():
            if tpl["id"] == template_id:
                return tpl
        return None

    def save_template(self, template: dict) -> dict:
        """Save a user-defined template (creates or overrides by id)."""
        if "id" not in template:
            raise ValueError("Template requires an 'id'")
        path = self.agent_templates_dir / f"{template['id']}.json"
        path.write_text(json.dumps(template, indent=2))
        return template

    # ---- agent instances ----

    def _agent_meta_path(self, agent_id: str) -> Path:
        return self.agents_dir / f"{agent_id}.json"

    def _agent_soul_path(self, agent_id: str) -> Path:
        if agent_id == KIT_AGENT_ID:
            return self.workspace_dir / "SOUL.md"
        return self.agents_dir / agent_id / "SOUL.md"

    @staticmethod
    def _kit_metadata() -> dict:
        return {
            "id": KIT_AGENT_ID,
            "name": "Kit",
            "description": "Chief of staff - manages the team, full tool/skill access.",
            "template_id": None,
            "tools": TOOL_WILDCARD,
            "skills": TOOL_WILDCARD,
        }

    def resolve(self, agent_id: str) -> Optional[AgentDefinition]:
        """Load a fully-resolved AgentDefinition (persona text included), or None."""
        if agent_id == KIT_AGENT_ID:
            meta = self._kit_metadata()
        else:
            meta = self._read_json(self._agent_meta_path(agent_id))
            if meta is None:
                return None

        soul_path = self._agent_soul_path(agent_id)
        soul = soul_path.read_text() if soul_path.exists() else ""

        return AgentDefinition(
            id=meta["id"],
            name=meta.get("name", meta["id"]),
            description=meta.get("description", ""),
            template_id=meta.get("template_id"),
            tools=meta.get("tools", TOOL_WILDCARD),
            skills=meta.get("skills", TOOL_WILDCARD),
            soul=soul,
            model=meta.get("model"),
            provider=meta.get("provider"),
        )

    def list_agents(self) -> List[AgentDefinition]:
        """All configured agents, Kit first."""
        agents = [self.resolve(KIT_AGENT_ID)]
        if self.agents_dir.exists():
            for path in sorted(self.agents_dir.glob("*.json")):
                defn = self.resolve(path.stem)
                if defn:
                    agents.append(defn)
        return agents

    def create_agent(
        self,
        template_id: str,
        id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tool_overrides: Optional[ToolList] = None,
        skill_overrides: Optional[ToolList] = None,
        soul_overrides: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> AgentDefinition:
        if id == KIT_AGENT_ID:
            raise ValueError("'kit' is reserved for the built-in manager agent")
        if self._agent_meta_path(id).exists():
            raise ValueError(f"Agent '{id}' already exists")

        template = self.get_template(template_id)
        if template is None:
            raise ValueError(f"Unknown template '{template_id}'")

        tools = tool_overrides if tool_overrides is not None else template.get("tools", [])
        skills = skill_overrides if skill_overrides is not None else template.get("skills", [])
        soul = soul_overrides if soul_overrides is not None else template.get("soul", "")

        self._raise_if_unknown(tools, self.validate_tools, "tool")
        self._raise_if_unknown(skills, self.validate_skills, "skill")

        meta = {
            "id": id,
            "name": name or template.get("name", id),
            "description": description or template.get("description", ""),
            "template_id": template_id,
            "tools": tools,
            "skills": skills,
        }
        if model is not None:
            meta["model"] = model
        if provider is not None:
            meta["provider"] = provider
        self._agent_meta_path(id).write_text(json.dumps(meta, indent=2))

        soul_path = self._agent_soul_path(id)
        soul_path.parent.mkdir(parents=True, exist_ok=True)
        soul_path.write_text(soul)

        return self.resolve(id)

    def update_agent(
        self,
        id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tools: Optional[ToolList] = None,
        skills: Optional[ToolList] = None,
        soul: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> AgentDefinition:
        if id == KIT_AGENT_ID:
            raise ValueError("'kit' cannot be edited via update_agent - edit workspace/SOUL.md directly")
        path = self._agent_meta_path(id)
        meta = self._read_json(path)
        if meta is None:
            raise ValueError(f"Agent '{id}' not found")

        if tools is not None:
            self._raise_if_unknown(tools, self.validate_tools, "tool")
            meta["tools"] = tools
        if skills is not None:
            self._raise_if_unknown(skills, self.validate_skills, "skill")
            meta["skills"] = skills
        if name is not None:
            meta["name"] = name
        if description is not None:
            meta["description"] = description
        if model is not None:
            meta["model"] = model if model else None
        if provider is not None:
            meta["provider"] = provider if provider else None

        path.write_text(json.dumps(meta, indent=2))

        if soul is not None:
            soul_path = self._agent_soul_path(id)
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            soul_path.write_text(soul)

        return self.resolve(id)

    def delete_agent(self, id: str) -> bool:
        if id == KIT_AGENT_ID:
            raise ValueError("Cannot delete the built-in 'kit' agent")
        path = self._agent_meta_path(id)
        existed = path.exists()
        if existed:
            path.unlink()
        soul_dir = self.agents_dir / id
        if soul_dir.exists():
            shutil.rmtree(soul_dir)
        return existed

    # ---- validation ----

    @staticmethod
    def _raise_if_unknown(names: ToolList, validator, label: str) -> None:
        if names == TOOL_WILDCARD:
            return
        unknown = validator(names)
        if unknown:
            raise ValueError(f"Unknown {label} name(s): {', '.join(unknown)}")

    def validate_tools(self, names: List[str]) -> List[str]:
        """Return the subset of `names` that aren't real tool names."""
        from tools.core import TOOLS
        known = {t["function"]["name"] for t in TOOLS}
        return [n for n in names if n not in known]

    def validate_skills(self, names: List[str]) -> List[str]:
        """Return the subset of `names` that aren't real skill names."""
        from runtime.skills import SkillsManager
        skills = SkillsManager(str(self.workspace_dir))
        known = set(skills.metadata.keys())
        return [n for n in names if n not in known]
