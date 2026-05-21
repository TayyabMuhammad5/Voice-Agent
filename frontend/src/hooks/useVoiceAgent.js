"use client";

import { useRef, useState, useCallback, useEffect } from "react";


const WS_URL = typeof window !== "undefined" 
  ? `ws://${window.location.hostname}:8000/ws/audio`
  : "ws://127.0.0.1:8000/ws/audio";
const SAMPLE_RATE = 48000;
const BUFFER_SIZE = 4096; // ~85ms chunks at 48kHz

// ─── AudioQueue for TTS Playback ──────────────────────────────────────────
class AudioQueue {
  constructor(audioCtx) {
    this.audioCtx = audioCtx;
    this.nextStartTime = 0;
    this.sources = [];
    this._queue = [];       // pending PCM buffers
    this._processing = false; // serialization lock
  }

  enqueue(pcmArrayBuffer) {
    if (!pcmArrayBuffer || pcmArrayBuffer.byteLength === 0) return;
    this._queue.push(pcmArrayBuffer);
    this._processNext();
  }

  async _processNext() {
    // Only one decode loop at a time
    if (this._processing) return;
    this._processing = true;

    while (this._queue.length > 0) {
      const buffer = this._queue.shift();
      await this._playPCM(buffer);
    }

    this._processing = false;
  }

  async _playPCM(pcmArrayBuffer) {
    if (this.audioCtx.state === "suspended") return;

    try {
      // Decode raw 16-bit PCM (24000Hz mono)
      const int16Array = new Int16Array(pcmArrayBuffer);
      const float32Array = new Float32Array(int16Array.length);
      for (let i = 0; i < int16Array.length; i++) {
        float32Array[i] = int16Array[i] / 32768.0;
      }

      const audioBuffer = this.audioCtx.createBuffer(1, float32Array.length, 24000);
      audioBuffer.copyToChannel(float32Array, 0);

      const source = this.audioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(this.audioCtx.destination);

      source.onended = () => {
        this.sources = this.sources.filter((s) => s !== source);
      };

      const currentTime = this.audioCtx.currentTime;
      if (this.nextStartTime < currentTime) {
        this.nextStartTime = currentTime + 0.1;
      }

      source.start(this.nextStartTime);
      this.nextStartTime += audioBuffer.duration;
      this.sources.push(source);
    } catch (e) {
      console.warn("Audio decode/playback error:", e);
    }
  }

  stopAll() {
    this._queue = [];  // clear pending
    this.sources.forEach((source) => {
      try { source.stop(); } catch (e) {}
      source.disconnect();
    });
    this.sources = [];
    this.nextStartTime = 0;
  }
}

