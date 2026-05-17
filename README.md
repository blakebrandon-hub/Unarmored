# UNARMORED
### Silesia, 1325

A grounded medieval RPG powered by a large language model narrator. You play Werner, the son of a tanner in Hartburg village, Silesia. Your father died in April — owing guild dues and an unfinished commission to Lord Dietmar's steward. You inherited the tools, the hide, and the debt.

<img width="1366" height="768" alt="Screenshot 2026-05-14 045730" src="https://github.com/user-attachments/assets/e243fcdd-c5f3-4854-b8eb-af28f99da093" />

---

## What It Is

Unarmored is a text-based RPG where an AI acts as a Chronicler — narrating Werner's story in third person, past tense, prose only. It tracks a full game state (stats, skills, inventory, equipment, currency, standing, morale, and vows) and updates it silently based on what the Chronicler emits. You type what Werner does. The Chronicler describes what happens.

The world is unglamorous and tactile. Leather, mud, debt, iron. Combat is dangerous and consequential. Skill determines outcome, not player intent.

---

## Gameplay

### The Chronicle

The central panel is the Chronicle — a running narrative of Werner's story. Each turn you describe an action (200 character limit) and the Chronicler responds with a short scene (roughly 75 words). The Chronicler reads Werner's exact skill values and filters everything through them silently. A Werner with Leathercraft 12 works clumsily. A Werner with Leathercraft 45 works with precision. You never see the numbers referenced in prose.

### Stats

| Stat | Starting Value | Grows Through |
|---|---|---|
| Strength | 11 | Hauling, sustained labor, combat |
| Quickness | 10 | Stealth, speed work, combat footwork |
| Wit | 9 | Reading, alchemy, healing, problem-solving |
| HP | 100 | — |

Wit also affects skill gain rate. High Wit means Werner learns faster.

### Skills

Skills are tracked 0–100 across three categories:

**Combat:** Swordsmanship, Mace Fighting, Fencing, Wrestling, Archery, Parrying

**Knowledge:** Reading, Healing, Herbalism, Tracking, Riding, Stealth, Persuasion

**Craft:** Alchemy, Blacksmithy, Bowcraft, Leathercraft, Lumberjacking, Mining, Skinning

Skills grow through genuine use — the Chronicler emits a tag when Werner earns it. Crafting, foraging, and item use also grant skill experience through the UI directly (see below).

### Vows

Vows are active objectives with named obligations and promised payment. Werner begins with one: finish and deliver the riding leathers to Dietmar's steward for 8 Groschen and a cleared debt. Vows track in the left panel. They open and close through play.

### Standing

Six factions track Werner's reputation: Hartburg, Dietmar, Breslau, Tanner's Guild, The Church, and The Road. Standing shifts through actions, trade, and consequence. The Road is the reputation that travels between places — what merchants and travelers say about Werner at the next inn.

### Hearts

Morale tracks Werner's relationships with individual people in Hartburg: Oswin, Ilse, Greta, Konrad, Dietrich, Vogel. Morale shifts through kindness, neglect, trust, and betrayal. It is never announced in prose — only shown in behavior, then sealed in a tag.

---

## The Workshop (Crafting)

Open the Workshop panel from the left sidebar. Crafting is organized into five tabs:

- **Leather** — Curing, hardening, cutting straps, assembling armor and gear
- **Blacksmith** — Requires access to Konrad's forge; smelting ore, forging blades and heads
- **Bowcraft** — Arrows, bows, and composite gear
- **Alchemy** — Requires Ilse's mortar and pestle; salves, tinctures, poultices
- **Forage** — Searching the current location for herbs and plants (Herbalism-gated)

Each recipe shows required ingredients, the relevant skill, and a skill requirement threshold. Crafting costs ingredients from your inventory and has a chance-based outcome — higher skill relative to the recipe means higher success rate and more reliable skill gain. Failures consume materials but still award a consolation skill point. Each successful craft or use grants +2 to the relevant skill (if the chance roll succeeds).

---

## Equipment

Werner can equip items across ten slots: Head, Neck, Chest, Arms, Hands, Belt, Legs, Feet, Weapon, and Shield. Only equipped items provide combat benefit. A bow in the pack is not a bow in the hand. Items with a Strength requirement show in red if Werner doesn't meet it.

---

## Combat

Combat is not turn-based. Werner describes what he does and the Chronicler resolves it. Strength determines damage weight. Quickness determines who acts first. Skill determines technique. A vague action earns a precise punishment — "I attack him" is not a plan.

Wolves, bandits, and other threats are genuinely dangerous. Death is possible and the game does not soften it. The death screen offers to begin again or load a save.

---

## AI Architecture

### Narrator

The narrator runs on whichever model is set in `NARRATOR_MODEL`. Each turn it receives the full system prompt (character, world, rules, NPC descriptions, tag format), a rolling window of the last 12 conversation messages, up to 4 archived summaries of older history, and the current game state (stats, skills, inventory, equipment, vows, standing, morale, location, combat status).

### Archiving

