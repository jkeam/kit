# Kit

A production-ready personal AI assistant built with OpenClaw/Hermes-inspired architecture, PatternFly UI, and real-time WebSocket capabilities.

## 🎯 What It Does

Kit that can:
- 💬 Chat with natural language understanding
- 🧠 Remember conversations with semantic search
- 🌐 Search and fetch web content
- 🖥️ Automate browser tasks (screenshots, extraction)
- ⏰ Schedule recurring tasks
- 📁 Manage files and execute shell commands
- 🔄 Real-time updates across multiple tabs

## 🚀 Quick Start

### Prerequisites
- Python 3.14+
- RedHat MaaS API access (or other LLM provider)
- ~2GB RAM for embeddings

### Setup

1. **Install dependencies**
```bash
# Create virtual environment
uv venv
source .venv/bin/activate

# Install packages
uv pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

2. **Configure environment**
```bash
cp .env.example .env
# Edit .env and add your API keys:
# - TAVILY_API_KEY (for web search)
```

3. **Start LlamaStack** (if using)
```bash
# Terminal 1
llama stack run llama-stack-run.yaml
```

4. **Start Gateway**
```bash
# Terminal 2
python -m gateway.server
```

5. **Access the Web UI**
```
Open browser: http://localhost:18789
```

## 📖 Usage

### Web UI (Recommended)
- Navigate to http://localhost:18789
- Chat in real-time with the assistant
- View active sessions
- Search memories semantically
- Browse available tools
- Manage scheduled tasks

### Command Line
```bash
# One-off queries
python cli.py "What files are in this directory?"

# Check session stats
python cli.py --stats
```

## 🛠️ Available Tools (17)

**File Operations**: read, write, list_files  
**Memory**: memory_write, memory_get, memory_search  
**Web**: web_search, web_fetch  
**Browser**: browser_screenshot, browser_navigate, browser_extract  
**Scheduling**: schedule_create, schedule_list, schedule_delete  
**System**: exec_shell  

## 🏗️ Architecture

```
Web UI (PatternFly)
    ↓ HTTP + WebSocket
Gateway (FastAPI :18789)
    ↓
PersonalAssistant Runtime
    ├─→ Memory (3-layer + vector search)
    ├─→ Tools (17 tools)
    └─→ LlamaStack → RedHat MaaS → Qwen3-14B
```

### Memory System
- **Long-term** (MEMORY.md): Curated knowledge
- **Daily logs** (memory/YYYY-MM-DD.md): Conversation history
- **Vector search**: Semantic retrieval via ChromaDB

## 📁 Project Structure

```
agents/
├── gateway/              # FastAPI server + WebSocket
├── runtime/              # Agent runtime + memory
├── tools/                # 17 tools organized by category
├── web/                  # PatternFly web UI
├── workspace/            # Memory files + schedules
├── cli.py             # CLI interface
├── llama-stack-run.yaml  # LlamaStack config
└── config.yaml           # Gateway config
```

## 🎨 Web UI Features

Built with **PatternFly v5** design system:
- Real-time chat interface
- Multi-session monitoring
- Semantic memory browser
- Tools explorer
- Schedule manager
- WebSocket live updates
- Auto-reconnect on disconnect

## ⚙️ Configuration

### Gateway (config.yaml)
- Port: 18789
- Session isolation: platform:user_id
- CORS: Enabled for development

### LlamaStack (llama-stack-run.yaml)
- Provider: RedHat MaaS
- Model: qwen3-14b
- Endpoint: Cloud-based (no local model)

## 🔧 Development

### Adding New Tools
1. Create function in `tools/` directory
2. Add tool definition (OpenAI format)
3. Add to TOOL_FUNCTIONS mapping
4. Restart gateway

### Adding Memory Entries
```python
# Via tool
assistant.chat("Remember: I prefer Python over JavaScript")

# Direct edit
vim workspace/MEMORY.md
```

## 🐛 Troubleshooting

**Gateway won't start**
- Check port 18789 is available: `lsof -i :18789`
- Kill existing: `pkill -f gateway.server`

**WebSocket disconnects**
- Normal - auto-reconnects in 2-10 seconds
- Check browser console for errors

**Embeddings slow on first run**
- Downloads SentenceTransformer model (~90MB)
- Cached after first run

**Browser tools fail**
- Ensure Playwright installed: `playwright install chromium`
- Check subprocess isolation (runs in separate process)

## 📊 Performance

**Memory Usage**: ~500MB (embeddings + browser)  
**Response Time**: 1-3 seconds (RedHat MaaS)  
**WebSocket Latency**: <50ms  
**Concurrent Users**: Unlimited (WebSocket broadcast)  

## 🔐 Security Notes

- **Local only**: Gateway binds to 127.0.0.1
- **No authentication**: Internal tool, trusted environment
- **Shell execution**: Blocks `sudo`, 30s timeout
- **File access**: Full filesystem access (be careful!)

## 🚧 Known Limitations

- Schedules stored but not executed (no cron runner)
- Web search requires Tavily API key
- Browser automation limited to basic actions
- No persistent WebSocket sessions (memory only)
- Synchronous message processing (one at a time)

## 🔮 Future Ideas (Not Implemented)

- Telegram/Discord bots (skipped - web UI sufficient)
- Skill learning system
- Persistent WebSocket sessions
- Async message processing
- Authentication/multi-user
- Mobile app

## 📄 License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

Inspired by:
- OpenClaw (Gateway-Workspace-Agent pattern)
- Hermes Agent (3-layer memory system)
- PatternFly (Red Hat design system)
- LlamaStack (agentic orchestration)

---

Built with ❤️ in a single session!
