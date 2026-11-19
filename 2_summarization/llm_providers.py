"""Provider-agnostic LLM access: every provider turns (system prompt, user prompt, JSON schema) into a dict
that matches the schema. The summarizer only calls generate_json(), so it never knows which provider runs.

Today there is one provider, Anthropic (Claude). To add another (OpenAI, Gemini, a local model ...): write a
class with the same three attributes and generate_json(), add it to PROVIDERS, and set "provider" in
settings.LLM (or LIVEMINUTES_LLM_PROVIDER in .env). Nothing else changes.
"""
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LLMError(RuntimeError):
    """The provider answered, but not with usable output (refused, cut off, or not valid JSON)."""


# Read KEY=value lines from the repo's .env into the environment (a variable already set wins).
# Same file the meeting bot uses, so all secrets stay in one gitignored place.
def load_env(path=os.path.join(REPO, ".env")):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if value.strip():
                    os.environ.setdefault(key.strip(), value.strip())


class AnthropicProvider:
    """Claude through the official Anthropic SDK, with structured outputs (the reply is forced to match the schema)."""
    name = "anthropic"

    def __init__(self, model, effort=None, fallbacks=None):
        import anthropic   # imported here so the other models never need the package
        load_env()
        self.anthropic = anthropic
        self.model, self.effort, self.fallbacks = model, effort, fallbacks
        self.client = anthropic.Anthropic()   # key from ANTHROPIC_API_KEY (or an `ant auth login` profile)
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "served_by": []}

    def generate_json(self, system, user, schema, max_tokens):
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        request = dict(model=self.model, max_tokens=max_tokens, system=system,
                       messages=[{"role": "user", "content": user}], output_config=output_config)
        try:
            if self.fallbacks:
                # If Claude's safety filter declines, the API re-runs the request on a fallback model by itself.
                response = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks=self.fallbacks, **request)
            else:
                response = self.client.messages.create(**request)
        except self.anthropic.AuthenticationError:
            raise SystemExit("Anthropic rejected the API key: set ANTHROPIC_API_KEY in the repo's .env "
                             "(https://platform.claude.com/settings/keys)")
        except TypeError as e:   # what the SDK raises when it finds no key at all
            if "authentication" not in str(e):
                raise
            raise SystemExit("No Anthropic API key found: add ANTHROPIC_API_KEY=... to the repo's .env "
                             "(https://platform.claude.com/settings/keys)")

        self.usage["calls"] += 1
        self.usage["input_tokens"] += response.usage.input_tokens
        self.usage["output_tokens"] += response.usage.output_tokens
        if response.model not in self.usage["served_by"]:
            self.usage["served_by"].append(response.model)
        if response.stop_reason == "refusal":
            raise LLMError(f"the model declined this request ({getattr(response.stop_details, 'category', None)})")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"reply cut off at max_tokens={max_tokens}: raise LLM['max_tokens'] in settings.py")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"reply was not valid JSON: {e}") from e


class GeminiProvider:
    """Google Gemini through its REST API (standard library only, no SDK), with a JSON-schema response format."""
    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    RETRY = {429, 500, 502, 503, 504}   # rate limit / server busy: wait and try again
    TRIES = 6                           # waits 10, 20, ... 60 s between tries: rides out ~2.5 min of "overloaded"

    def __init__(self, model, effort=None, fallbacks=None, timeout=180):
        load_env()
        self.model, self.timeout = model, timeout
        self.key = os.environ.get("GEMINI_API_KEY")
        if not self.key:
            raise SystemExit("No Gemini API key found: add GEMINI_API_KEY=... to the repo's .env "
                             "(https://aistudio.google.com/apikey)")
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "served_by": []}

    def post(self, body):
        import time
        import urllib.error
        import urllib.request
        request = urllib.request.Request(self.URL.format(model=self.model), data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        for attempt in range(1, self.TRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                if e.code in (401, 403):
                    raise SystemExit(f"Gemini rejected the API key (HTTP {e.code}): check GEMINI_API_KEY in .env")
                if e.code not in self.RETRY or attempt == self.TRIES:
                    raise LLMError(f"Gemini HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == self.TRIES:
                    raise LLMError(f"Gemini request failed {self.TRIES} times: {e}") from e
            time.sleep(min(10 * attempt, 60))

    def generate_json(self, system, user, schema, max_tokens):
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema,
                                     "maxOutputTokens": max_tokens}}
        response = self.post(body)
        self.usage["calls"] += 1
        meta = response.get("usageMetadata", {})
        self.usage["input_tokens"] += meta.get("promptTokenCount", 0)
        self.usage["output_tokens"] += meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0)
        served = response.get("modelVersion", self.model)
        if served not in self.usage["served_by"]:
            self.usage["served_by"].append(served)
        candidates = response.get("candidates") or []
        if not candidates:
            raise LLMError(f"Gemini returned no answer: {response.get('promptFeedback')}")
        finish = candidates[0].get("finishReason")
        if finish == "MAX_TOKENS":
            raise LLMError(f"reply cut off at max_tokens={max_tokens}: raise LLM['max_tokens'] in settings.py")
        if finish not in (None, "STOP"):
            raise LLMError(f"Gemini stopped early: {finish}")
        text = "".join(p.get("text", "") for p in candidates[0]["content"]["parts"] if not p.get("thought"))
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"reply was not valid JSON: {e}") from e


PROVIDERS = {"anthropic": AnthropicProvider, "gemini": GeminiProvider}


# Build the provider named in the settings (settings.LLM: provider, model, effort, fallbacks).
def make_provider(cfg):
    if cfg["provider"] not in PROVIDERS:
        raise SystemExit(f"unknown LLM provider {cfg['provider']!r}; known: {sorted(PROVIDERS)}")
    return PROVIDERS[cfg["provider"]](cfg["model"], effort=cfg.get("effort"), fallbacks=cfg.get("fallbacks"))
