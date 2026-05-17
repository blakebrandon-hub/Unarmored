import os
import base64
import json
import time
from flask import Flask, request, jsonify, send_from_directory, Blueprint
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

# ─────────────────────────────────────────────
# MODEL SELECTION
# ─────────────────────────────────────────────

# Narrator Model
# "claude-sonnet-4-6" | "gemini-3.1-pro-preview" | "gpt-5.4"
NARRATOR_MODEL = os.environ.get("NARRATOR_MODEL", "gemini-3-flash-preview") # # gemini-3-flash-preview # gemini-3.1-pro-preview-customtools

# Image Generation Model
# Options: "imagen-4.0-fast-generate-001" | "gpt-image-1.5" | "gemini-2.5-flash-image" (Nano Banana)
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "imagen-4.0-fast-generate-001")

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

    # Build messages: history + current action
    messages = list(history) + [{"role": "user", "content": player_action}]

    response = anthropic_client.messages.create(
        model=NARRATOR_MODEL,
        max_tokens=8192,
        temperature=0.7,
        system=system_parts,
        messages=messages
    )

    return response.content[0].text

# Better version with caching
"""def handle_gemini(system_prompt, context, player_action):
    # 1. Create the Cache
    cache = gemini_client.caches.create(
        model=NARRATOR_MODEL,
        config=types.CreateCachedContentConfig(
            display_name="neon_bazaar_logic",
            system_instruction=system_prompt,
            ttl="300s",
        )
    )
    
    # 2. Optimized Generation Config
    config = types.GenerateContentConfig(
        cached_content=cache.name,
        thinking_config=types.ThinkingConfig(thinking_level="low"),
        temperature=1.0, 
        max_output_tokens=8000, 
    )
    
    # 3. Generate
    response = gemini_client.models.generate_content(
        model=NARRATOR_MODEL,
        contents=[
            types.Part.from_text(text=f"### CURRENT_STATE ###\n{context}"),
            types.Part.from_text(text=f"### USER_INPUT ###\n{player_action}")
        ],
        config=config
    )
    
    return response.text"""

def handle_gemini(system_prompt, context, player_action, history=[], summaries=[]):
    summary_block = ""
    if summaries:
        summary_block = "\n\n## ◈ CHRONICLE ARCHIVE (oldest to most recent)\n" + \
            "\n---\n".join(f"[Archive {i+1}]\n{s}" for i, s in enumerate(summaries))

    full_system = system_prompt + (summary_block if summary_block else "")

    # Build contents: history pairs + current state + action
    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))

    # Append current state + action as final user turn
    contents.append(types.Content(role="user", parts=[
        types.Part.from_text(text=f"### CURRENT_STATE ###\n{context}"),
        types.Part.from_text(text=f"### USER_INPUT ###\n{player_action}")
    ]))

    config = types.GenerateContentConfig(
        system_instruction=full_system,
        temperature=1.0,
        max_output_tokens=8000,
    )

    response = gemini_client.models.generate_content(
        model=NARRATOR_MODEL,
        contents=contents,
        config=config
    )

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

    response = openai_client.chat.completions.create(
        model=NARRATOR_MODEL,
        messages=gpt_messages,
        temperature=1.0,
        max_completion_tokens=8000
    )

    return response.choices[0].message.content


# ─────────────────────────────────────────────────────────────────────────────
# AI HANDLERS - ARCHIVING (SUMMARIZATION)
# ─────────────────────────────────────────────────────────────────────────────

