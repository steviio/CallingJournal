"""
WebSocket endpoints for Twilio Media Streams.
Implements bidirectional conversation with real-time transcription (Deepgram),
LLM response generation, and TTS playback.
"""
import json
import asyncio
import base64
import sys
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import websockets

from src.config import settings
from src.services.conversation_service import conversation_service, ConversationContext
from src.services.tts_service import tts_service


def log(msg):
    """Print and flush immediately."""
    print(msg, flush=True)
    sys.stdout.flush()


router = APIRouter(prefix="/streams", tags=["Streams"])


def get_deepgram_ws_url() -> str:
    """Build Deepgram WebSocket URL from config settings."""
    return (
        "wss://api.deepgram.com/v1/listen"
        f"?model={settings.deepgram_model}"
        f"&language={settings.deepgram_language}"
        f"&encoding={settings.deepgram_encoding}"
        f"&sample_rate={settings.deepgram_sample_rate}"
        f"&channels={settings.deepgram_channels}"
        "&punctuate=true"
        "&interim_results=true"
        f"&endpointing={settings.deepgram_endpointing}"
        "&vad_events=true"
        "&smart_format=true"
    )


class CallState:
    """Track call state for bidirectional conversation."""

    def __init__(self):
        self.transcript_buffer = ""
        self.all_utterances = []
        self.utterance_count = 0
        self.is_speaking = False
        self.is_ai_speaking = False
        self.context: Optional[ConversationContext] = None
        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.pending_response: Optional[asyncio.Task] = None


async def send_audio_to_twilio(twilio_ws: WebSocket, stream_sid: str, audio_data: bytes):
    """
    Send audio data to Twilio in chunks.

    Args:
        twilio_ws: Twilio WebSocket connection
        stream_sid: Twilio stream SID
        audio_data: Audio bytes in mulaw 8kHz format
    """
    chunk_size = 160  # 20ms of audio at 8kHz
    for i in range(0, len(audio_data), chunk_size):
        chunk = audio_data[i:i + chunk_size]
        # Pad if needed
        if len(chunk) < chunk_size:
            chunk = chunk + b'\xff' * (chunk_size - len(chunk))

        media_message = {
            "event": "media",
            "streamSid": stream_sid,
            "media": {
                "payload": base64.b64encode(chunk).decode('utf-8')
            }
        }
        await twilio_ws.send_text(json.dumps(media_message))
        await asyncio.sleep(0.02)  # 20ms pacing


async def speak_response(twilio_ws: WebSocket, state: CallState, text: str):
    """
    Generate TTS and send to Twilio.

    Args:
        twilio_ws: Twilio WebSocket connection
        state: Call state
        text: Text to speak
    """
    state.is_ai_speaking = True
    log(f"🤖 AI: {text}")

    try:
        # Generate TTS audio
        audio_data = await tts_service.synthesize(text)

        # Send to Twilio
        await send_audio_to_twilio(twilio_ws, state.stream_sid, audio_data)

    except Exception as e:
        log(f"❌ TTS error: {e}")
    finally:
        state.is_ai_speaking = False


async def process_user_utterance(twilio_ws: WebSocket, state: CallState, utterance: str):
    """
    Process a completed user utterance and generate AI response.

    Args:
        twilio_ws: Twilio WebSocket connection
        state: Call state
        utterance: User's transcribed speech
    """
    if not state.context:
        return

    log(f"👤 User: {utterance}")

    # Generate AI response
    try:
        response = await conversation_service.generate_response(state.context, utterance)
        await speak_response(twilio_ws, state, response)

        # Check if conversation is ending
        if state.context.is_ending:
            log("🔚 User requested to end conversation")

    except Exception as e:
        log(f"❌ Error generating response: {e}")
        await speak_response(twilio_ws, state, "I'm sorry, I had trouble understanding. Could you please repeat that?")


async def deepgram_receiver(dg_ws, state: CallState, twilio_ws: WebSocket):
    """Receive transcriptions from Deepgram and trigger AI responses."""
    try:
        async for message in dg_ws:
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type == "SpeechStarted":
                if not state.is_speaking and not state.is_ai_speaking:
                    log(f"🎤 User started speaking")
                    state.is_speaking = True

                    # Cancel any pending AI response if user interrupts
                    if state.pending_response and not state.pending_response.done():
                        state.pending_response.cancel()

            elif msg_type == "Results":
                channel = data.get("channel", {})
                alternatives = channel.get("alternatives", [])

                if alternatives:
                    transcript = alternatives[0].get("transcript", "")
                    is_final = data.get("is_final", False)
                    speech_final = data.get("speech_final", False)

                    if transcript:
                        if is_final:
                            state.transcript_buffer += transcript + " "
                            log(f"   📝 [{transcript}]")
                        else:
                            # Interim result
                            pass

                    # speech_final = endpoint detected (user stopped speaking)
                    if speech_final and state.transcript_buffer.strip():
                        state.utterance_count += 1
                        final_text = state.transcript_buffer.strip()
                        state.all_utterances.append(final_text)

                        log(f"🔇 Utterance #{state.utterance_count} complete")

                        # Process utterance and generate response
                        state.pending_response = asyncio.create_task(
                            process_user_utterance(twilio_ws, state, final_text)
                        )

                        # Reset buffer
                        state.transcript_buffer = ""
                        state.is_speaking = False

            elif msg_type == "Metadata":
                log(f"📊 Deepgram connected: model={data.get('model_info', {}).get('name', 'unknown')}")

            elif msg_type == "UtteranceEnd":
                if state.transcript_buffer.strip():
                    state.utterance_count += 1
                    final_text = state.transcript_buffer.strip()
                    state.all_utterances.append(final_text)

                    log(f"🔇 [UtteranceEnd] #{state.utterance_count}")

                    state.pending_response = asyncio.create_task(
                        process_user_utterance(twilio_ws, state, final_text)
                    )

                    state.transcript_buffer = ""
                    state.is_speaking = False

    except websockets.exceptions.ConnectionClosed:
        log(f"🔌 Deepgram connection closed")
    except Exception as e:
        log(f"❌ Deepgram receiver error: {e}")


