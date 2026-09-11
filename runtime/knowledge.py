"""
Knowledge Manager - Per-agent two-layer knowledge system.

Layer 1 (curated): KNOWLEDGE.md loaded into every system prompt.
Layer 2 (RAG): Per-agent ChromaDB collection for large-scale knowledge search.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


KIT_AGENT_ID = "kit"


class KnowledgeManager:
    """Manages per-agent curated and RAG knowledge."""

    def __init__(
        self,
        workspace_dir: str,
        agent_id: str,
        embeddings: Optional[Any] = None,
    ):
        self.workspace_dir = Path(workspace_dir)
        self.agent_id = agent_id
        self.embeddings = embeddings
        self.collection_name = f"agent_{agent_id}_knowledge"

        if agent_id == KIT_AGENT_ID:
            self.knowledge_file = self.workspace_dir / "KNOWLEDGE.md"
            self.knowledge_dir = self.workspace_dir / "knowledge"
        else:
            agent_dir = self.workspace_dir / "agents" / agent_id
            self.knowledge_file = agent_dir / "KNOWLEDGE.md"
            self.knowledge_dir = agent_dir / "knowledge"

        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

    def get_curated_knowledge(self) -> str:
        if self.knowledge_file.exists():
            return self.knowledge_file.read_text()
        return ""

    def search(self, query: str, n_results: int = 3) -> List[Dict[str, Any]]:
        if not self.embeddings:
            return []
        return self.embeddings.search(query, n_results, collection_name=self.collection_name)

    def add_fact(self, content: str) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n\n## [{timestamp}]\n{content}"

        if self.knowledge_file.exists():
            existing = self.knowledge_file.read_text()
            self.knowledge_file.write_text(existing + entry)
        else:
            header = f"# Knowledge Base — {self.agent_id}\n"
            self.knowledge_file.write_text(header + entry)

        if self.embeddings:
            self.embeddings.index_file(
                self.knowledge_file,
                source_type="curated_knowledge",
                collection_name=self.collection_name,
            )

        return f"Fact added to {self.agent_id}'s knowledge base"

    def ingest_text(self, text: str, source_name: str) -> str:
        safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in source_name)
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        dest = self.knowledge_dir / safe_name
        dest.write_text(text)

        if self.embeddings:
            self.embeddings.index_file(
                dest,
                source_type="knowledge_doc",
                collection_name=self.collection_name,
            )

        return f"Ingested '{source_name}' into {self.agent_id}'s knowledge base"

    def list_sources(self) -> str:
        sources = []

        if self.knowledge_file.exists():
            size = len(self.knowledge_file.read_text())
            sources.append(f"- **KNOWLEDGE.md** (curated, {size} chars)")

        if self.knowledge_dir.exists():
            for f in sorted(self.knowledge_dir.iterdir()):
                if f.is_file():
                    size = len(f.read_text())
                    sources.append(f"- **{f.name}** (document, {size} chars)")

        if not sources:
            return f"No knowledge sources for agent '{self.agent_id}'"

        return f"Knowledge sources for **{self.agent_id}**:\n" + "\n".join(sources)

    def remove_source(self, source_name: str) -> str:
        safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in source_name)
        if not safe_name.endswith(".md"):
            safe_name += ".md"

        target = self.knowledge_dir / safe_name
        if not target.exists():
            return f"Source '{source_name}' not found in {self.agent_id}'s knowledge base"

        if self.embeddings:
            collection = self.embeddings._get_collection(self.collection_name)
            try:
                collection.delete(where={"source": str(target)})
            except Exception:
                pass

        target.unlink()
        return f"Removed '{source_name}' from {self.agent_id}'s knowledge base"

    def ingest_url(self, url: str, source_name: str = "") -> str:
        import html2text
        from urllib.parse import urlparse

        from tools.net_safety import UnsafeURLError, safe_get

        try:
            response = safe_get(
                url,
                timeout=60.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                },
            )
        except UnsafeURLError as e:
            return f"Error: {e}"
        response.raise_for_status()

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.ignore_emphasis = False
        h.body_width = 0
        markdown = h.handle(response.text)

        if not source_name:
            parsed = urlparse(url)
            source_name = parsed.netloc + parsed.path.rstrip("/").replace("/", "-")

        content = f"Source: {url}\n\n{markdown}"
        self.ingest_text(content, source_name)
        return f"Ingested '{source_name}' into {self.agent_id}'s knowledge base ({len(markdown)} characters from {url})"

    def index_all(self) -> None:
        if not self.embeddings:
            return

        if self.knowledge_file.exists():
            self.embeddings.index_file(
                self.knowledge_file,
                source_type="curated_knowledge",
                collection_name=self.collection_name,
            )

        if self.knowledge_dir.exists():
            for f in self.knowledge_dir.iterdir():
                if f.is_file():
                    self.embeddings.index_file(
                        f,
                        source_type="knowledge_doc",
                        collection_name=self.collection_name,
                    )
