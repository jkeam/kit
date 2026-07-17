# 🚀 Quick Start Guide

## Start Your Kit in 3 Steps

### Step 1: Start the Gateway
```bash
python -m gateway.server
```

You should see:
```
✅ Gateway server started
📡 Ready to handle multi-platform requests
INFO: Uvicorn running on http://127.0.0.1:18789
```

### Step 2: Open the Web UI
```bash
open http://localhost:18789
```

Or manually navigate to: **http://localhost:18789**

### Step 3: Start Chatting!

In the web UI, you'll see:
- ✅ "Connected to Kit"
- ✅ "Real-time updates enabled"

Try these example queries:

**File Operations**
```
What files are in this directory?
Read the README.md file
Create a new file called test.txt with "Hello World"
```

**Memory**
```
Remember: I prefer Python over JavaScript
What do you remember about my preferences?
Search my memories for information about Python
```

**Web**
```
Fetch the content from https://example.com
```

**Browser**
```
Take a screenshot of https://github.com
```

**Scheduling**
```
Create a schedule to check system status every day at 9am
List all my schedules
```

**System**
```
List all Python files in the current directory
Show disk usage
```

## Multi-Tab Real-Time Demo

1. Open http://localhost:18789 in **two browser tabs**
2. In tab 1, send a message
3. Watch it appear in **both tabs** instantly via WebSocket!

## Command Line Usage

Prefer terminal? Use the CLI:
```bash
python cli.py "What can you do?"
python cli.py "List all files in workspace/"
python cli.py --stats
```

## What's Happening Behind the Scenes?

```
Your Message
    ↓
Web UI (PatternFly)
    ↓ WebSocket broadcast
Gateway (FastAPI)
    ↓
PersonalAssistant
    ├─→ Loads memory (MEMORY.md + daily logs)
    ├─→ Semantic search (vector embeddings)
    ├─→ Calls Qwen3-14B via RedHat MaaS
    └─→ Executes tools as needed
    ↓
Response back to you!
```

## Troubleshooting

**Port already in use?**
```bash
pkill -f gateway.server
python -m gateway.server
```

**WebSocket not connecting?**
- Check browser console (F12)
- Should see "WebSocket connected"
- Auto-reconnects in 2-10 seconds if disconnected

**Slow first message?**
- Embedding model downloads first time (~90MB)
- Subsequent messages are fast

## Next Steps

- ✅ Explore the **Sessions** tab to see active sessions
- ✅ Try **Memory** tab to search your conversation history
- ✅ Check **Tools** tab to see all 17 available tools
- ✅ Use **Schedules** tab to manage recurring tasks

Enjoy your personal AI assistant! 🎉
