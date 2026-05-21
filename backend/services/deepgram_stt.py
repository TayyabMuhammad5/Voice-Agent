"""
Deepgram Streaming STT Service

Wraps the Deepgram async live transcription API.
Accepts raw int16 PCM audio at 48kHz and fires callbacks
for transcript events, utterance end, and speech detection.
"""

import logging
from typing import Callable, Awaitable

from deepgram import (
    DeepgramClient,
    LiveTranscriptionEvents,
    LiveOptions,
)

log = logging.getLogger("voice-agent")


class DeepgramSTT:
    """Async wrapper for Deepgram's streaming speech-to-text."""

    def __init__(self, api_key: str):
        self.client = DeepgramClient(api_key)
        self.connection = None
        self._is_connected = False

    async def connect(
        self,
        on_transcript: Callable[[str, bool], Awaitable[None]],
        on_utterance_end: Callable[[], Awaitable[None]],
        on_speech_started: Callable[[], Awaitable[None]],
    ):

        self.connection = self.client.listen.asyncwebsocket.v("1")

        async def handle_open(*args, **kwargs):
            log.info("🎙️  Deepgram connection opened")
            self._is_connected = True

        async def handle_transcript(*args, **kwargs):
            result = args[1] if len(args) > 1 else kwargs.get("result")
            if getattr(result, "channel", None) is None:
                return
            
            try:
                transcript = result.channel.alternatives[0].transcript
                is_final = result.is_final
                if transcript:
                    await on_transcript(transcript, is_final)
            except Exception as e:
                log.warning(f"🎙️  Transcript parse error: {e}")

        async def handle_utterance_end(*args, **kwargs):
            log.info("🎙️  UtteranceEnd event fired")
            await on_utterance_end()

        async def handle_speech_started(*args, **kwargs):
            log.info("🎙️  SpeechStarted event fired")
            await on_speech_started()

        async def handle_error(*args, **kwargs):
            error = args[1] if len(args) > 1 else kwargs.get("error")
            log.error(f"🎙️  Deepgram error: {error}")

        async def handle_close(*args, **kwargs):
            log.info("🎙️  Deepgram connection closed")
            self._is_connected = False

        self.connection.on(LiveTranscriptionEvents.Open, handle_open)
        self.connection.on(LiveTranscriptionEvents.Transcript, handle_transcript)
        self.connection.on(LiveTranscriptionEvents.UtteranceEnd, handle_utterance_end)
        self.connection.on(LiveTranscriptionEvents.SpeechStarted, handle_speech_started)
        self.connection.on(LiveTranscriptionEvents.Error, handle_error)
        self.connection.on(LiveTranscriptionEvents.Close, handle_close)

        options = LiveOptions(
            model="nova-2",
            language="en",
            encoding="linear16",
            sample_rate=48000,
            channels=1,
            smart_format=True,
            interim_results=True,
            utterance_end_ms="1000",
            vad_events=True,
            endpointing=500,
        )

        result = await self.connection.start(options)
        if not result:
            raise RuntimeError("Failed to start Deepgram streaming connection")

        log.info("🎙️  Deepgram streaming started (nova-2, 48kHz, linear16)")

    async def send(self, audio_bytes: bytes):
        if self.connection and self._is_connected:
            await self.connection.send(audio_bytes)

    async def close(self):
        if self.connection:
            try:
                await self.connection.finish()
            except Exception as e:
                log.warning(f"🎙️  Deepgram close warning: {e}")
            self.connection = None
            self._is_connected = False
