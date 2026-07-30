import os
import base64
import json
import time
import html
import threading
from collections import defaultdict, deque
from flask import Flask, request, jsonify, send_from_directory, Blueprint
from werkzeug.exceptions import HTTPException
from flask_cors import CORS
from google import genai
from google.genai import types
from anthropic import Anthropic
from openai import OpenAI


# ─────────────────────────────────────────────────────────────────────────────
# FLASK APP INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__, static_url_path='', static_folder='static')
CORS(app)  # Enable CORS for local development

# Hard ceiling on request body size. Werkzeug enforces this before the JSON
# is ever parsed, so a multi-megabyte POST is rejected without the server
# spending memory or CPU on it. This is the outermost line of defense —
# every per-field limit below sits inside it.
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1 MB

# ─────────────────────────────────────────────
# MODEL SELECTION
# ─────────────────────────────────────────────

# Narrator Model
# "claude-sonnet-5" | "gemini-3.1-pro-preview" | "gpt-5.5"
NARRATOR_MODEL = os.environ.get("NARRATOR_MODEL", "gpt-5.5") # # gemini-3-flash-preview # gemini-3.1-pro-preview-customtools

# Image Generation Model
# Options: "imagen-4.0-fast-generate-001" | "gpt-image-2" | "gemini-3.1-flash-image" (Nano Banana)
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "gpt-image-2")

# ─────────────────────────────────────────────
# API CLIENTS
# ─────────────────────────────────────────────

gemini_key = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=gemini_key) if gemini_key else None

openai_key = os.environ.get("OPENAI_API_KEY")
openai_client = OpenAI(api_key=openai_key.strip()) if openai_key else None

anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
anthropic_client = Anthropic(api_key=anthropic_key) if anthropic_key else None

# ─────────────────────────────────────────────
# PROVIDER DETECTION
# ─────────────────────────────────────────────

def get_provider(model_name: str) -> str:
    """Determine the provider based on model name."""
    name = model_name.lower()
    if "claude" in name:
        return "claude"
    elif "gemini" in name or "imagen" in name:
        return "gemini"
    elif "gpt" in name:
        return "gpt"
    return None

NARRATOR_PROVIDER = get_provider(NARRATOR_MODEL)
IMAGE_PROVIDER = get_provider(IMAGE_MODEL)

# ─────────────────────────────────────────────────────────────────────────────
# INPUT LIMITS
# ─────────────────────────────────────────────────────────────────────────────
# Every one of these is tunable via env var so you can loosen or tighten
# without a redeploy. The numbers below are starting points — watch your
# logs for legitimate players hitting them and adjust.

def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

# The player's typed action. This is the one the UI should mirror.
MAX_ACTION_CHARS     = _int_env("MAX_ACTION_CHARS", 750)

# Everything else the client POSTs. These are the fields that actually
# cost you money if left unbounded.
MAX_SYSTEM_CHARS     = _int_env("MAX_SYSTEM_CHARS", 40000)
MAX_CONTEXT_CHARS    = _int_env("MAX_CONTEXT_CHARS", 20000)
MAX_HISTORY_MESSAGES = _int_env("MAX_HISTORY_MESSAGES", 40)
MAX_HISTORY_CHARS    = _int_env("MAX_HISTORY_CHARS", 60000)
MAX_SUMMARIES        = _int_env("MAX_SUMMARIES", 20)
MAX_SUMMARY_CHARS    = _int_env("MAX_SUMMARY_CHARS", 8000)
MAX_ARCHIVE_CHARS    = _int_env("MAX_ARCHIVE_CHARS", 60000)
MAX_PAINTER_CHARS    = _int_env("MAX_PAINTER_CHARS", 8000)
MAX_STORED_CONTEXT   = _int_env("MAX_STORED_CONTEXT", 200000)

# Rate limiting: requests per window, per client.
RATE_LIMIT_REQUESTS  = _int_env("RATE_LIMIT_REQUESTS", 20)
RATE_LIMIT_WINDOW    = _int_env("RATE_LIMIT_WINDOW", 60)

