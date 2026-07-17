# Long-Term Memory

This file contains important facts, decisions, and knowledge that persist across sessions.

## Important Decisions

- **[2026-07-17]** MLX runtime for Apple Silicon optimization  
- **[2026-07-17]** File-first persistence (no databases initially)
- **[2026-07-17]** Phased implementation: Core Runtime → Gateway → Memory → Web → UI

## User Preferences

- Prefers Python over other languages
- Works primarily in terminal/CLI
- Values privacy (local-first architecture)

## Technical Context

- Constraint: Must use RedHatAI models only

## Architecture Patterns

- **Gateway-Agent separation** (from OpenClaw)
- **Three-layer memory** (from Hermes): Long-term + Daily logs + Skills
- **ReAct loop** for tool execution
- **Session isolation** across platforms

---

*The assistant maintains this file. Add important facts that should be remembered long-term.*
