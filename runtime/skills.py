"""
Skills Manager - Learn and execute reusable skills.

Skills are auto-generated Python functions that the assistant creates
from repeated patterns or explicit requests.
"""

import builtins
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
import hashlib

# Modules a skill is allowed to import. Keeps skills useful for the kind of
# small data-transformation tasks they're meant for, without handing them
# os/subprocess/sys/etc.
_SAFE_MODULES = (
    "json", "math", "re", "datetime", "time", "random", "string",
    "itertools", "collections", "functools", "textwrap",
)

# Builtins a skill is allowed to use. Notably excludes open, eval, exec,
# compile, input, and __import__ (replaced below with a restricted version).
_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "chr", "dict", "divmod", "enumerate",
    "filter", "float", "format", "frozenset", "hash", "hex", "int",
    "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min",
    "next", "oct", "ord", "pow", "print", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple", "type", "zip",
    "True", "False", "None",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "StopIteration", "RuntimeError", "AttributeError", "ZeroDivisionError",
)


def _restricted_import(name, *args, **kwargs):
    if name not in _SAFE_MODULES:
        raise ImportError(f"Import of '{name}' is not allowed in skills")
    return __import__(name, *args, **kwargs)


def _build_restricted_namespace() -> Dict[str, Any]:
    """Build an exec() namespace with restricted builtins and imports."""
    safe_builtins = {
        name: getattr(builtins, name)
        for name in _SAFE_BUILTIN_NAMES
        if hasattr(builtins, name)
    }
    safe_builtins["__import__"] = _restricted_import

    namespace: Dict[str, Any] = {"__builtins__": safe_builtins}
    for mod_name in _SAFE_MODULES:
        namespace[mod_name] = __import__(mod_name)
    return namespace


class SkillsManager:
    """Manages skill storage, execution, and improvement."""

    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = Path(workspace_dir)
        self.skills_dir = self.workspace_dir / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.skills_dir / "skills_metadata.json"

        # Load metadata
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict[str, Any]:
        """Load skills metadata."""
        if self.metadata_file.exists():
            return json.loads(self.metadata_file.read_text())
        return {}

    def _save_metadata(self):
        """Save skills metadata."""
        self.metadata_file.write_text(json.dumps(self.metadata, indent=2))

    def create_skill(
        self,
        name: str,
        description: str,
        code: str,
        parameters: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None
    ) -> str:
        """
        Create a new skill.

        Args:
            name: Skill name (kebab-case)
            description: What the skill does
            code: Python code implementation
            parameters: Parameter descriptions
            tags: Categorization tags

        Returns:
            Success message or error

        Note:
            Skills run with restricted builtins and a small stdlib import
            allowlist (see _SAFE_MODULES / _SAFE_BUILTIN_NAMES) — no
            filesystem, network, subprocess, or arbitrary imports.
        """
        # Validate name
        if not name.replace("-", "").replace("_", "").isalnum():
            return f"Error: Invalid skill name '{name}'. Use alphanumeric with - or _"

        skill_file = self.skills_dir / f"{name}.py"

        if skill_file.exists():
            return f"Error: Skill '{name}' already exists. Use improve_skill to update."

        # Create skill file with metadata
        skill_content = f'''"""
Skill: {name}
Description: {description}
Created: {datetime.now().isoformat()}
Version: 1
"""

{code}
'''

        try:
            skill_file.write_text(skill_content)

            # Save metadata
            self.metadata[name] = {
                "name": name,
                "description": description,
                "created_at": datetime.now().isoformat(),
                "version": 1,
                "success_rate": 0.0,
                "usage_count": 0,
                "success_count": 0,
                "parameters": parameters or {},
                "tags": tags or [],
                "last_used": None,
                "last_improved": None
            }
            self._save_metadata()

            return f"✅ Skill '{name}' created successfully!\nFile: {skill_file}"

        except Exception as e:
            return f"Error creating skill: {e}"

    def list_skills(self, tag: Optional[str] = None) -> str:
        """
        List all available skills.

        Args:
            tag: Optional tag filter

        Returns:
            Formatted list of skills
        """
        if not self.metadata:
            return "No skills created yet."

        skills = self.metadata.values()

        # Filter by tag if provided
        if tag:
            skills = [s for s in skills if tag in s.get("tags", [])]

        if not skills:
            return f"No skills found with tag '{tag}'" if tag else "No skills found"

        # Format output
        lines = ["Available Skills:\n"]
        for skill in sorted(skills, key=lambda s: s["usage_count"], reverse=True):
            lines.append(f"📦 **{skill['name']}** (v{skill['version']})")
            lines.append(f"   {skill['description']}")
            lines.append(f"   Usage: {skill['usage_count']} times | Success: {skill['success_rate']:.1%}")

            if skill['tags']:
                lines.append(f"   Tags: {', '.join(skill['tags'])}")

            if skill['last_used']:
                lines.append(f"   Last used: {skill['last_used']}")

            lines.append("")

        return "\n".join(lines)

    def get_skill_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get detailed skill information."""
        return self.metadata.get(name)

    def execute_skill(self, name: str, **kwargs) -> str:
        """
        Execute a skill.

        Args:
            name: Skill name
            **kwargs: Arguments to pass to skill

        Returns:
            Skill execution result
        """
        if name not in self.metadata:
            return f"Error: Skill '{name}' not found"

        skill_file = self.skills_dir / f"{name}.py"
        if not skill_file.exists():
            return f"Error: Skill file for '{name}' not found"

        try:
            # Read skill code
            code = skill_file.read_text()

            # Create a restricted execution namespace (no open/eval/exec,
            # only a small allowlist of stdlib imports)
            namespace = _build_restricted_namespace()

            # Execute skill code to load function
            exec(code, namespace)

            # Find the main function (same name as skill or 'main')
            func = namespace.get(name) or namespace.get('main')

            if not func:
                return f"Error: No function '{name}' or 'main' found in skill"

            # Execute skill
            result = func(**kwargs)

            # Update metadata
            meta = self.metadata[name]
            meta['usage_count'] += 1
            meta['success_count'] = meta.get('success_count', 0) + 1
            meta['last_used'] = datetime.now().isoformat()
            meta['success_rate'] = meta['success_count'] / meta['usage_count']
            self._save_metadata()

            return str(result)

        except Exception as e:
            # Track failure
            meta = self.metadata[name]
            meta['usage_count'] += 1
            meta.setdefault('success_count', 0)
            meta['last_used'] = datetime.now().isoformat()
            meta['success_rate'] = meta['success_count'] / meta['usage_count']
            self._save_metadata()

            return f"Error executing skill '{name}': {e}"

    def improve_skill(
        self,
        name: str,
        changes: str,
        code: Optional[str] = None
    ) -> str:
        """
        Improve an existing skill.

        Args:
            name: Skill name
            changes: Description of changes
            code: New code (if replacing entirely)

        Returns:
            Success message or error
        """
        if name not in self.metadata:
            return f"Error: Skill '{name}' not found"

        skill_file = self.skills_dir / f"{name}.py"
        if not skill_file.exists():
            return f"Error: Skill file for '{name}' not found"

        try:
            meta = self.metadata[name]

            # Backup old version
            backup_file = self.skills_dir / f"{name}.v{meta['version']}.py.bak"
            backup_file.write_text(skill_file.read_text())

            # Update code if provided
            if code:
                new_version = meta['version'] + 1
                skill_content = f'''"""