# Only trust X-Forwarded-For when you are actually behind a proxy you
# control. Otherwise a client can forge the header and get a fresh rate
# limit bucket on every request.
TRUST_PROXY = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")


class InputError(Exception):
    """A client-side validation failure. Always maps to HTTP 400."""


def _require_str(value, field, max_chars, required=False):
    """Type-check and length-check a string field from the request body."""
    if value is None or value == "":
        if required:
            raise InputError(f"{field} is required.")
        return ""
    if not isinstance(value, str):
        raise InputError(f"{field} must be text.")
    if len(value) > max_chars:
        raise InputError(f"{field} too long. {max_chars:,} characters maximum.")
    return value


def _validate_history(history):
    """
    Bound the conversation history and normalize roles.

    Restricting roles to user/assistant matters as much as the length cap:
    without it a client can post a 'system' turn and rewrite the narrator's
    instructions from the browser.
    """
    if history is None:
        return []
    if not isinstance(history, list):
        raise InputError("history must be a list.")
    if len(history) > MAX_HISTORY_MESSAGES:
        raise InputError(f"history too long. {MAX_HISTORY_MESSAGES} messages maximum.")

    clean, total = [], 0
    for msg in history:
        if not isinstance(msg, dict):
            raise InputError("history entries must be objects.")
        role = msg.get("role")
        content = msg.get("content")
        if role == "model":          # Gemini-style name for the same thing
            role = "assistant"
        if role not in ("user", "assistant"):
            raise InputError("history roles must be 'user' or 'assistant'.")
        if not isinstance(content, str):
            raise InputError("history content must be text.")
        total += len(content)
        if total > MAX_HISTORY_CHARS:
            raise InputError(f"history too large. {MAX_HISTORY_CHARS:,} characters maximum.")
        clean.append({"role": role, "content": content})
    return clean


def _validate_summaries(summaries):
    if summaries is None:
        return []
    if not isinstance(summaries, list):
        raise InputError("summaries must be a list.")
    if len(summaries) > MAX_SUMMARIES:
        raise InputError(f"Too many summaries. {MAX_SUMMARIES} maximum.")
    clean = []
    for s in summaries:
        if not isinstance(s, str):
            raise InputError("summaries must be text.")
        if len(s) > MAX_SUMMARY_CHARS:
            raise InputError(f"Summary too long. {MAX_SUMMARY_CHARS:,} characters maximum.")
        clean.append(s)
    return clean


