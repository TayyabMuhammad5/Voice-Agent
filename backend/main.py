"""
Voice AI Agent — FastAPI Backend
Phase 2: Terminal-Only Pipeline

Pipeline:
    Mic audio (float32 PCM via WebSocket)
    → Convert float32 → int16
    → Deepgram Streaming STT (nova-2)
    → Groq / LangChain (Llama-3, streaming)
    → TextChunker (sentence splitting)
    → JSON messages back to frontend + print to terminal

No TTS in this phase — all LLM output is text only.
"""

import asyncio
import logging
import os
import time

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from services.deepgram_stt import DeepgramSTT
from services.deepgram_tts import DeepgramTTS
from services.llm_chain import LLMChain
from services.text_chunker import TextChunker

# ─── Environment ────────────────────────────────────────────────────────────
load_dotenv()
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("voice-agent")

# ─── App ────────────────────────────────────────────────────────────────────
app = FastAPI(title="Voice AI Agent", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

tts = DeepgramTTS()


@app.get("/health")
async def health():
    return {"status": "ok", "phase": 2}


# ─── LLM Response Generator ────────────────────────────────────────────
async def generate_response(
    ws: WebSocket,
    ws_lock: asyncio.Lock,
    llm: LLMChain,
    user_text: str,
):
    """Stream LLM response: chunk into short phrases, TTS via queue."""

    async def safe_send(data: dict):
        async with ws_lock:
            try:
                await ws.send_json(data)
            except Exception:
                pass

    async def send_audio(audio_bytes: bytes):
        async with ws_lock:
            try:
                await ws.send_bytes(audio_bytes)
            except Exception:
                pass

    # ── TTS worker: consumes phrases from queue in order ────────────
    tts_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def tts_worker():
        """Process TTS phrases sequentially. Prefetching happens naturally 
        because downloading is faster than playback."""
        while True:
            phrase = await tts_queue.get()
            if phrase is None:  # poison pill = done
                break
            try:
                first_chunk = True
                audio_buffer = bytearray()
                
                async for chunk in tts.generate_audio_stream(phrase):
                    audio_buffer.extend(chunk)
                    
                    # Group into ~340ms chunks (16KB) to prevent micro-cuts
                    if len(audio_buffer) >= 16384:
                        if first_chunk:
                            # Send text EXACTLY when first audio block is ready!
                            await safe_send({"type": "response_chunk", "text": phrase + " "})
                            first_chunk = False
                        
                        await send_audio(bytes(audio_buffer))
                        audio_buffer.clear()
                
                if audio_buffer:
                    if first_chunk:
                        await safe_send({"type": "response_chunk", "text": phrase + " "})
                    await send_audio(bytes(audio_buffer))
                    
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"❌  TTS worker error: {e}")

    await safe_send({"type": "response_start"})

    chunker = TextChunker()
    full_response = ""
    chunk_count = 0
    t_start = time.perf_counter()

    # Start TTS worker (runs concurrently with LLM streaming)
    tts_task = asyncio.create_task(tts_worker())

    try:
        async for token in llm.stream(user_text):
            full_response += token
            # We no longer send tokens immediately to avoid text jumping ahead of audio.

            # Check for complete phrases
            phrases = chunker.add(token)
            for phrase in phrases:
                chunk_count += 1
                log.info(f"💬  [{chunk_count}] {phrase}")
                await tts_queue.put(phrase)

        # Flush remaining text
        remaining = chunker.flush()
        if remaining:
            chunk_count += 1
            log.info(f"💬  [{chunk_count}] {remaining}")
            await tts_queue.put(remaining)

        # Signal TTS worker to finish, then wait for it
        await tts_queue.put(None)
        await tts_task

        # Save to conversation history
        llm.add_response(full_response)
        llm.trim_history()

        elapsed = time.perf_counter() - t_start
        await safe_send({"type": "response_end", "full_text": full_response})
        log.info(
            f"✅  Response complete │ "
            f"{chunk_count} chunks │ "
            f"{len(full_response)} chars │ "
            f"{elapsed:.2f}s"
        )

    except asyncio.CancelledError:
        # Barge-in — cancel TTS worker and save partial response
        log.info("🛑  LLM generation cancelled (barge-in)")
        tts_task.cancel()
        try:
            await tts_task
        except (asyncio.CancelledError, Exception):
            pass
        if full_response:
            llm.add_response(full_response + "…")
        raise
    except Exception as e:
        log.error(f"❌  LLM generation failed: {e}")
        await safe_send({"type": "error", "message": f"LLM Error: {e}"})