Every 12 messages, the Archivist (a fast lightweight model) summarizes the oldest message window into a Chronicle Archive entry. Up to 4 summaries are kept in a rolling window, giving the narrator long-term memory without unbounded context growth.

### Visualize

The Visualize button sends the most recent chronicle entry to the image pipeline: a fast model refines the narrative prose into a detailed image prompt (historically grounded, Bruegel-style, Bohemian medieval), then the image model generates a scene. Supported image models: Gemini Imagen, GPT Image, and Gemini Flash Image.

---

## Saving and Loading

Use the Save and Load buttons in the left panel. Saves are JSON files containing full game state, conversation log, and archive summaries. The game also autosaves to localStorage between sessions.

---

## Audio

Four ambient tracks and four combat tracks play in rotation. The game switches automatically between ambient and combat music based on the `[Combat: Start]` / `[Combat: End]` tags emitted by the Chronicler. Music defaults to off — toggle it from the left panel.

Text-to-speech is also available. When enabled, the browser reads each Chronicle entry aloud in a low, slow voice. Toggle from the left panel.

---

## The World

**Silesia, 1325.** The Piast dynasty is fracturing. Minor lords navigate competing duke allegiances. John of Luxembourg quietly acquires territory. Discharged soldiers move the roads. Guild law governs crafts. German settlers and Polish locals share the region uneasily.

### Hartburg

A village. Tannery at the edge. The Grey Goose inn at the center. The priest knows more than he says. A job board stands near the well — notices nailed over older notices, some weathered to illegibility.

### Key Locations

| Location | Region | Notes |
|---|---|---|
| Werner's Home | Hartburg | Tannery, workbench, home storage |
| The Grey Goose | Hartburg | Inn. Greta. Credit extended carefully. |
| Hartburg Smithy | Hartburg | Konrad's forge. Fee or favor to use. |
| Hartburg Stables | Hartburg | Horses for hire. The stableman knows who passes through. |
| Oswin's Hut | Hartburg | Forest edge. Timber, tools. Gateway to Lumberjacking. |
| Ilse's Home | Hartburg | Herbs drying, sharp smells. Gateway to Alchemy. |
| Dietmar's Estate | Silesia | Two hours east. Steward Hesso. Precise records. |
| Hartburg Forest | Silesia | Dense. Dangerous further in. Wolves. |
| The Road to Breslau | Silesia | A day's walk. Merchants, soldiers, danger. |
| Breslau Gates | Breslau | Captain Vogel. Tolls, questions, assessment. |
| Breslau Market | Breslau | Iron, tools, trade goods. |
| Breslau Guild Hall | Breslau | The tanners' guild. Legal obligations live here. |

---

## The People

**Greta** — Tavern keep. Widow. Extends credit carefully. Gateway to Hartburg's information network and steady work at the inn.

**Oswin** — Woodcutter, family friend. Quiet, reliable, knows the forest. Will lend his axe or let Werner work alongside him once he's watched long enough. Unlocks Lumberjacking.

**Ilse** — Midwife, herbalist. Has been watching Werner since birth. Will teach herbalism and eventually give him her mortar and pestle — but only if he pays attention. Unlocks Alchemy.

**Konrad** — The Smith. Wide man, leather apron stiff with grease. Will let Werner use the forge for a fee or a favor. Watches his tools.

**Emmerich** — Bard. Travels between courts and market towns. Fluent in Latin, German, a little Polish. Can push Werner's Reading further than he could manage alone. His currency is usefulness.

**Dietrich** — Discharged soldier, passing through and not quite leaving. Will train Werner in Swordsmanship for leather work or useful trade. When he decides Werner has earned it, he gives him his spare sword — not sells. Gives.

**Ernst Vogel** — Captain of the Breslau Gate Guard. Twenty years in city service. Not corrupt — calibrated. Honest answers and legitimate business move through quickly.

---

## Currency

12 Haler = 1 Groschen

| Item | Cost |
|---|---|
| Ale | 2–3 Haler |
| Bread | 1–2 Haler |
| Night at the inn | 6–8 Haler |
| Day labor | 6–8 Haler |
| Tanning Knife | 2 Groschen |
| Sword | 8–18 Groschen |

---

## Setup

### Requirements

- Python 3.10+
- Flask
- At least one API key: Gemini, OpenAI, or Anthropic

### Installation

```bash
pip install flask flask-cors google-genai anthropic openai
```

### Configuration

Set environment variables for whichever providers you want to use:

```bash
export GEMINI_API_KEY=your_key_here
export OPENAI_API_KEY=your_key_here
export ANTHROPIC_API_KEY=your_key_here
```

The narrator model and image model are configured at the top of `app.py`:

```python
NARRATOR_MODEL = "gemini-3-flash-preview"   # or a Claude/GPT model
IMAGE_MODEL    = "imagen-4.0-fast-generate-001"
```

Model selection determines provider automatically. Any model name containing `claude` routes to Anthropic, `gemini` or `imagen` to Google, and `gpt` to OpenAI.

### Running

```bash
python app.py
```

Then open `http://localhost:5000` in your browser.

---

## License

This project is not open source. All rights reserved.
