# Stop Runaway LangChain API Bills with Local SQLite Tracking

Cloud dashboards bill on a lag. By the time you get the email alert that your recursive agent is stuck in an infinite loop, your wallet is already drained. 

This repository contains **BotGuardian Lite**: a lightweight, synchronous Python wrapper and local SQLite ledger to track OpenAI API costs in real-time. No cloud dependencies, no subscriptions, no lag.

## The Free Solution (BotGuardian Lite)

The `guardian_lite.py` script provides a simple decorator that intercepts `openai>=1.0.0` SDK calls, extracts the token usage, calculates the estimated cost, and writes it to a local SQLite database.

**Setup:**
```bash
pip install openai>=1.0.0