# ─── WebSocket Pipeline ────────────────────────────────────────────────────
@app.websocket("/ws/audio")
async def audio_ws(ws: WebSocket):
    await ws.accept()
    log.info("🔌  WebSocket connected")

    # ── Validate API keys ───────────────────────────────────────────────
    if not DEEPGRAM_API_KEY or DEEPGRAM_API_KEY == "your_deepgram_api_key_here":
        await ws.send_json({"type": "error", "message": "DEEPGRAM_API_KEY not configured — check backend/.env"})
        await ws.close()
        return

    if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_api_key_here":
        await ws.send_json({"type": "error", "message": "GROQ_API_KEY not configured — check backend/.env"})
        await ws.close()
        return

    # ── Initialize services ─────────────────────────────────────────────
    stt = DeepgramSTT(DEEPGRAM_API_KEY)
    llm = LLMChain(GROQ_API_KEY)
    ws_lock = asyncio.Lock()

    # Mutable state shared across async callbacks
    transcript_buffer: list[str] = []
    llm_task: asyncio.Task | None = None
    silence_timer: asyncio.Task | None = None

    async def wait_for_silence():
        try:
            # 500ms of silence = trigger AI response!
            await asyncio.sleep(0.5)
            await on_utterance_end()
        except asyncio.CancelledError:
            pass

    async def safe_send(data: dict):
        async with ws_lock:
            try:
                await ws.send_json(data)
            except Exception:
                pass

    # ── Deepgram Callbacks ──────────────────────────────────────────────

    async def on_transcript(text: str, is_final: bool):
        label = "FINAL" if is_final else "interim"
        log.info(f"🎙️  [{label}] {text}")
        await safe_send({
            "type": "transcript",
            "text": text,
            "is_final": is_final,
        })
        if is_final:
            transcript_buffer.append(text)
            # Reset silence timer
            nonlocal silence_timer
            if silence_timer and not silence_timer.done():
                silence_timer.cancel()
            silence_timer = asyncio.create_task(wait_for_silence())

    async def on_utterance_end():
        nonlocal llm_task, silence_timer

        if silence_timer and not silence_timer.done():
            silence_timer.cancel()

        if not transcript_buffer:
            return

        user_text = " ".join(transcript_buffer).strip()
        transcript_buffer.clear()

        if not user_text:
            return

        log.info(f"🗣️  Utterance complete: \"{user_text}\"")

        # Cancel any in-flight LLM generation
        if llm_task and not llm_task.done():
            llm_task.cancel()
            try:
                await llm_task
            except (asyncio.CancelledError, Exception):
                pass
            await safe_send({"type": "response_interrupted"})

        # Launch new LLM generation
        llm_task = asyncio.create_task(
            generate_response(ws, ws_lock, llm, user_text)
        )

    async def on_speech_started():
        nonlocal llm_task, silence_timer

        if silence_timer and not silence_timer.done():
            silence_timer.cancel()

        # We no longer mute TTS here because Deepgram's SpeechStarted is 
        # highly sensitive to speaker echo and causes audio skipping.
        # Barge-in interruption will be handled by on_utterance_end instead.

    # ── Connect to Deepgram ─────────────────────────────────────────────
    try:
        await stt.connect(on_transcript, on_utterance_end, on_speech_started)
    except Exception as e:
        log.error(f"❌  Deepgram connection failed: {e}")
        await ws.send_json({
            "type": "error",
            "message": f"Speech recognition connection failed: {e}",
        })
        await ws.close()
        return

    # ── Main Audio Receive Loop ─────────────────────────────────────────
    chunks_received = 0

    try:
        while True:
            data: bytes = await ws.receive_bytes()
            chunks_received += 1

            # Payload: [8-byte float64 timestamp][float32 PCM samples]
            if len(data) <= 8:
                continue

            # The frontend now sends raw little-endian int16 PCM bytes!
            # We can forward it directly to Deepgram without ANY math/conversion.
            audio_bytes = data[8:]
            await stt.send(audio_bytes)

            if chunks_received % 10 == 0:
                # Just for volume logging: parse as int16
                pcm_i16 = np.frombuffer(audio_bytes, dtype=np.int16)
                max_vol = np.max(np.abs(pcm_i16)) if len(pcm_i16) > 0 else 0
                log.info(f"📦  Audio chunks: {chunks_received} │ max_volume={max_vol} / 32767")

    except WebSocketDisconnect:
        log.info(f"🔌  WebSocket disconnected │ audio chunks={chunks_received}")
    except Exception as e:
        log.error(f"❌  WebSocket error: {e}")
    finally:
        # ── Cleanup ─────────────────────────────────────────────────────
        if llm_task and not llm_task.done():
            llm_task.cancel()
            try:
                await llm_task
            except (asyncio.CancelledError, Exception):
                pass
        await stt.close()
        log.info("🧹  Session cleanup complete")