def _json_body():
    """Parse the JSON body without raising on a bad/missing content type."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise InputError("Request body must be a JSON object.")
    return body


# ─────────────────────────────────────────────
# RATE LIMITING (in-process, no dependencies)
# ─────────────────────────────────────────────
# A sliding window per client. Good enough for a single Flask process.
# If you scale to multiple workers, swap this for flask-limiter backed
# by Redis — separate processes do not share these buckets.

_rate_lock = threading.Lock()
_rate_buckets = defaultdict(deque)
_rate_last_prune = 0.0


def _client_key():
    if TRUST_PROXY:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _check_rate_limit(limit=None, window=None):
    """Return None if allowed, or the number of seconds to wait if throttled."""
    global _rate_last_prune
    limit = limit or RATE_LIMIT_REQUESTS
    window = window or RATE_LIMIT_WINDOW
    key = _client_key()
    now = time.monotonic()

    with _rate_lock:
        # Drop buckets nobody has touched in a while, so the dict itself
        # cannot be grown without bound by rotating source addresses.
        if now - _rate_last_prune > window:
            for k in [k for k, b in _rate_buckets.items() if not b or now - b[-1] > window]:
                del _rate_buckets[k]
            _rate_last_prune = now

        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return int(window - (now - bucket[0])) + 1
        bucket.append(now)
    return None


def _rate_limited_response(retry_after):
    resp = jsonify({"error": f"Too many requests. Try again in {retry_after}s."})
    resp.status_code = 429
    resp.headers["Retry-After"] = str(retry_after)
    return resp


# ─────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────

@app.errorhandler(413)
def _payload_too_large(e):
    return jsonify({"error": "Request body too large."}), 413

# ─────────────────────────────────────────────
# HARD-CODED FAST MODELS (ARCHIVIST + IMAGE REFINER)
# ─────────────────────────────────────────────

GEMINI_ARCHIVIST_AND_REFINER = 'gemini-3.1-flash-lite-preview'
GPT_ARCHIVIST_AND_REFINER = "gpt-5.4-mini"
CLAUDE_ARCHIVIST_AND_REFINER = "claude-haiku-4-5"

# Helper to get archivist model based on narrator provider
def get_archivist_model(narrator_provider):
    """Return the appropriate archivist model based on narrator provider."""
    if narrator_provider == "gpt":
        return GPT_ARCHIVIST_AND_REFINER
    elif narrator_provider == "claude":
        return CLAUDE_ARCHIVIST_AND_REFINER
    else:  # Default to Gemini
        return GEMINI_ARCHIVIST_AND_REFINER

# ─────────────────────────────────────────────────────────────────────────────
# AI HANDLERS - NARRATION
# ─────────────────────────────────────────────────────────────────────────────

def handle_sonnet(system_prompt, context, player_action, history=[], summaries=[]):
    if not anthropic_client:
        raise ValueError("ANTHROPIC_API_KEY not configured for Claude")

    # Build system: rules + summaries + current state
    summary_block = ""
    if summaries:
        summary_block = "\n\n## ◈ CHRONICLE ARCHIVE (oldest to most recent)\n" + \
            "\n---\n".join(f"[Archive {i+1}]\n{s}" for i, s in enumerate(summaries))

    system_parts = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": summary_block + "\n\n" + context if summary_block else context}
    ]

    print(context)

    # Build messages: history + current action
    messages = list(history) + [{"role": "user", "content": player_action}]

    kwargs = {
        "model": NARRATOR_MODEL,
        "max_tokens": 8192,
        "temperature": 0.7,
        "system": system_parts,
        "messages": messages,
    }

    try:
        response = anthropic_client.messages.create(**kwargs)
    except Exception as e:
        # Claude 4.7+ and Mythos-tier models reject temperature outright
        # rather than clamping it. Retry once without it — cache_control
        # on system_parts is untouched either way.
        if "temperature" in str(e).lower():
            kwargs.pop("temperature", None)
            response = anthropic_client.messages.create(**kwargs)
        else:
            raise RuntimeError(f"Claude narration call failed ({NARRATOR_MODEL}): {e}") from e

    return response.content[0].text

# ─────────────────────────────────────────────
# GEMINI CACHE
# ─────────────────────────────────────────────

_gemini_cache = None
_gemini_cache_expiry = 0
_gemini_cache_prompt = None

def get_or_create_gemini_cache(system_prompt):
    global _gemini_cache, _gemini_cache_expiry, _gemini_cache_prompt
    now = time.time()
    if _gemini_cache and now < _gemini_cache_expiry and _gemini_cache_prompt == system_prompt:
        return _gemini_cache
    # Create new cache
    _gemini_cache = gemini_client.caches.create(
        model=NARRATOR_MODEL,
        config=types.CreateCachedContentConfig(
            display_name="unarmored_logic",
            system_instruction=system_prompt,
            ttl="3600s",
        )
    )
    _gemini_cache_expiry = now + 3500  # refresh 100s before expiry
    _gemini_cache_prompt = system_prompt
    print(f"✅ Gemini cache created: {_gemini_cache.name}")
    return _gemini_cache


def handle_gemini(system_prompt, context, player_action, history=[], summaries=[]):
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY not configured")

    cache = get_or_create_gemini_cache(system_prompt)

    summary_block = ""
    if summaries:
        summary_block = "\n\n## ◈ CHRONICLE ARCHIVE (oldest to most recent)\n" + \
            "\n---\n".join(f"[Archive {i+1}]\n{s}" for i, s in enumerate(summaries))

    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=msg["content"])]
        ))

    contents.append(types.Content(role="user", parts=[
        types.Part.from_text(text=f"### CURRENT_STATE ###\n{context}{summary_block}"),
        types.Part.from_text(text=f"### USER_INPUT ###\n{player_action}")
    ]))

    def _config(with_temp: bool):
        cfg = {"cached_content": cache.name, "max_output_tokens": 8000}
        if with_temp:
            cfg["temperature"] = 1.0
        return types.GenerateContentConfig(**cfg)

    try:
        response = gemini_client.models.generate_content(
            model=NARRATOR_MODEL, contents=contents, config=_config(True)
        )
    except Exception as e:
        # Latest-gen Gemini 3.x models deprecate temperature the same way
        # Claude 4.7+ and GPT-5 do. Retry once without it — the cache
        # reference is untouched either way.
        if "temperature" in str(e).lower() or "sampling" in str(e).lower():
            response = gemini_client.models.generate_content(
                model=NARRATOR_MODEL, contents=contents, config=_config(False)
            )
        else:
            raise RuntimeError(f"Gemini narration call failed ({NARRATOR_MODEL}): {e}") from e
    return response.text


def handle_gpt(system_prompt, context, player_action, history=[], summaries=[]):
    """GPT narration with history and archive summaries"""
    if not openai_client:
        raise ValueError("OpenAI API key not configured")

    summary_block = ""
    if summaries:
        summary_block = "\n\n## ◈ CHRONICLE ARCHIVE (oldest to most recent)\n" + \
            "\n---\n".join(f"[Archive {i+1}]\n{s}" for i, s in enumerate(summaries))

    gpt_messages = [{"role": "system", "content": system_prompt + (summary_block if summary_block else "")}]

    # Inject history
    gpt_messages.extend(history)

    # Current state + action
    gpt_messages.append({"role": "user", "content": f"### CURRENT_STATE ###\n{context}"})
    gpt_messages.append({"role": "user", "content": f"### USER_INPUT ###\n{player_action}"})

    kwargs = {
        "model": NARRATOR_MODEL,
        "messages": gpt_messages,
        "temperature": 1.0,
        "max_completion_tokens": 8000,
    }

    try:
        response = openai_client.chat.completions.create(**kwargs)
    except Exception as e:
        if "temperature" in str(e).lower():
            kwargs.pop("temperature", None)
            response = openai_client.chat.completions.create(**kwargs)
        else:
            raise RuntimeError(f"GPT narration call failed ({NARRATOR_MODEL}): {e}") from e

    return response.choices[0].message.content


# ─────────────────────────────────────────────────────────────────────────────
# AI HANDLERS - ARCHIVING (SUMMARIZATION)
# ─────────────────────────────────────────────────────────────────────────────

def handle_archive(log_segment, narrator_provider, archivist_prompt):
    """
    Archive/summarize conversation logs using fast models.
    """
    # Previously only the Gemini branch honored a custom archivist_prompt;
    # GPT and Claude silently discarded it. Fall back to the old default
    # only if the caller passes nothing.
    system_instruction = archivist_prompt or "Summarize the following conversation accurately."

    if narrator_provider == "gpt":
        model = GPT_ARCHIVIST_AND_REFINER
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Summarize:\n{log_segment}"}
            ],
            "max_completion_tokens": 8000,
            "temperature": 0.3,
        }
        try:
            response = openai_client.chat.completions.create(**kwargs)
        except Exception as e:
            if "temperature" in str(e).lower():
                kwargs.pop("temperature", None)
                response = openai_client.chat.completions.create(**kwargs)
            else:
                raise RuntimeError(f"Archive (GPT/{model}) failed: {e}") from e
        return response.choices[0].message.content

    elif narrator_provider == "claude":
        model = CLAUDE_ARCHIVIST_AND_REFINER
        kwargs = {
            "model": model,
            "system": system_instruction,
            "messages": [{"role": "user", "content": log_segment}],
            "max_tokens": 8000,
            "temperature": 0.3,
        }
        try:
            response = anthropic_client.messages.create(**kwargs)
        except Exception as e:
            if "temperature" in str(e).lower():
                kwargs.pop("temperature", None)
                response = anthropic_client.messages.create(**kwargs)
            else:
                raise RuntimeError(f"Archive (Claude/{model}) failed: {e}") from e
        return response.content[0].text

    else:  # Gemini default
        model = GEMINI_ARCHIVIST_AND_REFINER
        contents = f"Log Segment to Archive:\n{log_segment}"

        def _config(with_temp: bool):
            cfg = {"system_instruction": system_instruction, "max_output_tokens": 8000}
            if with_temp:
                cfg["temperature"] = 0.3
            return types.GenerateContentConfig(**cfg)

        try:
            response = gemini_client.models.generate_content(
                model=model, contents=contents, config=_config(True)
            )
        except Exception as e:
            if "temperature" in str(e).lower() or "sampling" in str(e).lower():
                response = gemini_client.models.generate_content(
                    model=model, contents=contents, config=_config(False)
                )
            else:
                raise RuntimeError(f"Archive (Gemini/{model}) failed: {e}") from e
        return response.text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# AI HANDLERS - IMAGE GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def handle_painter(narrative_text, refinement_instruction, narrator_provider):
    """
    The Painter — refines narrative text into a detailed image prompt
    using fast models. Sibling to handle_archive (the Archivist).
    """
    if narrator_provider == "gpt":
        model = GPT_ARCHIVIST_AND_REFINER
        kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": f"{refinement_instruction}\n\n{narrative_text}"}],
            "max_completion_tokens": 2000,
            "temperature": 0.7,
        }
        try:
            response = openai_client.chat.completions.create(**kwargs)
        except Exception as e:
            if "temperature" in str(e).lower():
                kwargs.pop("temperature", None)
                response = openai_client.chat.completions.create(**kwargs)
            else:
                raise RuntimeError(f"Painter (GPT/{model}) failed: {e}") from e
        return response.choices[0].message.content

    elif narrator_provider == "claude":
        model = CLAUDE_ARCHIVIST_AND_REFINER
        kwargs = {
            "model": model,
            "system": refinement_instruction,
            "messages": [{"role": "user", "content": narrative_text}],
            "max_tokens": 8000,
            "temperature": 0.7,
        }
        try:
            response = anthropic_client.messages.create(**kwargs)
        except Exception as e:
            if "temperature" in str(e).lower():
                kwargs.pop("temperature", None)
                response = anthropic_client.messages.create(**kwargs)
            else:
                raise RuntimeError(f"Painter (Claude/{model}) failed: {e}") from e
        return response.content[0].text

    else:  # Gemini
        model = GEMINI_ARCHIVIST_AND_REFINER
        contents = narrative_text

        def _config(with_temp: bool):
            # max_output_tokens was missing entirely before — an unbounded
            # image-prompt call is an easy place for cost to creep in.
            cfg = {"system_instruction": refinement_instruction, "max_output_tokens": 2000}
            if with_temp:
                cfg["temperature"] = 0.7
            return types.GenerateContentConfig(**cfg)

        try:
            response = gemini_client.models.generate_content(
                model=model, contents=contents, config=_config(True)
            )
        except Exception as e:
            if "temperature" in str(e).lower() or "sampling" in str(e).lower():
                response = gemini_client.models.generate_content(
                    model=model, contents=contents, config=_config(False)
                )
            else:
                raise RuntimeError(f"Painter (Gemini/{model}) failed: {e}") from e
        return response.text.strip()


def generate_image(visual_prompt, aspect_ratio="16:9"):
    if "imagen" in IMAGE_MODEL.lower():  # Gemini Imagen
        if not gemini_client:
            raise ValueError("GEMINI_API_KEY not configured for image generation")
        
        response = gemini_client.models.generate_images(
            model=IMAGE_MODEL,
            prompt=visual_prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                output_mime_type="image/png",
                safety_filter_level="block_low_and_above",
            )
        )
        
        if response.generated_images:
            image_obj = response.generated_images[0]
            img_b64 = base64.b64encode(image_obj.image.image_bytes).decode('utf-8')
            return img_b64
        else:
            raise ValueError("Image generation blocked by safety filters")

    elif "gpt-image" in IMAGE_MODEL.lower():  # GPT Image 1.5
        if not openai_client:
            raise ValueError("OPENAI_API_KEY not configured for GPT Image generation")
        
        response = openai_client.images.generate(
            model=IMAGE_MODEL,
            prompt=visual_prompt,
            n=1,
            size="1024x1024"  # Adjust aspect_ratio mapping if needed
        )
        
        img_b64 = response.data[0].b64_json
        if img_b64:
            return img_b64
        else:
            raise ValueError("GPT Image generation returned no image")
    
    elif "flash-image" in IMAGE_MODEL.lower():  # Nano Banana – gemini-2.5-flash-image
        if not gemini_client:
            raise ValueError("GEMINI_API_KEY not configured for image generation")

        response = gemini_client.models.generate_content(
            model=IMAGE_MODEL,
            contents=visual_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                temperature=1.0,
            )
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                img_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                return img_b64

        raise ValueError("Nano Banana returned no image — prompt may have been blocked or produced text only")

    else:
        raise ValueError(f"Unsupported IMAGE_MODEL: {IMAGE_MODEL}")


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    return send_from_directory('templates', 'index.html')


@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        retry_after = _check_rate_limit()
        if retry_after:
            return _rate_limited_response(retry_after)

        data = _json_body()

        # The headline cap: the player's typed action.
        player_action = _require_str(
            data.get('player_action'), "Action", MAX_ACTION_CHARS
        )

        # Everything else the client sends is bounded too. These fields are
        # forwarded to the model verbatim, so leaving any of them open is
        # the same hole as an uncapped action box.
        system_prompt = _require_str(
            data.get('system_prompt'), "system_prompt", MAX_SYSTEM_CHARS, required=True
        )
        context   = _require_str(data.get('context'), "context", MAX_CONTEXT_CHARS)
        history   = _validate_history(data.get('history'))
        summaries = _validate_summaries(data.get('summaries'))

        session_id = _require_str(data.get('session_id'), "session_id", 200) or 'unknown'
        turn       = data.get('turn', 0)
        region     = _require_str(data.get('region'), "region", 200)
        location   = _require_str(data.get('location'), "location", 200)

        # Populate the context viewer store for this turn
        _context_store["text"] = context

        if NARRATOR_PROVIDER == "claude":
            content = handle_sonnet(system_prompt, context, player_action, history, summaries)
        elif NARRATOR_PROVIDER == "gemini":
            content = handle_gemini(system_prompt, context, player_action, history, summaries)
        elif NARRATOR_PROVIDER == "gpt":
            content = handle_gpt(system_prompt, context, player_action, history, summaries)
        else:
            return jsonify({"error": f"Unsupported narrator model: {NARRATOR_MODEL}"}), 500

        return jsonify({"text": content})

    except InputError as e:
        return jsonify({"error": str(e)}), 400
    except HTTPException:
        raise
    except Exception as e:
        # Log the detail, return a generic message. str(e) here can contain
        # provider error text, model names, and occasionally key fragments.
        print(f"❌ Chat Error: {e}")
        return jsonify({"error": "Narration failed."}), 500


@app.route('/api/archive', methods=['POST'])
def archive_route():
    try:
        retry_after = _check_rate_limit()
        if retry_after:
            return _rate_limited_response(retry_after)

        data = _json_body()

        log_segment = _require_str(
            data.get('context'), "context", MAX_ARCHIVE_CHARS, required=True
        )
        archivist_prompt = _require_str(
            data.get('system_instruction'), "system_instruction", MAX_SYSTEM_CHARS
        )

        summary = handle_archive(log_segment, NARRATOR_PROVIDER, archivist_prompt)

        return jsonify({"text": summary})

    except InputError as e:
        return jsonify({"error": str(e)}), 400
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Archive Error: {e}")
        return jsonify({"error": "Archive failed."}), 500


@app.route('/api/painter', methods=['POST'])
def painter():
    try:
        # Image generation is the priciest thing here — two model calls per
        # request. Throttled harder than narration.
        retry_after = _check_rate_limit(limit=max(1, RATE_LIMIT_REQUESTS // 4))
        if retry_after:
            return _rate_limited_response(retry_after)

        data = _json_body()
        narrative_text = _require_str(
            data.get('prompt'), "prompt", MAX_PAINTER_CHARS, required=True
        )
        aspect_ratio = data.get('aspect_ratio', '16:9')
        if aspect_ratio not in ('16:9', '9:16', '1:1', '4:3', '3:4'):
            raise InputError("Unsupported aspect_ratio.")
        narrator_provider = data.get('narrator_provider', NARRATOR_PROVIDER)
        if narrator_provider not in ('gpt', 'claude', 'gemini'):
            raise InputError("Unsupported narrator_provider.")

        refinement_instruction = """You are an expert at converting narrative text into detailed image generation prompts.