def handle_archive(log_segment, narrator_provider, archivist_prompt):
    """
    Archive/summarize conversation logs using hard models.
    """
    if narrator_provider == "gpt":
        model = GPT_ARCHIVIST_AND_REFINER
        response = openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": f"Summarize:\n{log_segment}"}],
            temperature=0.3,
            max_tokens=8000
        )
        return response.choices[0].message.content

    elif narrator_provider == "claude":
        model = CLAUDE_ARCHIVIST_AND_REFINER
        response = anthropic_client.messages.create(
            model=model,
            system="Summarize the following conversation accurately.",
            messages=[{"role": "user", "content": log_segment}],
            max_tokens=8000,
            temperature=0.3
        )
        return response.content[0].text

    else:  # Gemini default
        model = GEMINI_ARCHIVIST_AND_REFINER
        response = gemini_client.models.generate_content(
            model=model,
            contents=f"Log Segment to Archive:\n{log_segment}",
            config=types.GenerateContentConfig(
                system_instruction=archivist_prompt,
                temperature=0.3
            )
        )
        return response.text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# AI HANDLERS - IMAGE GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def refine_image_prompt(narrative_text, refinement_instruction, narrator_provider):
    """
    Refine narrative text into detailed image prompt using fast models.
    """
    if narrator_provider == "gpt":
        model = GPT_ARCHIVIST_AND_REFINER
        response = openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": f"{refinement_instruction}\n\n{narrative_text}"}],
            temperature=0.7,
            max_completion_tokens=2000
        )
        return response.choices[0].message.content

    elif narrator_provider == "claude":
        model = CLAUDE_ARCHIVIST_AND_REFINER
        response = anthropic_client.messages.create(
            model=model,
            system=refinement_instruction,
            messages=[{"role": "user", "content": narrative_text}],
            temperature=0.7,
            max_tokens=2000
        )
        return response.content[0].text

    else:  # Gemini
        model = GEMINI_ARCHIVIST_AND_REFINER
        response = gemini_client.models.generate_content(
            model=model,
            contents=narrative_text,
            config=types.GenerateContentConfig(
                system_instruction=refinement_instruction,
                temperature=0.7
            )
        )
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
        data = request.json
        system_prompt = data.get('system_prompt', '')
        context = data.get('context', '')
        player_action = data.get('player_action', '')
        history = data.get('history', [])       # last 12 messages [{role, content}, ...]
        summaries = data.get('summaries', [])   # up to 4 archive summaries (oldest first)

        if not system_prompt:
            return jsonify({"error": "system_prompt is required"}), 400

        if len(player_action) > 200:
            return jsonify({"error": "Action too long. 200 characters maximum."}), 400

        # Route to the correct narrator
        if NARRATOR_PROVIDER == "claude":
            content = handle_sonnet(system_prompt, context, player_action, history, summaries)
        elif NARRATOR_PROVIDER == "gemini":
            content = handle_gemini(system_prompt, context, player_action, history, summaries)
        elif NARRATOR_PROVIDER == "gpt":
            content = handle_gpt(system_prompt, context, player_action, history, summaries)
        else:
            return jsonify({"error": f"Unsupported narrator model: {NARRATOR_MODEL}"}), 500

        return jsonify({"text": content})

    except Exception as e:
        print(f"❌ Chat Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/archive', methods=['POST'])
def archive_route():
    try:
        data = request.json
        
        log_segment = data.get('context', '')
        archivist_prompt = data.get('system_instruction', '')
        
        summary = handle_archive(log_segment, NARRATOR_PROVIDER, archivist_prompt)
        
        return jsonify({"text": summary})
    
    except Exception as e:
        print(f"❌ Archive Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/painter', methods=['POST'])
def painter():
    try:
        data = request.json
        narrative_text = data.get('prompt', '')
        aspect_ratio = data.get('aspect_ratio', '16:9')
        narrator_provider = data.get('narrator_provider', NARRATOR_PROVIDER)

        refinement_instruction = """You are an expert at converting narrative text into highly detailed image generation prompts for a grounded, historically authentic medieval drama set in Bohemia, 1403.

Analyze the narrative and create a vivid, cinematic image prompt that captures the world of Unarmored — unglamorous, tactile, and real. Focus on:
- The key visual elements: peasants, minor nobles, village life, forests, muddy roads, stone churches, timber halls, and the occasional soldier or armed traveler.
- The atmosphere and lighting: overcast northern European skies, tallow candlelight, firelight, cold March light through unglazed windows, long shadows at dusk.
- Textures and materials: rough-spun linen, boiled leather, tarnished iron, weathered oak, packed earth, straw, animal hides, parchment with wax seals.
- The emotional tone: quiet tension, poverty, duty, grief, the weight of obligation. Not epic — intimate and specific.

Output ONLY the image prompt as a single, highly detailed paragraph. Be specific about:
- Camera angle and framing (e.g., eye-level, medium shot, over-the-shoulder).
- Lighting conditions (flat overcast, firelight from below, pale winter sun).
- Color palette (muted earth tones — ochre, ash, umber, dull iron — with no saturated colors).
- Style (historically grounded medieval realism, painterly, in the style of Pieter Bruegel the Elder or early Flemish panel painting, cinematic but unglamorous)."""

        # Step 1: Refine narrative into image prompt using fast model
        print(f"🎨 Refining narrative for {narrator_provider}...")
        visual_prompt = refine_image_prompt(
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

    except Exception as e:
        print(f"❌ Painter Error: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
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
    
    app.run(debug=True, port=5000)
