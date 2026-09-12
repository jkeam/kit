"""
Provider Registry - manages named LLM provider configurations.

Each provider bundles connection details (type, base_url, default_model) and
references to env vars for secrets (api_key_env, extra_headers_env).  Agents
reference a provider by its id; secrets stay in .env and are resolved at
runtime via os.environ.
"""

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ProviderConfig:
    """A named LLM provider configuration."""

    id: str
    name: str
    type: str  # "ollama" | "openai" | "llamastack"
    base_url: str
    default_model: str
    api_key_env: Optional[str] = None
    extra_headers_env: Optional[str] = None
    is_default: bool = False


class ProviderRegistry:
    """Loads, saves, and resolves provider configurations."""

    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = Path(workspace_dir)
        self.providers_dir = self.workspace_dir / "providers"
        self.providers_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_json(path: Path) -> Optional[dict]:
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _provider_path(self, provider_id: str) -> Path:
        return self.providers_dir / f"{provider_id}.json"

    def resolve(self, provider_id: str) -> Optional[ProviderConfig]:
        data = self._read_json(self._provider_path(provider_id))
        if data is None:
            return None
        return ProviderConfig(**{
            k: v for k, v in data.items()
            if k in ProviderConfig.__dataclass_fields__
        })

    def list_providers(self) -> List[ProviderConfig]:
        providers = []
        for path in sorted(self.providers_dir.glob("*.json")):
            config = self.resolve(path.stem)
            if config:
                providers.append(config)
        return providers

    def get_default(self) -> Optional[ProviderConfig]:
        for config in self.list_providers():
            if config.is_default:
                return config
        return None

    def _clear_other_defaults(self, exclude_id: str) -> None:
        for config in self.list_providers():
            if config.id != exclude_id and config.is_default:
                config.is_default = False
                self._save(config)

    def _save(self, config: ProviderConfig) -> None:
        data = asdict(config)
        self._provider_path(config.id).write_text(json.dumps(data, indent=2))

    def create_provider(
        self,
        id: str,
        name: str,
        type: str,
        base_url: str,
        default_model: str,
        api_key_env: Optional[str] = None,
        extra_headers_env: Optional[str] = None,
        is_default: bool = False,
    ) -> ProviderConfig:
        if self._provider_path(id).exists():
            raise ValueError(f"Provider '{id}' already exists")

        config = ProviderConfig(
            id=id,
            name=name,
            type=type,
            base_url=base_url,
            default_model=default_model,
            api_key_env=api_key_env,
            extra_headers_env=extra_headers_env,
            is_default=is_default,
        )

        if is_default:
            self._clear_other_defaults(id)

        self._save(config)
        return config

    def update_provider(
        self,
        id: str,
        name: Optional[str] = None,
        type: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        api_key_env: Optional[str] = None,
        extra_headers_env: Optional[str] = None,
        is_default: Optional[bool] = None,
    ) -> ProviderConfig:
        config = self.resolve(id)
        if config is None:
            raise ValueError(f"Provider '{id}' not found")

        if name is not None:
            config.name = name
        if type is not None:
            config.type = type
        if base_url is not None:
            config.base_url = base_url
        if default_model is not None:
            config.default_model = default_model
        if api_key_env is not None:
            config.api_key_env = api_key_env if api_key_env else None
        if extra_headers_env is not None:
            config.extra_headers_env = extra_headers_env if extra_headers_env else None
        if is_default is not None:
            config.is_default = is_default
            if is_default:
                self._clear_other_defaults(id)

        self._save(config)
        return config

    def delete_provider(self, provider_id: str) -> bool:
        path = self._provider_path(provider_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def resolve_credentials(self, config: ProviderConfig) -> Dict[str, Any]:
        """Read actual secret values from the environment."""
        api_key = None
        if config.api_key_env:
            api_key = os.environ.get(config.api_key_env)
        extra_headers = None
        if config.extra_headers_env:
            raw = os.environ.get(config.extra_headers_env)
            if raw:
                extra_headers = json.loads(raw)
        return {"api_key": api_key, "extra_headers": extra_headers}

    def env_var_status(self, config: ProviderConfig) -> Dict[str, bool]:
        """Check whether referenced env vars are set (for UI status dots)."""
        return {
            "api_key_set": bool(config.api_key_env and os.environ.get(config.api_key_env)),
            "extra_headers_set": bool(config.extra_headers_env and os.environ.get(config.extra_headers_env)),
        }

    def ensure_default_provider(self) -> Optional[ProviderConfig]:
        """Auto-create a 'default' provider from legacy LLM_* env vars
        when no providers are configured yet (backward compat)."""
        if list(self.providers_dir.glob("*.json")):
            return None

        base_url = os.environ.get("LLM_BASE_URL")
        if not base_url:
            return None

        provider_type = os.environ.get("LLM_PROVIDER", "llamastack")
        model = os.environ.get("LLM_MODEL", "redhat-maas/qwen3-14b")

        kwargs: Dict[str, Any] = {
            "id": "default",
            "name": "Default",
            "type": provider_type,
            "base_url": base_url,
            "default_model": model,
            "is_default": True,
        }
        if os.environ.get("LLM_API_KEY"):
            kwargs["api_key_env"] = "LLM_API_KEY"
        if os.environ.get("LLM_EXTRA_HEADERS"):
            kwargs["extra_headers_env"] = "LLM_EXTRA_HEADERS"

        return self.create_provider(**kwargs)
