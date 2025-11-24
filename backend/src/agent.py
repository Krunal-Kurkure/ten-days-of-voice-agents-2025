# backend/src/agent.py
"""
LiveKit agent for Day 2 (Coffee Shop Barista) and Day 3 (Health & Wellness Companion).

Key behavior:
- Strong system prompt instructs the LLM to perform step-by-step slot-filling
  for coffee orders and wellness check-ins.
- When an order or check-in is complete, the LLM must call the function tools:
    - save_order_tool(ctx, order)
    - save_wellness_tool(ctx, entry)
  which persist to backend/order.json and backend/wellness.json respectively.
- A lightweight transcript-based heuristic auto-save is kept as a fallback.
"""
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    metrics,
    tokenize,
    function_tool,
    RunContext,
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(ch)

# load env
load_dotenv(".env.local")

# --------------------------
# Backend file locations
# --------------------------
BASE_DIR = Path(__file__).resolve().parent.parent  # backend/src -> backend
ORDER_FILE = BASE_DIR / "order.json"
WELLNESS_FILE = BASE_DIR / "wellness.json"
BASE_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_json_array_file(path: Path) -> None:
    try:
        if not path.exists():
            logger.info("Creating data file: %s", path)
            path.write_text("[]", encoding="utf-8")
            return
        text = path.read_text(encoding="utf-8").strip()
        if text == "":
            logger.warning("%s was empty — initializing to []", path)
            path.write_text("[]", encoding="utf-8")
            return
        try:
            v = json.loads(text)
            if not isinstance(v, list):
                logger.warning("%s did not contain a JSON array — resetting to []", path)
                path.write_text("[]", encoding="utf-8")
        except Exception:
            logger.warning("%s contained invalid JSON — resetting to []", path)
            path.write_text("[]", encoding="utf-8")
    except Exception as e:
        logger.exception("Failed to ensure JSON file %s: %s", path, e)
        raise


_ensure_json_array_file(ORDER_FILE)
_ensure_json_array_file(WELLNESS_FILE)


# --------------------------
# Atomic append helper
# --------------------------
def _atomic_append(path: Path, entry: Dict) -> None:
    try:
        text = path.read_text(encoding="utf-8") or "[]"
        try:
            data = json.loads(text)
            if not isinstance(data, list):
                logger.warning("Replacing non-array content at %s with []", path)
                data = []
        except Exception:
            logger.warning("Invalid JSON in %s, starting fresh as []", path)
            data = []

        data.append(entry)

        dirpath = path.parent
        dirpath.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(dirpath), delete=False) as tf:
            json.dump(data, tf, indent=2, ensure_ascii=False)
            tempname = tf.name
        os.replace(tempname, str(path))
        logger.info("Atomic write complete: %s (appended id=%s)", path, entry.get("id"))
    except Exception as e:
        logger.exception("Failed atomic append to %s: %s", path, e)
        raise


# --------------------------
# Function tools: called by LLM
# --------------------------
@function_tool
async def save_order_tool(ctx: RunContext, order: Dict) -> Dict:
    """
    save_order_tool is expected to be called by the LLM when the barista has
    collected all required fields. Order schema (Day 2 required):
    {
      "drinkType": "string",
      "size": "string",
      "milk": "string",
      "extras": ["string"],
      "name": "string",
      "timestamp": "ISO8601",  # optional (we add if missing)
      "id": 1234567890         # tool will add an id
    }
    """
    try:
        logger.info("save_order_tool invoked with: %s", order)
        if "timestamp" not in order:
            order["timestamp"] = datetime.utcnow().isoformat() + "Z"
        if "extras" in order and not isinstance(order["extras"], list):
            if isinstance(order["extras"], str):
                order["extras"] = [s.strip() for s in order["extras"].split(",") if s.strip()]
            else:
                order["extras"] = [str(order["extras"])]
        defaults = {
            "drinkType": "coffee",
            "size": "regular",
            "milk": "regular",
            "extras": [],
            "name": "anonymous",
        }
        for k, default in defaults.items():
            if k not in order or order[k] is None:
                order[k] = default
        order["id"] = int(datetime.utcnow().timestamp() * 1000)
        _atomic_append(ORDER_FILE, order)
        logger.info("Order saved to %s id=%s", ORDER_FILE, order["id"])
        return {"status": "ok", "message": "order saved", "order": order}
    except Exception as e:
        logger.exception("save_order_tool error: %s", e)
        return {"status": "error", "message": str(e)}


