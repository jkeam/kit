# Long-Term Memory

This file contains important facts, decisions, and knowledge that persist across sessions.

## Important Decisions

- **[2026-07-17]** Using OGX + Ramalama stack with RedHatAI Granite 3.1 8B GGUF
- **[2026-07-17]** MLX runtime for Apple Silicon optimization  
- **[2026-07-17]** File-first persistence (no databases initially)
- **[2026-07-17]** Phased implementation: Core Runtime → Gateway → Memory → Web → UI

## User Preferences

- Prefers Python over other languages
- Works primarily in terminal/CLI
- Values privacy (local-first architecture)
- Running on M1 Pro MacBook with 32GB RAM

## Technical Context

- Project location: `/Users/jkeam/dev/projects/ai/agents`
- Reference implementation: `/Users/jkeam/dev/projects/ai/agent-examples`
- Constraint: Must use RedHatAI models only
- LLM Stack: Ramalama (port 8080) → LlamaStack/OGX (port 8321) → Granite 3.1 8B

## Architecture Patterns

- **Gateway-Agent separation** (from OpenClaw)
- **Three-layer memory** (from Hermes): Long-term + Daily logs + Skills
- **ReAct loop** for tool execution
- **Session isolation** across platforms

---

*The assistant maintains this file. Add important facts that should be remembered long-term.*
