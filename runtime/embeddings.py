"""
Embeddings Manager - Vector search over memory files.

Uses ChromaDB for vector storage and sentence-transformers for embeddings.
"""

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from pathlib import Path
from typing import List, Dict, Any
import hashlib


class EmbeddingsManager:
    """Manages vector embeddings for semantic memory search."""

    def __init__(
        self,
        workspace_dir: str = "workspace",
        model_name: str = "all-MiniLM-L6-v2",  # Fast, lightweight model
        collection_name: str = "memories"
    ):
        """
        Initialize embeddings manager.

        Args:
            workspace_dir: Path to workspace directory
            model_name: SentenceTransformer model to use
            collection_name: ChromaDB collection name
        """
        self.workspace_dir = Path(workspace_dir)
        self.model_name = model_name

        # Initialize ChromaDB (in-memory for Phase 3)
        self.client = chromadb.Client(Settings(
            anonymized_telemetry=False,
            allow_reset=True
        ))

        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "Memory embeddings"}
        )

        # Initialize embedding model (lightweight, runs locally)
        print("Loading embedding model (this may take a moment)...")
        self.model = SentenceTransformer(model_name)
        print(f"✅ Embedding model loaded: {model_name}")

        # Track indexed files
        self.indexed_files: Dict[str, str] = {}  # path -> content_hash

    def _get_file_hash(self, file_path: Path) -> str:
        """Get MD5 hash of file contents."""
        if not file_path.exists():
            return ""
        return hashlib.md5(file_path.read_bytes()).hexdigest()

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """
        Split text into chunks for embedding.

        Args:
            text: Text to chunk
            chunk_size: Approximate chunk size in characters

        Returns:
            List of text chunks
        """
        # Simple chunking by paragraphs, then by size
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) < chunk_size:
                current_chunk += para + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para + "\n\n"

        if current_chunk:
            chunks.append(current_chunk.strip())

        return [c for c in chunks if c.strip()]

    def index_file(self, file_path: Path, source_type: str = "memory"):
        """
        Index a file for semantic search.

        Args:
            file_path: Path to file
            source_type: Type of file (memory, daily_log, skill)
        """
        if not file_path.exists():
            return

        # Check if file needs re-indexing
        current_hash = self._get_file_hash(file_path)
        file_key = str(file_path)

        if file_key in self.indexed_files and self.indexed_files[file_key] == current_hash:
            return  # Already indexed, no changes

        # Read and chunk file
        content = file_path.read_text()
        chunks = self._chunk_text(content)

        if not chunks:
            return

        # Generate embeddings
        embeddings = self.model.encode(chunks).tolist()

        # Create IDs for chunks
        ids = [f"{file_path.stem}_chunk_{i}" for i in range(len(chunks))]

        # Prepare metadata
        metadatas = [
            {
                "source": str(file_path),
                "source_type": source_type,
                "chunk_index": i,
                "total_chunks": len(chunks)
            }
            for i in range(len(chunks))
        ]

        # Add to collection (upsert to handle updates)
        self.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas
        )

        # Update tracked files
        self.indexed_files[file_key] = current_hash

    def index_workspace(self):
        """Index all memory files in workspace."""
        # Index MEMORY.md
        memory_file = self.workspace_dir / "MEMORY.md"
        if memory_file.exists():
            self.index_file(memory_file, "long_term_memory")

        # Index daily logs
        memory_dir = self.workspace_dir / "memory"
        if memory_dir.exists():
            for log_file in memory_dir.glob("*.md"):
                self.index_file(log_file, "daily_log")

        # Index skills (future)
        skills_dir = self.workspace_dir / "skills"
        if skills_dir.exists():
            for skill_file in skills_dir.glob("*.py"):
                self.index_file(skill_file, "skill")

    def search(self, query: str, n_results: int = 3) -> List[Dict[str, Any]]:
        """
        Semantic search over memories.

        Args:
            query: Search query
            n_results: Number of results to return

        Returns:
            List of matching memory chunks with metadata
        """
        # Generate query embedding
        query_embedding = self.model.encode(query).tolist()

        # Search
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results
        )

        # Format results
        formatted_results = []
        if results['documents'] and results['documents'][0]:
            for i in range(len(results['documents'][0])):
                formatted_results.append({
                    "content": results['documents'][0][i],
                    "source": results['metadatas'][0][i]['source'],
                    "source_type": results['metadatas'][0][i]['source_type'],
                    "similarity": 1 - results['distances'][0][i] if 'distances' in results else 1.0
                })

        return formatted_results

    def get_stats(self) -> Dict[str, Any]:
        """Get embedding statistics."""
        return {
            "indexed_files": len(self.indexed_files),
            "total_chunks": self.collection.count(),
            "model": self.model_name,
            "collection": self.collection.name
        }