@function_tool
async def save_wellness_tool(ctx: RunContext, entry: Dict) -> Dict:
    """
    save_wellness_tool is expected to be called by the LLM when the wellness
    check-in has collected the required items. Example entry schema (Day 3):
    {
      "timestamp": "...",
      "mood": "string",
      "energy": "string",
      "stress": "string (optional)",
      "objectives": ["..."],
      "summary": "agent-generated summary",
      "id": 1234567890
    }
    """
    try:
        logger.info("save_wellness_tool invoked with: %s", entry)
        if "timestamp" not in entry:
            entry["timestamp"] = datetime.utcnow().isoformat() + "Z"
        if "objectives" in entry and not isinstance(entry["objectives"], list):
            if isinstance(entry["objectives"], str):
                entry["objectives"] = [s.strip() for s in entry["objectives"].split(",") if s.strip()]
            else:
                entry["objectives"] = [str(entry["objectives"])]
        entry["id"] = int(datetime.utcnow().timestamp() * 1000)
        _atomic_append(WELLNESS_FILE, entry)
        logger.info("Wellness entry saved to %s id=%s", WELLNESS_FILE, entry["id"])
        return {"status": "ok", "message": "entry saved", "entry": entry}
    except Exception as e:
        logger.exception("save_wellness_tool error: %s", e)
        return {"status": "error", "message": str(e)}


# --------------------------
# Heuristics: transcript fallback (unchanged)
# --------------------------
COFFEE_KEYWORDS = ["coffee", "latte", "americano", "espresso", "cappuccino", "mocha", "order", "iced"]
SIZE_KEYWORDS = ["small", "medium", "large", "regular"]
MILK_KEYWORDS = ["oat", "almond", "soy", "skim", "whole", "regular"]
EXTRA_KEYWORDS = ["whipped", "extra shot", "shot", "vanilla", "caramel", "syrup", "cinnamon"]
WELLNESS_KEYWORDS = ["wellness", "well-being", "feeling", "mood", "energy", "stressed", "stress", "tired"]


def simple_intent_detect_from_text(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in COFFEE_KEYWORDS):
        return "coffee"
    if any(k in t for k in WELLNESS_KEYWORDS):
        return "wellness"
    return "chat"


def extract_coffee_slots_from_text(text: str) -> Dict:
    t = (text or "").lower()
    slots = {}
    for d in ["latte", "americano", "espresso", "cappuccino", "mocha", "flat white", "black coffee", "iced coffee"]:
        if d in t:
            slots["drinkType"] = d
            break
    for s in SIZE_KEYWORDS:
        if s in t:
            slots["size"] = s
            break
    for m in MILK_KEYWORDS:
        if m in t:
            slots["milk"] = m
            break
    extras = []
    for ex in EXTRA_KEYWORDS:
        if ex in t:
            extras.append(ex)
    if extras:
        slots["extras"] = extras
    # naive name extraction: "for <name>"
    if " for " in t:
        try:
            after = text.lower().split(" for ", 1)[1].strip().split()[0]
            slots["name"] = after.title()
        except Exception:
            pass
    return slots


def build_order_from_slots(slots: Dict, raw_text: Optional[str] = None) -> Dict:
    order = {
        "drinkType": slots.get("drinkType", "coffee"),
        "size": slots.get("size", "regular"),
        "milk": slots.get("milk", "regular"),
        "extras": slots.get("extras", []),
        "name": slots.get("name", "anonymous"),
        "raw_text": raw_text or "",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "id": int(datetime.utcnow().timestamp() * 1000),
    }
    return order


