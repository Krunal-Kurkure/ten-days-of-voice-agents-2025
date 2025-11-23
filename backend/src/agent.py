import json
import logging
import os
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

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

# load env
load_dotenv(".env.local")

# Orders file path (relative to backend folder)
BASE_DIR = Path(__file__).resolve().parent.parent  # backend/src -> backend
ORDERS_FILE = BASE_DIR / "orders.json"


# Utility: append order to JSON file (keeps a list)
def append_order_to_file(order: dict):
    try:
        ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if ORDERS_FILE.exists():
            with ORDERS_FILE.open("r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    if not isinstance(data, list):
                        data = []
                except Exception:
                    data = []
        else:
            data = []

        data.append(order)

        with ORDERS_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved order to {ORDERS_FILE}")
    except Exception as e:
        logger.exception("Failed to save order to file: %s", e)


# Define a function tool that the LLM/agent can call to persist the order.
# The function signature must be serializable by the agent framework.
@function_tool
async def save_order_tool(ctx: RunContext, order: dict):
    """
    Save the completed order to the orders.json file.
    The `order` param is expected to match the shape:
    {
      "drinkType": "string",
      "size": "string",
      "milk": "string",
      "extras": ["string"],
      "name": "string",
      "timestamp": "ISO8601"
    }
    """
    try:
        # add a timestamp if not present
        if "timestamp" not in order:
            order["timestamp"] = datetime.utcnow().isoformat() + "Z"
        append_order_to_file(order)
        return {"status": "ok", "message": "order saved"}
    except Exception as e:
        logger.exception("save_order_tool error: %s", e)
        return {"status": "error", "message": str(e)}


class BaristaAgent(Agent):
    def __init__(self) -> None:
        # The instructions tell the LLM to behave as a barista and to call the
        # save_order_tool once the order is complete.
        instructions = """
You are a friendly coffee shop barista for BrewMate Café. 
Your goal is to take voice orders and collect a structured order object with the following fields:
{
  "drinkType": "string",
  "size": "string",
  "milk": "string",
  "extras": ["string"],
  "name": "string"
}

Behavior rules (VERY IMPORTANT):
1. Ask short clarifying questions until every field in the order object is filled.
2. Accept natural answers and extract the values. If a user gives multiple pieces of info at once, fill all relevant fields.
3. For `size`, accept "small", "medium", or "large" (normalise capitalization).
4. For `extras`, accept items like 'caramel', 'sugar', 'whipped cream', 'soy', 'extra shot' — produce an array of strings (may be empty).
5. For `milk`, accept 'whole', 'skim', 'almond', 'oat', 'soy', 'none' (or similar).
6. For `name`, accept any short identifier (first name is fine).
7. After you have all fields, confirm the order verbally to the user (one short confirmation sentence).
8. THEN call the function `save_order_tool(order)` with the final order object (including a timestamp).
9. Use a warm and concise tone. Avoid excessive punctuation or emojis.

If the user asks for changes before confirmation, modify the order (e.g., user says "make that medium" -> update size).
"""
        super().__init__(instructions=instructions)


def prewarm(proc: JobProcess):
    # prepare VAD model (silero) so start-up latency is lower
    logger.info("Prewarming VAD")
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    # Add helpful logging context for easier debugging
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Build the session with STT / LLM / TTS etc.
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
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    # Metrics collector
    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Usage: {summary}")

    ctx.add_shutdown_callback(log_usage)

    # Register the tool so the LLM can call save_order_tool(order)
    # In the livekit agents framework the decorator above is enough,
    # but registering is still useful for clarity if needed.
    # (No explicit registration code required in many versions.)

    # Start the session with our BaristaAgent
    await session.start(
        agent=BaristaAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()),
    )

    # Connect and wait for participants (this keeps the agent running)
    await ctx.connect()


if __name__ == "__main__":
    # Run the worker
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
