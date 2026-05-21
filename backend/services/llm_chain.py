"""
Groq / LangChain LLM Service

Wraps ChatGroq for streaming LLM responses with conversation history.
Enforces concise, voice-appropriate responses (3-4 sentences, 50-100 words).
"""

import logging
from typing import AsyncIterator

from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

log = logging.getLogger("voice-agent")

SYSTEM_PROMPT = """\
You are a helpful voice assistant having a real-time spoken conversation.

Rules:
- Keep every response to 3–4 sentences (50–100 words max).
- Be natural, direct, and conversational — like talking to a friend.
- Never use markdown, bullet points, numbered lists, or any formatting.
- Never use emojis or special characters.
- Don't start with filler like "Sure!", "Of course!", or "Great question!".
- If you don't know something, say so briefly.
- Give clear, useful answers — not vague pleasantries."""


class LLMChain:
    """Streaming LLM with conversation history management."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self.llm = ChatGroq(
            model=model,
            temperature=0.7,
            max_tokens=250,  # Hard cap — prevents runaway responses
            api_key=api_key,
        )
        self.history: list = [SystemMessage(content=SYSTEM_PROMPT)]

    async def stream(self, user_text: str) -> AsyncIterator[str]:
        """Stream LLM response tokens for the given user input.

        Appends the user message to history before streaming.
        Call add_response() after streaming completes to save the AI response.
        """
        self.history.append(HumanMessage(content=user_text))
        log.info(f"🤖  Streaming LLM for: \"{user_text}\"")

        async for chunk in self.llm.astream(self.history):
            if chunk.content:
                yield chunk.content

    def add_response(self, response_text: str):
        """Save the AI's complete response to conversation history."""
        self.history.append(AIMessage(content=response_text))

    def trim_history(self, max_turns: int = 10):
        """Keep only the last N turns to prevent context overflow.

        Always preserves the system message at index 0.
        """
        # System message + max_turns * 2 (human + ai per turn)
        max_messages = 1 + (max_turns * 2)
        if len(self.history) > max_messages:
            self.history = [self.history[0]] + self.history[-max_turns * 2 :]
            log.info(f"✂️  Trimmed history to {len(self.history)} messages")

    def reset(self):
        """Clear conversation history (keeps system prompt)."""
        self.history = [SystemMessage(content=SYSTEM_PROMPT)]