def build_wellness_from_text(text: str, speaker: Optional[str] = None) -> Dict:
    t = (text or "").lower()
    mood = ""
    energy = ""
    if any(w in t for w in ["good", "great", "fine", "happy"]):
        mood = "positive"
    if any(w in t for w in ["tired", "low", "exhausted"]):
        mood = "low"
    if "high energy" in t or "energetic" in t:
        energy = "high"
    if "low energy" in t or "tired" in t:
        energy = "low"
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "mood": mood,
        "energy": energy,
        "stress": "",
        "objectives": [],
        "summary": text,
        "speaker": speaker or "unknown",
        "id": int(datetime.utcnow().timestamp() * 1000),
    }
    return entry


# --------------------------
# MultiPersonaAgent: detailed system prompt (Day 2 & Day 3 script)
# --------------------------
class MultiPersonaAgent(Agent):
    def __init__(self) -> None:
        # Very specific, step-by-step instructions so the LLM will ask for missing info.
        # The LLM must call the function tools at the end of each completed flow.
        instructions = r"""
You are a multi-role voice assistant that can switch between two primary personas:

1) Coffee Shop Barista (Day 2 task) — friendly, efficient, clarifying.
2) Health & Wellness Companion (Day 3 task) — calm, supportive, non-diagnostic.

GENERAL RULES:
- At the start of a user's utterance, detect intent: coffee order vs wellness check-in vs casual chat.
- If the user mentions ordering a drink, switch to Barista persona.
- If the user mentions mood, energy, feeling, or wellness, switch to Wellness persona.
- If intent is unclear, ask a single clarifying question to disambiguate (e.g., "Do you want to place a coffee order or do a wellness check-in today?").

BARISTA (DAY 2) — REQUIRED FLOW:
- Maintain an order object exactly with these fields:
  {
    "drinkType": "string",   # e.g., latte, americano
    "size": "string",        # small, medium, large (or 'regular')
    "milk": "string",        # oat, almond, soy, skim, whole, regular
    "extras": ["string"],    # list of extras like "extra shot", "vanilla"
    "name": "string"         # customer's name
  }
- Ask these clarifying questions (in this order), but skip any question if the field is already supplied by the user's utterance:
  1) "What drink would you like today? (e.g., latte, americano, espresso)"
  2) "What size — small, medium, or large?"
  3) "Any milk preference? (oat, almond, soy, skim, whole, regular)"
  4) "Any extras? (whipped cream, extra shot, vanilla syrup)"
  5) "Who is the order for?"
- After you have all fields populated, call the function:
    save_order_tool(order)
  where `order` matches the schema above. The `timestamp` and `id` may be added by the tool.
- Then say a brief confirmation (one sentence), for example:
    "Got it — I saved your order: Large Oat Milk Latte for Krunal."
- If the user wants to change something before saving, accept the change, update the fields, and only call save_order_tool when they confirm.

WELLNESS (DAY 3) — REQUIRED FLOW:
- Conduct a short check-in with the following steps (ask them in this order):
  1) "How are you feeling today?" (collect 'mood' free text)
  2) "What's your energy like — high, moderate, or low?" (collect 'energy')
  3) "Is anything stressing you right now?" (collect 'stress' short text, optional)
  4) "What are 1–3 simple objectives you'd like to accomplish today?" (collect 'objectives' list)
  5) Offer one small, practical, non-medical suggestion (e.g., "Consider a 5-minute walk" or "Try a 20-minute focused session").
  6) Summarize in one short sentence and repeat the main objectives.
  7) Confirm: "Does this sound right?"
- When mood, energy, and objectives are captured and the user confirms, call:
    save_wellness_tool(entry)
  where `entry` includes at least: timestamp, mood, energy, objectives (list), summary.
- After the tool returns, verbally confirm: "Your check-in is saved."

IMPORTANT BEHAVIOR RULES:
- Keep responses short (1–2 sentences) and kind.
- Never give medical diagnoses or clinical instructions.
- Use the function tools only when the respective data collection is complete and after explicit confirmation (or when user clearly states "save"/"that's fine"/"yes, save it").
- When unsure about formats (e.g., objectives as a single string), convert sensibly to a list by splitting on commas and trimming whitespace.

SAMPLE DIALOGUES (use these styles):
User: "I want a large oat latte for Alex"
Agent: (parses fields and, if all present) -> call save_order_tool(order) -> "Got it — I saved your order: Large Oat Latte for Alex."
User: "How am I today?" / "I'm feeling tired and I have low energy"
Agent: (asks energy if missing, asks objectives) -> call save_wellness_tool(entry) -> "Thanks — I saved your check-in."

DEVELOPER NOTE:
- If an utterance clearly contains a completed order or check-in, calling the corresponding function tool directly is acceptable.
- If the conversation needs follow-up, ask the single follow-up question required to complete the data.
"""
        super().__init__(instructions=instructions)


