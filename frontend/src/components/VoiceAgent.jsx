"use client";

import { useRef, useEffect } from "react";
import { useVoiceAgent } from "../hooks/useVoiceAgent";


export default function VoiceAgent() {
  const { status, error, stats, messages, interimTranscript, start, stop } =
    useVoiceAgent();

  const isActive = status === "active";
  const isConnecting = status === "connecting";

  // Auto-scroll conversation to bottom
  const conversationEndRef = useRef(null);
  useEffect(() => {
    conversationEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, interimTranscript]);

  return (
    <div className="agent-container">
      {/* ── Header ─────────────────────────────────── */}
      <div className="agent-header">
        <div className="agent-logo">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" x2="12" y1="19" y2="22" />
          </svg>
        </div>
        <div className={`status-badge ${status}`}>
          <span className="status-dot"></span>
          {status === "idle" && "Offline"}
          {status === "connecting" && "Connecting..."}
          {status === "active" && "Live"}
          {status === "error" && "Error"}
        </div>
      </div>

      {/* ── Orb Visualizer (compact in Phase 2) ────── */}
      <div className="visualizer-area compact">
        <div className={`orb orb-sm ${isActive ? "orb-active" : ""} ${isConnecting ? "orb-connecting" : ""}`}>
          <div className="orb-ring orb-ring-1"></div>
          <div className="orb-ring orb-ring-2"></div>
          <div className="orb-core orb-core-sm">
            {isActive ? (
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" x2="12" y1="19" y2="22" />
              </svg>
            ) : (
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" x2="12" y1="19" y2="22" />
              </svg>
            )}
          </div>
        </div>
        <p className="visualizer-label">
          {status === "idle" && "Tap Start to begin"}
          {status === "connecting" && "Establishing connection..."}
          {status === "active" && "Listening — speak now"}
          {status === "error" && "Connection failed"}
        </p>
      </div>

      {/* ── Conversation ───────────────────────────── */}
      <div className="conversation-area">
        <div className="conversation-scroll">
          {messages.length === 0 && isActive && (
            <div className="conversation-empty">
              <p>Start speaking — your conversation will appear here.</p>
            </div>
          )}

          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`message ${msg.role} ${msg.interrupted ? "interrupted" : ""} ${msg.id === "streaming" ? "streaming" : ""}`}
            >
              <div className="message-label">
                {msg.role === "user" ? "You" : "AI"}
                {msg.interrupted && <span className="interrupted-badge">interrupted</span>}
              </div>
              <div className="message-text">
                {msg.text || (msg.id === "streaming" ? "" : "...")}
                {msg.id === "streaming" && <span className="typing-cursor" />}
              </div>
            </div>
          ))}

          {/* Interim transcript (user is currently speaking) */}
          {interimTranscript && (
            <div className="message user interim">
              <div className="message-label">You</div>
              <div className="message-text">{interimTranscript}</div>
            </div>
          )}

          <div ref={conversationEndRef} />
        </div>
      </div>

      {/* ── Controls ───────────────────────────────── */}
      <div className="controls">
        <button
          id="voice-toggle-btn"
          className={`btn-primary ${isActive ? "btn-stop" : "btn-start"}`}
          onClick={isActive ? stop : start}
          disabled={isConnecting}
        >
          {isConnecting ? "Connecting..." : isActive ? "Stop Session" : "Start Session"}
        </button>
      </div>

      {/* ── Error Display ──────────────────────────── */}
      {error && (
        <div className="error-banner">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <line x1="15" y1="9" x2="9" y2="15" />
            <line x1="9" y1="9" x2="15" y2="15" />
          </svg>
          {error}
        </div>
      )}
    </div>
  );
}
