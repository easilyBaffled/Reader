# AudibleWeb

Turn URLs, files, and text into podcast episodes — published to a personal RSS feed you subscribe to in any podcast app.

## What it does

1. Submit a URL, PDF, text, or markdown
2. Extracts and cleans the content
3. Converts to speech via Kokoro TTS (with voice blending)
4. Publishes MP3 + RSS feed to GitHub Pages
5. Listen in your podcast app

## Quick Start

```bash
uv sync
cp .env.example .env  # add your GitHub PAT, optional LLM/Jina keys
uv run audibleweb
```

Open http://localhost:5000

## Requirements

- Python 3.12+
- FFmpeg
- Kokoro TTS server (or any OpenAI-compatible TTS API)
