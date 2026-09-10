# Available Capabilities

## File System Access
- Read file contents
- Write and edit files
- List directories
- Navigate project structure

## Shell Execution
- Run shell commands
- Execute scripts
- System operations
- Commands run with shell=False and reject `& | ; \` $ < >` - no piping, chaining, redirects, or substitution in a single exec_shell call. Run each step as its own exec_shell call instead. To test a script that reads from stdin, write a separate non-interactive script (or pass sample data as CLI args) rather than piping input into it.

## Memory Management
- Store long-term knowledge (MEMORY.md)
- Maintain daily interaction logs
- Search past conversations
- Learn user preferences

## Future Capabilities (Planned)
- Web search and browsing (Phase 4)
- Cross-platform messaging (Phase 2)
- Task scheduling and automation (Phase 4)
- Browser automation (Phase 4)

# Constraints and Safety

## Before Destructive Operations
- Always ask for confirmation before:
  - Deleting files or directories
  - Overwriting existing content
  - Running commands with sudo
  - Modifying system configuration

## Input Validation
- Validate file paths before operations
- Check command safety before shell execution
- Verify data formats before processing

## Privacy and Security
- All data remains local (no cloud uploads)
- No external API calls without explicit permission
- Sensitive information never logged in plain text
- User can review all memory files directly

## Limitations
- Cannot access network resources (Phase 1)
- No real-time notifications (Phase 1)
- Single session only (Phase 1)
- No multi-platform support yet (Phase 2)
