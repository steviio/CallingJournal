# CallingJournal Development Environment Setup Guide

This document helps you quickly set up the development environment and run the AI-powered conversational diary system.

## Features

- Outbound phone calls via Twilio
- Real-time speech-to-text transcription (Deepgram Nova-3)
- Automatic VAD (Voice Activity Detection)
- AI-guided conversation with LLM responses
- Text-to-Speech for AI voice responses (OpenAI TTS / ElevenLabs)
- Automatic diary entry generation from conversations

---

## Prerequisites

### 1. Install ngrok

ngrok exposes your local server to the public internet, allowing Twilio to send webhooks to your development machine.

```bash
# macOS (via Homebrew)
brew install ngrok

# Or download directly from https://ngrok.com/download
```

Register for an ngrok account and configure your authtoken:
```bash
ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
```

### 2. Obtain API Keys

You will need the following API keys:

| Service | Purpose | Registration URL |
|---------|---------|------------------|
| **Twilio** | Phone calls | https://www.twilio.com |
| **Deepgram** | Speech transcription | https://deepgram.com |
| **OpenAI** | LLM + TTS | https://platform.openai.com |

Optional:
| Service | Purpose | Registration URL |
|---------|---------|------------------|
| **ElevenLabs** | Alternative TTS | https://elevenlabs.io |
| **Anthropic** | Alternative LLM | https://console.anthropic.com |
| **OpenRouter** | Multi-model LLM | https://openrouter.ai |

### 3. Configure Environment Variables

Copy the example environment file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env` with your API keys. See `.env.example` for detailed documentation of each variable.

**Minimum required for testing:**
- `OPENAI_API_KEY` - For LLM and TTS
- `DEEPGRAM_API_KEY` - For real-time transcription
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` - For calls
- Database credentials (`DB_*`)

---

## Running the Application

### Step 1: Install Dependencies

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Step 2: Start PostgreSQL and Redis

Using Docker:
```bash
# PostgreSQL
docker run -d --name postgres -p 5432:5432 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=calling_journal \
  postgres:15

# Redis (for background tasks)
docker run -d --name redis -p 6379:6379 redis:7
```

### Step 3: Start ngrok

In a separate terminal window:
```bash
ngrok http 8000
```

You will see output similar to:
```
Forwarding    https://xxxx-xxxx.ngrok-free.dev -> http://localhost:8000
```

**Note this HTTPS URL - you will need it for Twilio webhooks.**

### Step 4: Start the Server

In another terminal window:
```bash
source .venv/bin/activate
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The server will be available at `http://localhost:8000`. API documentation is available at `http://localhost:8000/docs`.

### Step 5: Configure Twilio Webhooks

In your Twilio Console, configure your phone number's voice webhook to point to:
```
https://xxxx-xxxx.ngrok-free.dev/webhooks/twilio/voice
```

Replace `xxxx-xxxx.ngrok-free.dev` with your actual ngrok URL.

### Step 6: Make a Test Call

Using curl:
```bash
curl -X POST "http://localhost:8000/calls" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{"phone_number": "+1XXXXXXXXXX"}'
```

Or using Python:
```python
import requests

response = requests.post(
    "http://localhost:8000/calls",
    headers={"Authorization": "Bearer YOUR_JWT_TOKEN"},
    json={"phone_number": "+1XXXXXXXXXX"}  # Your phone number
)
print(response.json())
```

---

## Conversation Flow

1. **Call Initiated** - Your phone receives an incoming call
2. **Greeting** - AI greets you with a time-appropriate message
3. **Conversation** - You speak, AI listens and responds
   - Deepgram transcribes your speech in real-time
   - LLM generates contextual responses
   - TTS converts responses to speech
4. **End Call** - Say "goodbye" or "I'm done" to end
5. **Diary Generated** - AI creates a first-person diary entry from the conversation

### Example Conversation

```
AI: "Good evening! I'm here to help you reflect on your day. How has your day been so far?"

You: "It was pretty stressful. I had a big presentation at work."

AI: "I hear you. Work presentations can be really nerve-wracking. How did it go?"

You: "Actually, it went better than I expected. My boss said I did a great job."

AI: "That's wonderful! It sounds like all your preparation paid off. How are you feeling about it now?"

You: "I'm done"

AI: "Thank you for sharing with me tonight. It sounds like despite the stress, you had a real win today. Sleep well!"
```

### Generated Diary Entry

```
December 6, 2024

Today was one of those days that started with my stomach in knots. I had a big presentation
at work that I'd been dreading for days. The build-up of anxiety was intense, but I kept
reminding myself that I was prepared.

And you know what? It actually went better than I expected! My boss even said I did a great
job, which meant so much to me. Looking back now, I realize how often I underestimate
myself. The stress beforehand felt overwhelming, but pushing through led to a genuine
moment of pride.

I'm grateful for the positive feedback and reminded that preparation really does pay off.
Tomorrow, I want to carry this confidence into whatever challenges come my way.
```

---

## Troubleshooting

### Common Issues

**"Deepgram auth failed"**
- Verify your `DEEPGRAM_API_KEY` is correct in `.env`

**No audio/transcription**
- Ensure ngrok is running and the URL is correctly configured
- Check that Twilio webhook URL matches your ngrok URL

**"Trial account" message**
- Twilio trial accounts require pressing a digit before the call connects
- Consider upgrading to a paid Twilio account for production use

**TTS not working**
- Verify `OPENAI_API_KEY` is set (required for OpenAI TTS)
- Check `TTS_PROVIDER`, `TTS_VOICE`, and `TTS_MODEL` are configured

**LLM responses slow**
- Consider using a faster model (e.g., `gpt-4o-mini` instead of `gpt-4-turbo`)
- Or use OpenRouter with a faster provider

---

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Twilio    │────▶│   FastAPI   │────▶│  Deepgram   │
│  (Phone)    │◀────│   Server    │◀────│   (STT)     │
└─────────────┘     └──────┬──────┘     └─────────────┘
                          │
                    ┌─────▼─────┐
                    │    LLM    │
                    │ (OpenAI/  │
                    │ Anthropic)│
                    └─────┬─────┘
                          │
                    ┌─────▼─────┐
                    │    TTS    │
                    │ (OpenAI/  │
                    │ElevenLabs)│
                    └───────────┘
```

**Data Flow:**
1. User speaks → Twilio streams audio → Deepgram transcribes
2. Transcription → LLM generates response
3. Response text → TTS generates audio → Twilio plays to user
4. On call end → LLM generates diary entry → Saved to database