Skill: {name}
Description: {meta['description']}
Created: {meta['created_at']}
Version: {new_version}
Changes in v{new_version}: {changes}
"""

{code}
'''
                skill_file.write_text(skill_content)

                # Update metadata
                meta['version'] = new_version
                meta['last_improved'] = datetime.now().isoformat()
                self._save_metadata()

                return (
                    f"✅ Skill '{name}' improved to v{new_version}!\n"
                    f"Changes: {changes}\n"
                    f"Backup saved: {backup_file.name}"
                )
            else:
                # Just record the improvement note
                meta['last_improved'] = datetime.now().isoformat()
                self._save_metadata()
                return f"✅ Improvement note recorded for '{name}': {changes}"

        except Exception as e:
            return f"Error improving skill: {e}"

    def delete_skill(self, name: str) -> str:
        """
        Delete a skill.

        Args:
            name: Skill name

        Returns:
            Success message or error
        """
        if name not in self.metadata:
            return f"Error: Skill '{name}' not found"

        skill_file = self.skills_dir / f"{name}.py"

        try:
            # Remove file
            if skill_file.exists():
                skill_file.unlink()

            # Remove metadata
            del self.metadata[name]
            self._save_metadata()

            return f"✅ Skill '{name}' deleted"

        except Exception as e:
            return f"Error deleting skill: {e}"

    def get_stats(self) -> Dict[str, Any]:
        """Get skills statistics."""
        if not self.metadata:
            return {
                "total_skills": 0,
                "total_usage": 0,
                "avg_success_rate": 0.0
            }

        total_usage = sum(s['usage_count'] for s in self.metadata.values())
        avg_success = sum(s['success_rate'] for s in self.metadata.values()) / len(self.metadata)

        return {
            "total_skills": len(self.metadata),
            "total_usage": total_usage,
            "avg_success_rate": avg_success,
            "most_used": max(self.metadata.values(), key=lambda s: s['usage_count'])['name'] if self.metadata else None
        }
