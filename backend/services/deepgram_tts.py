import os
import httpx
import logging
import asyncio

log = logging.getLogger("uvicorn")

class DeepgramTTS:
    def __init__(self):
        self.api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            log.warning("⚠️  DEEPGRAM_API_KEY not configured. TTS will be disabled.")
        self.model = "aura-asteria-en"
        self.base_url = f"https://api.deepgram.com/v1/speak?model={self.model}&encoding=linear16&sample_rate=24000"

    async def generate_audio_stream(self, text: str):
        """
        Stream TTS audio from Deepgram for a given text chunk.
        Yields raw PCM audio bytes (24kHz, 16-bit, mono).
        """
        if not self.api_key:
            return

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "text": text
        }

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", self.base_url, headers=headers, json=data) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        log.error(f"❌  Deepgram TTS API error: {error_text}")
                        return
                    
                    # Yield chunks as they arrive from the network
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        yield chunk
        except asyncio.CancelledError:
            log.info("🛑  TTS generation cancelled (barge-in)")
            raise
        except Exception as e:
            log.error(f"❌  Deepgram TTS streaming error: {e}")