export function useVoiceAgent() {
  const [status, setStatus] = useState("idle"); // idle | connecting | active | error
  const [error, setError] = useState(null);
  const [stats, setStats] = useState({ chunksSent: 0 });
  const [messages, setMessages] = useState([]);
  const [interimTranscript, setInterimTranscript] = useState("");

  // Refs survive re-renders
  const wsRef = useRef(null);
  const streamRef = useRef(null);
  const audioCtxRef = useRef(null);
  const workletNodeRef = useRef(null);
  const statsRef = useRef({ chunksSent: 0 });
  const currentResponseRef = useRef(""); // What is actually displayed
  const pendingResponseRef = useRef(""); // What the server has sent so far
  const isFinalizingRef = useRef(false); // Whether we are waiting to finalize the message
  const audioQueueRef = useRef(null);

  // ─── Typewriter Effect ──────────────────────────────────────────────
  useEffect(() => {
    // 40ms per character is roughly 25 chars/sec, similar to voice speed
    const interval = setInterval(() => {
      if (currentResponseRef.current.length < pendingResponseRef.current.length) {
        const nextChar = pendingResponseRef.current[currentResponseRef.current.length];
        currentResponseRef.current += nextChar;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === "streaming"
              ? { ...m, text: currentResponseRef.current }
              : m
          )
        );
      } else if (isFinalizingRef.current) {
        // Done typing and server sent response_end, so finalize it!
        setMessages((prev) =>
          prev.map((m) =>
            m.id === "streaming"
              ? { ...m, id: `ai-${Date.now()}` }
              : m
          )
        );
        isFinalizingRef.current = false;
        currentResponseRef.current = "";
        pendingResponseRef.current = "";
      }
    }, 60);

    return () => clearInterval(interval);
  }, []);

  // ─── Handle Server Messages ──────────────────────────────────────────
  const handleServerMessage = useCallback((msg) => {
    switch (msg.type) {
      case "transcript":
        if (msg.is_final) {
          // Add finalized user message to conversation
          setMessages((prev) => [
            ...prev,
            {
              id: `user-${Date.now()}`,
              role: "user",
              text: msg.text,
              interrupted: false,
            },
          ]);
          setInterimTranscript("");
        } else {
          // Show interim transcript (will be replaced on next interim or final)
          setInterimTranscript(msg.text);
        }
        break;

      case "response_start":
        currentResponseRef.current = "";
        pendingResponseRef.current = "";
        isFinalizingRef.current = false;
        // Add streaming placeholder for AI response
        setMessages((prev) => [
          ...prev,
          {
            id: "streaming",
            role: "assistant",
            text: "",
            interrupted: false,
          },
        ]);
        break;

      case "response_token":
        // We ignore raw tokens since we pace it by chunks now, but if it comes, just add to pending
        pendingResponseRef.current += msg.text;
        break;

      case "response_chunk":
        // Backend now sends chunks right before audio plays.
        // Add to pending so the typewriter effect types it out.
        pendingResponseRef.current += msg.text;
        break;

      case "response_end":
        // Server says it's done sending. Let the typewriter finish the remaining text.
        pendingResponseRef.current = msg.full_text;
        isFinalizingRef.current = true;
        break;

      case "response_interrupted":
        // Full barge-in confirmed. Stop all audio playback.
        if (audioQueueRef.current) {
          audioQueueRef.current.stopAll();
        }
        // Mark current streaming message as interrupted immediately
        setMessages((prev) =>
          prev.map((m) =>
            m.id === "streaming"
              ? {
                  ...m,
                  id: `ai-${Date.now()}-int`,
                  text: currentResponseRef.current,
                  interrupted: true,
                }
              : m
          )
        );
        isFinalizingRef.current = false;
        currentResponseRef.current = "";
        pendingResponseRef.current = "";
        break;

      case "error":
        setError(msg.message);
        break;
    }
  }, []);

  // ─── Start Session ────────────────────────────────────────────────────
  const start = useCallback(async () => {
    try {
      setError(null);
      setStatus("connecting");
      statsRef.current = { chunksSent: 0 };
      setStats({ chunksSent: 0 });
      setMessages([]);
      setInterimTranscript("");

      // 1. Get microphone access
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: SAMPLE_RATE,
        },
      });
      streamRef.current = stream;

      // 2. Create AudioContext and load worklet
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: SAMPLE_RATE,
      });
      audioCtxRef.current = audioCtx;
      audioQueueRef.current = new AudioQueue(audioCtx);

      if (audioCtx.state === "suspended") {
        await audioCtx.resume();
      }

      await audioCtx.audioWorklet.addModule("/worklets/pcm-capture-processor.js");

      // 3. Connect mic → AudioWorklet
      const micSource = audioCtx.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(
        audioCtx,
        "pcm-capture-processor",
        { processorOptions: { bufferSize: BUFFER_SIZE } }
      );
      workletNodeRef.current = workletNode;
      micSource.connect(workletNode);

      // 4. Open WebSocket
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.binaryType = "arraybuffer";

      await new Promise((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error("WebSocket connection failed"));
        setTimeout(() => reject(new Error("WebSocket connection timeout")), 5000);
      });

      setStatus("active");

      // 5. AudioWorklet → WebSocket: send raw PCM as binary
      workletNode.port.onmessage = (e) => {
        if (e.data.type === "pcm" && ws.readyState === WebSocket.OPEN) {
          const samples = e.data.samples;
          // Prepend 8-byte timestamp for latency tracking
          const payload = new ArrayBuffer(8 + samples.byteLength);
          const view = new DataView(payload);
          view.setFloat64(0, performance.now(), true);
          new Int16Array(payload, 8).set(samples);

          ws.send(payload);
          statsRef.current.chunksSent++;
          setStats({ chunksSent: statsRef.current.chunksSent });
        }
      };

      // 6. Handle server messages (JSON text)
      ws.onmessage = (e) => {
        if (typeof e.data === "string") {
          try {
            const msg = JSON.parse(e.data);
            handleServerMessage(msg);
          } catch (err) {
            console.warn("Failed to parse server message:", err);
          }
        } else if (e.data instanceof ArrayBuffer) {
          // Binary message: TTS audio (PCM) from backend
          if (audioQueueRef.current) {
            audioQueueRef.current.enqueue(e.data);
          }
        }
      };

      ws.onerror = () => {
        setError("WebSocket connection error");
        setStatus("error");
      };

      ws.onclose = () => {
        setStatus((prev) => (prev === "error" ? prev : "idle"));
      };
    } catch (err) {
      console.error("Start error:", err);
      setError(err.message || "Failed to start session");
      setStatus("error");
      cleanup();
    }
  }, [handleServerMessage]);

  // ─── Cleanup ──────────────────────────────────────────────────────────
  const cleanup = useCallback(() => {
    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    cleanup();
    setStatus("idle");
  }, [cleanup]);

  // Cleanup on unmount
  useEffect(() => {
    return () => cleanup();
  }, [cleanup]);

  return { status, error, stats, messages, interimTranscript, start, stop };
}
