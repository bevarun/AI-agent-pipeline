"""Thin Gemini (Google AI Studio free tier) client: JSON mode, rate limiting, retry with backoff."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

import requests

log = logging.getLogger("llm")


class LLMError(Exception):
    pass


def _clean_model(name: str) -> str:
    clean = re.sub(r"[\s_]+", "-", (name or "").strip()).lower()
    if clean.startswith("models/"):
        clean = clean[len("models/"):]
    if "3.1" in clean:
        clean = clean.replace("3.1", "3.5")
    return clean or "gemini-2.5-flash"


class GeminiClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 min_interval: Optional[float] = None):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        raw_model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.model = _clean_model(raw_model)
        self.min_interval = float(min_interval if min_interval is not None
                                  else os.getenv("LLM_MIN_INTERVAL_SECONDS", "15.0"))
        self._last_call = 0.0
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            log.info("Rate limiter: waiting %.1fs to enforce <= %.1f calls/min (%.1fs spacing)",
                     wait, 60.0 / self.min_interval, self.min_interval)
            time.sleep(wait)
        self._last_call = time.monotonic()

    def generate(self, system: str, prompt: str, json_mode: bool = True,
                 temperature: float = 0.4, retries: int = 4) -> str:
        if not self.available:
            raise LLMError("GEMINI_API_KEY not set")
        fallback_models = ["gemini-2.5-flash", "gemini-2.0-flash"]
        raw_candidates = [self.model] + fallback_models
        # preserve order and deduplicate
        candidates = []
        for m in raw_candidates:
            cm = _clean_model(m)
            if cm and cm not in candidates:
                candidates.append(cm)

        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": 700,
                                 **({"responseMimeType": "application/json"} if json_mode else {})},
        }
        delay = 4.0
        prompt_snippet = (prompt[:120] + "...") if len(prompt) > 120 else prompt
        system_snippet = (system[:80] + "...") if len(system) > 80 else system
        call_num = self.calls + 1

        for attempt in range(1, retries + 1):
            model_to_use = candidates[min(attempt - 1, len(candidates) - 1)]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_use}:generateContent"
            self._throttle()
            t0 = time.monotonic()
            log.info("LLM [Call #%d Attempt %d/%d] -> %s | system: %s | prompt: %s",
                     call_num, attempt, retries, model_to_use, repr(system_snippet), repr(prompt_snippet))
            try:
                r = requests.post(url, json=body, timeout=25,
                                  headers={"x-goog-api-key": self.api_key})
                latency_ms = int((time.monotonic() - t0) * 1000)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:180]}")
                r.raise_for_status()
                data = r.json()
                self.calls += 1
                usage = data.get("usageMetadata", {})
                t_in = usage.get("promptTokenCount", 0)
                t_out = usage.get("candidatesTokenCount", 0)
                self.tokens_in += t_in
                self.tokens_out += t_out
                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                response_text = "".join(p.get("text", "") for p in parts)
                log.info("LLM [Call #%d Success] <- %s (%dms, %d in / %d out tokens, total calls: %d)",
                         self.calls, model_to_use, latency_ms, t_in, t_out, self.calls)
                if model_to_use != self.model:
                    log.info("LLM succeeded using fallback model %s", model_to_use)
                return response_text
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
                next_model = candidates[min(attempt, len(candidates) - 1)]
                if attempt == retries:
                    log.error("LLM [Call #%d FAILED] after %d attempts: %s", call_num, retries, e)
                    raise LLMError(f"Gemini failed after {retries} attempts: {e}") from e
                msg = f"; switching to {next_model}" if next_model != model_to_use else ""
                log.warning("LLM [Call #%d Attempt %d/%d] on %s failed: %s%s; backing off %.0fs",
                            call_num, attempt, retries, model_to_use, e, msg, delay)
                time.sleep(delay)
                delay *= 1.5
            except (KeyError, IndexError, ValueError) as e:  # blocked / empty / non-JSON response
                log.error("LLM [Call #%d Format Error] Unexpected Gemini response: %s", call_num, e)
                raise LLMError(f"Unexpected Gemini response: {e}") from e
        raise LLMError("unreachable")

    def generate_json(self, system: str, prompt: str, **kw) -> dict:
        text = self.generate(system, prompt, json_mode=True, **kw)
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"Model returned invalid JSON: {e}: {text[:150]}") from e
        if not isinstance(obj, dict):
            raise LLMError("Model JSON is not an object")
        return obj
