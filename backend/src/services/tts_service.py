"""
Text-to-Speech service for generating AI voice responses.
Supports OpenAI TTS and ElevenLabs.
"""
import io
import audioop
from abc import ABC, abstractmethod
from typing import Optional
import httpx
import openai

from src.config import settings


class ITTSService(ABC):
    """Interface for TTS service implementations."""

    @abstractmethod
    async def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        """
        Synthesize speech from text.

        Args:
            text: Text to synthesize
            voice: Optional voice override

        Returns:
            Audio bytes in mulaw 8kHz format (for Twilio)
        """
        pass


class OpenAITTSService(ITTSService):
    """OpenAI TTS implementation."""

    def __init__(self):
        self.client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.tts_model
        self.voice = settings.tts_voice

    async def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        """Synthesize speech using OpenAI TTS API."""
        response = await self.client.audio.speech.create(
            model=self.model,
            voice=voice or self.voice,
            input=text,
            response_format="pcm"  # Raw PCM audio
        )

        # OpenAI returns 24kHz 16-bit mono PCM
        pcm_data = response.content

        # Resample from 24kHz to 8kHz for Twilio
        # First convert to 8kHz
        resampled = audioop.ratecv(pcm_data, 2, 1, 24000, 8000, None)[0]

        # Convert to mulaw for Twilio
        mulaw_data = audioop.lin2ulaw(resampled, 2)

        return mulaw_data


class ElevenLabsTTSService(ITTSService):
    """ElevenLabs TTS implementation."""

    BASE_URL = "https://api.elevenlabs.io/v1"

    def __init__(self):
        self.api_key = settings.elevenlabs_api_key
        self.voice = settings.tts_voice
        self.model = settings.tts_model

    async def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        """Synthesize speech using ElevenLabs API."""
        voice_id = voice or self.voice

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "text": text,
                    "model_id": self.model,
                    "output_format": "pcm_16000"  # 16kHz PCM
                },
                timeout=30.0
            )
            response.raise_for_status()
            pcm_data = response.content

        # Resample from 16kHz to 8kHz for Twilio
        resampled = audioop.ratecv(pcm_data, 2, 1, 16000, 8000, None)[0]

        # Convert to mulaw for Twilio
        mulaw_data = audioop.lin2ulaw(resampled, 2)

        return mulaw_data


class TTSServiceFactory:
    """Factory for creating TTS service instances."""

    @staticmethod
    def create(provider: Optional[str] = None) -> ITTSService:
        """Create TTS service based on provider."""
        provider = provider or settings.tts_provider

        if provider == "openai":
            return OpenAITTSService()
        elif provider == "elevenlabs":
            return ElevenLabsTTSService()
        else:
            raise ValueError(f"Unsupported TTS provider: {provider}")


# Default TTS service instance
tts_service = TTSServiceFactory.create()