@router.websocket("/twilio")
async def websocket_endpoint(websocket: WebSocket):
    """
    Handle Twilio Media Streams with bidirectional AI conversation.

    Flow:
    1. Twilio connects and starts streaming audio
    2. Audio is forwarded to Deepgram for real-time transcription
    3. When user finishes speaking (VAD), LLM generates response
    4. Response is converted to speech (TTS) and sent back to Twilio
    5. Conversation continues until user ends or hangs up
    6. On disconnect, diary entry is generated from conversation
    """
    log("=" * 60)
    log("🎙️ New call incoming...")
    await websocket.accept()
    log("✅ Twilio WebSocket accepted")

    state = CallState()

    try:
        # Connect to Deepgram
        log(f"🔌 Connecting to Deepgram...")

        async with websockets.connect(
            get_deepgram_ws_url(),
            additional_headers={
                "Authorization": f"Token {settings.deepgram_api_key}"
            }
        ) as dg_ws:
            log(f"✅ Deepgram connected")

            # Wait for Twilio to send 'start' event
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                event = message.get('event', 'unknown')

                if event == 'connected':
                    log(f"✅ Twilio protocol: {message.get('protocol')}")
                elif event == 'start':
                    start_data = message.get('start', {})
                    state.stream_sid = start_data.get('streamSid')
                    state.call_sid = start_data.get('callSid')
                    log(f"✅ Stream started: {state.call_sid}")

                    # Start conversation
                    state.context = conversation_service.start_conversation(
                        call_id=state.call_sid
                    )

                    # Send initial greeting
                    greeting = await conversation_service.generate_greeting(state.context)
                    await speak_response(websocket, state, greeting)

                    break

            # Create task for receiving Deepgram transcriptions
            receiver_task = asyncio.create_task(
                deepgram_receiver(dg_ws, state, websocket)
            )

            # Forward audio from Twilio to Deepgram
            media_count = 0
            try:
                while True:
                    data = await websocket.receive_text()
                    message = json.loads(data)
                    event = message.get('event', 'unknown')

                    if event == 'media':
                        # Don't forward audio while AI is speaking (echo cancellation)
                        if not state.is_ai_speaking:
                            media_count += 1
                            payload = message['media']['payload']
                            audio_bytes = base64.b64decode(payload)
                            await dg_ws.send(audio_bytes)

                            if media_count % 500 == 0:
                                log(f"📦 Packets: {media_count}")

                    elif event == 'stop':
                        log(f"🛑 Stream stopped (packets: {media_count})")
                        break

            except WebSocketDisconnect:
                log(f"⚠️ Twilio disconnected (packets: {media_count})")

            # Cancel receiver task
            receiver_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass

            # Close Deepgram connection
            await dg_ws.send(json.dumps({"type": "CloseStream"}))

    except websockets.exceptions.InvalidStatusCode as e:
        log(f"❌ Deepgram auth failed: {e}")
    except Exception as e:
        log(f"❌ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # End conversation and generate diary
        if state.context:
            context = conversation_service.end_conversation(state.call_sid)
            if context and context.messages:
                log(f"")
                log(f"📋 Conversation Summary ({state.utterance_count} utterances):")
                for i, text in enumerate(state.all_utterances, 1):
                    log(f"   {i}. {text}")

                # Generate diary entry
                try:
                    log(f"")
                    log(f"📔 Generating diary entry...")
                    diary_entry = await conversation_service.generate_diary_entry(context)
                    log(f"   Title: {diary_entry.get('title', 'Untitled')}")
                    log(f"   Mood: {diary_entry.get('mood', 'unknown')}")
                    log(f"   Content preview: {diary_entry.get('content', '')[:100]}...")

                    # TODO: Save diary entry to database
                    # This will be handled by the webhook when call status is 'completed'

                except Exception as e:
                    log(f"❌ Error generating diary: {e}")

        log(f"")
        log(f"🔚 Call ended")
        log("=" * 60)