# --------------------------
# Prewarm (VAD)
# --------------------------
def prewarm(proc: JobProcess):
    logger.info("Prewarming VAD")
    proc.userdata["vad"] = silero.VAD.load()


# --------------------------
# Entrypoint: attach transcript handler as fallback
# --------------------------
async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(
            voice="en-US-matthew",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata.get("vad"),
        preemptive_generation=True,
    )

    saved_hashes = set()

    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info("Usage summary: %s", summary)

    ctx.add_shutdown_callback(log_usage)

    # Transcript listener fallback (keeps previous heuristic behavior)
    @session.on("transcript")
    def _on_transcript(ev):
        try:
            text = getattr(ev, "text", None) or getattr(ev, "transcript", None) or ""
            participant = getattr(ev, "participant_name", None) or getattr(ev, "participant", None) or "unknown"
            text = (text or "").strip()
            if not text:
                return
            h = hash(text)
            if h in saved_hashes:
                logger.debug("Transcript already saved (dedupe) skipping: %s", text)
                return
            intent = simple_intent_detect_from_text(text)
            logger.debug("Transcript (fallback) intent=%s participant=%s text=%s", intent, participant, text)

            if intent == "coffee":
                t = text.lower()
                has_size = any(s in t for s in SIZE_KEYWORDS)
                has_drink = any(d in t for d in ["latte", "americano", "espresso", "cappuccino", "mocha", "iced"])
                has_for = " for " in t or "for " in t
                has_order_word = "order" in t or "i'd like" in t or "i would like" in t or "i want" in t
                if has_drink or has_size or has_for or has_order_word:
                    slots = extract_coffee_slots_from_text(text)
                    order = build_order_from_slots(slots, raw_text=text)
                    try:
                        _atomic_append(ORDER_FILE, order)
                        saved_hashes.add(h)
                        logger.info("[auto-save] Saved coffee order from transcript id=%s text=%s", order["id"], text)
                    except Exception as e:
                        logger.exception("Failed to auto-save order: %s", e)
                    return

            if intent == "wellness":
                t = text.lower()
                triggers = ["i'm feeling", "i am feeling", "feeling", "mood", "energy", "wellness", "check-in", "check in"]
                if any(trg in t for trg in triggers):
                    entry = build_wellness_from_text(text, speaker=participant)
                    try:
                        _atomic_append(WELLNESS_FILE, entry)
                        saved_hashes.add(h)
                        logger.info("[auto-save] Saved wellness entry id=%s text=%s", entry["id"], text)
                    except Exception as e:
                        logger.exception("Failed to auto-save wellness entry: %s", e)
                    return
        except Exception as e:
            logger.exception("Error in transcript handler: %s", e)

    # Start session with the MultiPersonaAgent (LLM-driven)
    await session.start(
        agent=MultiPersonaAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()),
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