Analyze the narrative and create a vivid, cinematic image prompt that captures:
- The key visual elements and composition
- The atmosphere and lighting
- Character positioning and actions
- Environmental details
- The emotional tone

Output ONLY the image prompt as a single detailed paragraph. Be specific about:
- Camera angle and framing
- Lighting conditions
- Color palette
- Textures and materials
- Style (photorealistic, illustrated, etc.)"""

        # Step 1: Refine narrative into image prompt using fast model
        print(f"🎨 Refining narrative for {narrator_provider}...")
        visual_prompt = handle_painter(
            narrative_text,
            refinement_instruction,
            narrator_provider=narrator_provider
        )
        print(f"✨ Refined prompt: {visual_prompt}")

        # Step 2: Generate image using Gemini Imagen or GPT Image
        print(f"🖼️ Generating image using {IMAGE_MODEL}...")
        img_b64 = generate_image(visual_prompt, aspect_ratio)

        return jsonify({
            "success": True,
            "image_base64": img_b64,
            "refined_prompt": visual_prompt
        })

    except InputError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Painter Error: {e}")
        return jsonify({
            "success": False,
            "error": "Image generation failed."
        }), 500


@app.route('/api/config', methods=['GET'])
def get_config():
    """Return current configuration"""
    return jsonify({
        "narrator_model": NARRATOR_MODEL,
        "narrator_provider": NARRATOR_PROVIDER,
        "image_model": IMAGE_MODEL,
        "image_provider": IMAGE_PROVIDER,
        "archivist_model": get_archivist_model(NARRATOR_PROVIDER)
    })


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT VIEWER
# ─────────────────────────────────────────────────────────────────────────────

_context_store = {"text": ""}

@app.route('/api/context', methods=['POST'])
def context_store():
    try:
        body = _json_body()
        _context_store["text"] = _require_str(
            body.get("text"), "text", MAX_STORED_CONTEXT
        )
    except InputError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})

@app.route('/api/context', methods=['GET'])
def context_view():
    # Escaped: this text is client-supplied and was previously interpolated
    # straight into the page, so anything a player typed could execute here.
    text = html.escape(_context_store.get("text") or "No context stored yet.")
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body {{ background: #080705; color: #c8c0b0; font-family: monospace;
         font-size: 0.78rem; line-height: 1.7; padding: 30px; margin: 0; }}
  pre {{ white-space: pre-wrap; word-break: break-word; }}
</style></head>
<body><pre>{text}</pre></body></html>"""


@app.route('/api/context/raw', methods=['GET'])
def context_view_raw():
    text = _context_store.get("text") or "No context stored yet."
    return app.response_class(text, mimetype='text/plain')


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*80)
    print("🎮 UNIFIED GAME BACKEND")
    print("="*80)
    print(f"📖 Narrator: {NARRATOR_MODEL} ({NARRATOR_PROVIDER})")
    print(f"🎨 Image Model: {IMAGE_MODEL} ({IMAGE_PROVIDER})")
    print("="*80 + "\n")
    
    # Verify at least one API key is configured
    if not (gemini_client or openai_client or anthropic_client):
        print("⚠️  WARNING: No API keys configured!")
        print("Set environment variables: GEMINI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY")
    
    app.run(debug=True, port=5001, use_reloader=False)
