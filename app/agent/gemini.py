import os
from time import sleep

import requests
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


TEMPORARY_SERVER_ERRORS = (502, 503, 504)


class GeminiUnavailable(requests.HTTPError):
    """Gemini returned a temporary server error after retrying."""


class GeminiClient:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Set GEMINI_API_KEY in the project .env file.")
        self.model = os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite"

    def generate[Output: BaseModel](
        self,
        *,
        instructions: str,
        prompt: str,
        temperature: float,
        output_type: type[Output],
    ) -> Output:
        for attempt in range(3):
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key},
                json={
                    "systemInstruction": {"parts": [{"text": instructions}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": temperature,
                        "responseMimeType": "application/json",
                        "responseJsonSchema": output_type.model_json_schema(),
                    },
                },
                timeout=(10, 90),
            )
            if response.status_code not in TEMPORARY_SERVER_ERRORS or attempt == 2:
                break
            sleep(2 ** attempt)
        if response.status_code in TEMPORARY_SERVER_ERRORS:
            raise GeminiUnavailable(
                f"Gemini returned HTTP {response.status_code} after three attempts.",
                response=response,
            )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
            raise ValueError(f"Gemini returned no candidate (reason: {reason}).")

        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        if not text:
            reason = candidate.get("finishReason", "unknown")
            raise ValueError(f"Gemini returned no text (reason: {reason}).")
        return output_type.model_validate_json(text)
