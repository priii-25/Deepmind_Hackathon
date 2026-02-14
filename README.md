# Teems

**Your AI Creative Team, One Conversation.**

It gives you a full AI creative team photographer, video creator, social media manager, presentation designer, meeting assistant all coordinated by one Chief of Staff named **Eve**.

## Architecture

```mermaid
flowchart TD
    User([User]) -->|chat| Eve[Eve - Chief of Staff]

    Eve -->|delegates| Vera[Vera - Fashion Photographer]
    Eve -->|delegates| Kai[Kai - UGC Creator]
    Eve -->|delegates| Chad[Chad - Social Media Manager]
    Eve -->|delegates| Noa[Noa - Presentation Maker]
    Eve -->|delegates| Ivy[Ivy - Meeting Assistant]

    Vera -->|Gemini 2.5 Flash Image| Photos[AI Photoshoots]
    Kai -->|AI Pipeline| Videos[UGC Videos]
    Chad -->|YouTube Data API| YouTube[YouTube Upload]
    Noa -->|Gemini nanobanana + python-pptx| PPTX[Slide Decks]
    Ivy -->|MeetingBaaS| Notes[Meeting Notes]
```

## Agent Pipeline

```mermaid
sequenceDiagram
    participant U as User
    participant E as Eve
    participant A as Agent
    participant S as Service
    participant DB as Storage

    U->>E: "Make me a presentation about AI"
    E->>E: Tool call: agent_presentation
    E->>A: Delegate task
    A->>S: Generate content (Gemini API)
    S-->>A: Result (images, files)
    A->>DB: Save to local/S3 storage
    A-->>E: AgentResponse (content + media URLs)
    E-->>U: SSE stream (text + previews + download)
```

## Meet the Team

| Agent | Role | Tech Stack | Status |
|-------|------|-----------|--------|
| **Eve** | Chief of Staff / Orchestrator | Gemini 3 Flash, tool calling, memory | Live |
| **Vera** | Fashion Photographer | Gemini 2.5 Flash Image, multi-turn chat | Live |
| **Kai** | UGC Video Creator | AI avatars, ElevenLabs, lip-sync | Live |
| **Chad** | Social Media Manager | YouTube Data API v3, OAuth 2.0 | Live |
| **Noa** | Presentation Maker | Gemini nanobanana, python-pptx | Live |
| **Ivy** | Meeting Assistant | MeetingBaaS, calendar sync |Live |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys (GEMINI_API_KEY required)

# Run
python main.py
# Open http://localhost:8000
```
