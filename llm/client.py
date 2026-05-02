"""Client for interacting with the Ollama API."""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class OllamaClient:
    """Client for interacting with the Ollama API."""

    base_url: str
    model: str

    async def chat(
        self, system: str, user: str, temperature: float = 0.7, think: bool = False
    ) -> str:
        """Send a chat request and return the assistant's response text."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
            "think": think,
            "options": {"temperature": temperature},
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=180.0,
            )
            response.raise_for_status()
        data = response.json()
        return data["message"]["content"]
