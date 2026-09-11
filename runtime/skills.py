"""
Skills Manager - Learn and execute reusable skills.

Supports two skill types:
  - executable (.py) — Python functions run in a sandboxed subprocess
  - prompt (.md) — Markdown guides injected into the system prompt to
    give the agent domain expertise (NVIDIA SKILL.md format compatible)
"""

import builtins
import json
import subprocess
import sys
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
import hashlib

from env_config import env_int

_RUNNER_SCRIPT = Path(__file__).parent / "_skill_scripts" / "run_skill.py"
_SKILL_TIMEOUT_SECONDS = env_int("SKILL_TIMEOUT_SECONDS", 60)

# Modules a skill is allowed to import. Keeps skills useful for the kind of
# small data-transformation tasks they're meant for, without handing them
# os/subprocess/sys/etc.
_SAFE_MODULES = (
    "json", "math", "re", "datetime", "time", "random", "string",
    "itertools", "collections", "functools", "textwrap",
    "hashlib", "base64", "csv", "io", "statistics", "urllib.parse",
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

        # Load metadata and auto-discover unregistered skill files
        self.metadata = self._load_metadata()
        self._discover_skills()

    def _load_metadata(self) -> Dict[str, Any]:
        """Load skills metadata."""
        if self.metadata_file.exists():
            return json.loads(self.metadata_file.read_text())
        return {}

    @staticmethod
    def _parse_md_frontmatter(text: str) -> Dict[str, Any]:
        """Extract YAML frontmatter and body from a markdown skill file."""
        frontmatter: Dict[str, Any] = {}
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError:
                    pass
                body = parts[2].strip()
        return {"frontmatter": frontmatter, "body": body}

    def _discover_skills(self):
        """Register any .py/.md skill files on disk that are missing from metadata."""
        changed = False

        for path in self.skills_dir.glob("*.py"):
            name = path.stem
            if name in self.metadata:
                continue
            description = name.replace("-", " ").replace("_", " ")
            version = 1
            try:
                text = path.read_text()
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.lower().startswith("description:"):
                        description = stripped.split(":", 1)[1].strip()
                    elif stripped.lower().startswith("version:"):
                        try:
                            version = int(stripped.split(":", 1)[1].strip().split()[0])
                        except (ValueError, IndexError):
                            pass
            except OSError:
                pass
            self.metadata[name] = {
                "name": name,
                "description": description,
                "type": "executable",
                "created_at": datetime.now().isoformat(),
                "version": version,
                "success_rate": 0.0,
                "usage_count": 0,
                "success_count": 0,
                "parameters": {},
                "tags": [],
                "last_used": None,
                "last_improved": None,
            }
            changed = True

        for path in self.skills_dir.glob("*.md"):
            if path.name == "skills_metadata.json":
                continue
            name = path.stem
            if name in self.metadata:
                continue
            description = name.replace("-", " ").replace("_", " ")
            tags: List[str] = []
            try:
                parsed = self._parse_md_frontmatter(path.read_text())
                fm = parsed["frontmatter"]
                if fm.get("description"):
                    description = fm["description"]
                if fm.get("name"):
                    name = fm["name"]
                meta_block = fm.get("metadata") or {}
                if isinstance(meta_block.get("tags"), list):
                    tags = meta_block["tags"]
                elif isinstance(fm.get("tags"), list):
                    tags = fm["tags"]
            except OSError:
                pass
            self.metadata[name] = {
                "name": name,
                "description": description,
                "type": "prompt",
                "created_at": datetime.now().isoformat(),
                "version": 1,
                "success_rate": 0.0,
                "usage_count": 0,
                "success_count": 0,
                "parameters": {},
                "tags": tags,
                "last_used": None,
                "last_improved": None,
            }
            changed = True

        if changed:
            self._save_metadata()

    def _save_metadata(self):
        """Save skills metadata."""
        self.metadata_file.write_text(json.dumps(self.metadata, indent=2))

    def create_skill(
        self,
        name: str,
        description: str,
        code: str,
        parameters: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        skill_type: str = "executable",
    ) -> str:
        """
        Create a new skill.

        Args:
            name: Skill name (kebab-case)
            description: What the skill does
            code: Python code for executable skills, or markdown body for
                prompt skills
            parameters: Parameter descriptions (executable skills only)
            tags: Categorization tags
            skill_type: "executable" (default) for Python scripts, or
                "prompt" for markdown guides injected into context

        Returns:
            Success message or error

        Note:
            Executable skills run in a dedicated subprocess with restricted
            builtins and a small stdlib import allowlist (see _SAFE_MODULES /
            _SAFE_BUILTIN_NAMES). Prompt skills are injected into the system
            prompt and never executed.
        """
        # Validate name
        if not name.replace("-", "").replace("_", "").isalnum():
            return f"Error: Invalid skill name '{name}'. Use alphanumeric with - or _"

        ext = ".md" if skill_type == "prompt" else ".py"
        skill_file = self.skills_dir / f"{name}{ext}"

        if name in self.metadata or skill_file.exists():
            kind = "Skill" if skill_type == "prompt" else "Custom tool"
            return f"Error: {kind} '{name}' already exists. Use improve_skill to update."

        if skill_type == "prompt":
            tag_list = tags or []
            frontmatter = yaml.dump({
                "name": name,
                "description": description,
                "metadata": {"tags": tag_list} if tag_list else {},
            }, default_flow_style=False).strip()
            skill_content = f"---\n{frontmatter}\n---\n\n{code}\n"
        else:
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
                "type": skill_type,
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

            label = "Skill" if skill_type == "prompt" else "Custom tool"
            return f"✅ {label} '{name}' created successfully!\nFile: {skill_file}"

        except Exception as e:
            return f"Error creating skill: {e}"

    def list_skills(self, tag: Optional[str] = None, names: Optional[set] = None) -> str:
        """
        List all available skills.

        Args:
            tag: Optional tag filter
            names: Optional set of skill names to restrict the listing to
                (used to scope a team member to only its allowlisted skills)

        Returns:
            Formatted list of skills
        """
        if not self.metadata:
            return "No custom tools or skills created yet."

        skills = self.metadata.values()

        if names is not None:
            skills = [s for s in skills if s["name"] in names]

        # Filter by tag if provided
        if tag:
            skills = [s for s in skills if tag in s.get("tags", [])]

        if not skills:
            return f"No items found with tag '{tag}'" if tag else "No custom tools or skills found"

        # Format output
        lines = ["Available Custom Tools & Skills:\n"]
        for skill in sorted(skills, key=lambda s: s["usage_count"], reverse=True):
            skill_type = skill.get("type", "executable")
            if skill_type == "prompt":
                type_label = "skill"
                lines.append(f"📖 **{skill['name']}** (v{skill['version']}, {type_label})")
                lines.append(f"   {skill['description']}")
                lines.append("   Type: skill (domain guide loaded into context automatically)")
            else:
                type_label = "custom tool"
                lines.append(f"📦 **{skill['name']}** (v{skill['version']}, {type_label})")
                lines.append(f"   {skill['description']}")
                lines.append(f"   Usage: {skill['usage_count']} times | Success: {skill['success_rate']:.1%}")

            if skill.get('parameters'):
                param_parts = [f"{k}: {v}" for k, v in skill['parameters'].items()]
                lines.append(f"   Parameters: {', '.join(param_parts)}")

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

        Runs in a dedicated subprocess (see _skill_scripts/run_skill.py) so a
        skill can't touch the gateway process's memory, can't wedge the
        server if it hangs (the subprocess is killed on timeout), and a
        crash in the skill can't take down the server.

        Args:
            name: Skill name
            **kwargs: Arguments to pass to skill

        Returns:
            Skill execution result
        """
        if name not in self.metadata:
            return f"Error: Skill '{name}' not found"

        if self.metadata[name].get("type") == "prompt":
            return f"Error: '{name}' is a skill (domain guide loaded into context automatically), not a custom tool — it cannot be executed"

        skill_file = self.skills_dir / f"{name}.py"
        if not skill_file.exists():
            return f"Error: Skill file for '{name}' not found"

        try:
            code = skill_file.read_text()
            payload = json.dumps({"code": code, "func_name": name, "kwargs": kwargs})

            proc = subprocess.run(
                [sys.executable, str(_RUNNER_SCRIPT)],
                input=payload,
                capture_output=True,
                text=True,
                timeout=_SKILL_TIMEOUT_SECONDS,
            )

            if proc.returncode != 0:
                raise RuntimeError(
                    proc.stderr.strip() or f"skill process exited with code {proc.returncode}"
                )

            output = json.loads(proc.stdout.strip() or "{}")
            if "error" in output:
                raise RuntimeError(output["error"])

            result = output.get("result", "")

            # Update metadata
            meta = self.metadata[name]
            meta['usage_count'] += 1
            meta['success_count'] = meta.get('success_count', 0) + 1
            meta['last_used'] = datetime.now().isoformat()
            meta['success_rate'] = meta['success_count'] / meta['usage_count']
            self._save_metadata()

            return result

        except subprocess.TimeoutExpired:
            meta = self.metadata[name]
            meta['usage_count'] += 1
            meta.setdefault('success_count', 0)
            meta['last_used'] = datetime.now().isoformat()
            meta['success_rate'] = meta['success_count'] / meta['usage_count']
            self._save_metadata()

            return f"Error executing skill '{name}': timed out after {_SKILL_TIMEOUT_SECONDS}s"

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

        meta = self.metadata[name]
        is_prompt = meta.get("type") == "prompt"
        ext = ".md" if is_prompt else ".py"
        skill_file = self.skills_dir / f"{name}{ext}"
        if not skill_file.exists():
            return f"Error: file for '{name}' not found"

        try:
            # Backup old version
            backup_file = self.skills_dir / f"{name}.v{meta['version']}{ext}.bak"
            backup_file.write_text(skill_file.read_text())

            # Update code if provided
            if code:
                new_version = meta['version'] + 1
                if is_prompt:
                    tag_list = meta.get("tags", [])
                    frontmatter = yaml.dump({
                        "name": name,
                        "description": meta["description"],
                        "metadata": {"tags": tag_list} if tag_list else {},
                    }, default_flow_style=False).strip()
                    skill_content = f"---\n{frontmatter}\n---\n\n{code}\n"
                else:
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

        ext = ".md" if self.metadata[name].get("type") == "prompt" else ".py"
        skill_file = self.skills_dir / f"{name}{ext}"

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

    def get_prompt_skills(self, names: Optional[set] = None) -> List[Dict[str, str]]:
        """Return the content of all prompt-type skills for system prompt injection.

        Args:
            names: Optional set of skill names to restrict to (for scoped agents).
                None means unrestricted.

        Returns:
            List of dicts with 'name', 'description', and 'content' keys.
        """
        results = []
        for meta in self.metadata.values():
            if meta.get("type") != "prompt":
                continue
            if names is not None and meta["name"] not in names:
                continue
            skill_file = self.skills_dir / f"{meta['name']}.md"
            if not skill_file.exists():
                continue
            try:
                parsed = self._parse_md_frontmatter(skill_file.read_text())
                results.append({
                    "name": meta["name"],
                    "description": meta["description"],
                    "content": parsed["body"],
                })
            except OSError:
                continue
        return results

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
