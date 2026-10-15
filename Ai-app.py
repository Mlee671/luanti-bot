"""Let an AI play Luanti, choosing tools one at a time until it holds a diamond.

    python Ai-app.py

Each turn: observe the game state -> send it to the model with the tool list
-> run whichever tool it picks -> remember the result -> repeat.

The tools (bot_tools/actions.py) are predetermined sequences of in-game
actions; the model only decides which one to run next and with what arguments.

Luanti settings come from LUANTI_HOST / LUANTI_PORT / LUANTI_USER /
LUANTI_PASSWORD, the model from LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
(see bot_tools/llm.py). MAX_TURNS caps the run (default 60).
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from luantibot import Bot
from bot_tools import actions, llm, names, state

GOAL_ITEM = "mcl_core:diamond"     # or any mod's ":diamond", e.g. default:diamond


def holding_diamond(bot):
    return any(k.split(":")[-1] == "diamond" for k in bot.item_counts())
MAX_TURNS = int(os.environ.get("MAX_TURNS", 60))
HISTORY = 10          # how many past turns the model is reminded of

SYSTEM_PROMPT = f"""You control a bot in Luanti running the MineClone2 game.
Your goal: get at least one {GOAL_ITEM} into the inventory.

Every turn you receive the game state and recent history. Respond by calling
exactly one tool. Briefly explain your reasoning in the message text.

How to progress in MineClone2:
- Stone needs a pickaxe. Diamond ore only drops a diamond when mined with an
  iron (or better) pickaxe; a wooden or stone pick breaks it and drops nothing.
- Tech path: logs -> mcl_core:wood planks -> mcl_core:stick and a crafting table
  -> mcl_tools:pick_wood -> cobble (mine stone) -> mcl_tools:pick_stone ->
  mine iron ore (and coal) -> craft mcl_furnaces:furnace from 8 cobble ->
  smelt mcl_raw_ores:raw_iron (what iron ore drops) into mcl_core:iron_ingot (fuel: coal lump, or
  planks) -> mcl_tools:pick_iron (3 ingots + 2 sticks).
- 1 log -> 4 planks; 2 planks -> 4 sticks; 4 planks -> table; 3 planks + 2 sticks -> wooden pick.
- Any tree species works: birch, spruce, jungle etc. logs and planks are used
  automatically wherever a recipe needs logs or planks.
- Diamond ore lies between y=-62 and y=-50. The server only sends map near the
  bot, so ore stays invisible until the bot is deep. Use mine_down (target -55)
  once you have an iron pickaxe, then mine_block "diamond".
- Iron ore is common below y=0, coal near the surface and in stone.
- The bot digs its way to blocks it cannot walk to (e.g. stone under dirt), so
  buried targets are fine to mine; dirt can be dug by hand.
- If a tool keeps failing, try a different approach instead of repeating it.
- If hostile mobs are close and hp is low, use defend.
- Block names differ between servers and games. Trust the names below and in
  the state/inventory over the MineClone2 examples above."""


def ask_model(bot, messages):
    """Call the API on a thread while the main thread keeps the bot connected."""
    box = {}

    def call():
        try:
            box["msg"] = llm.chat(messages, actions.openai_tool_specs())
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=call, daemon=True)
    t.start()
    bot.wait_for(lambda: not t.is_alive(), timeout=180)
    if "err" in box:
        raise box["err"]
    if "msg" not in box:
        raise TimeoutError("model did not answer in time")
    return box["msg"]


def main():
    ## put login details for your server here
    bot = Bot(os.environ.get("LUANTI_HOST", "127.0.0.1"),
              int(os.environ.get("LUANTI_PORT", 30000)),
              os.environ.get("LUANTI_USER", "bot"),
              os.environ.get("LUANTI_PASSWORD", ""),
              dig_through=True)       # mine a way in when a target is unreachable
    bot.connect(timeout=90)
    history = []
    try:
        bot.wait(15)                                 # let the map stream in
        print("connected at", bot.node_pos, "using model", llm.MODEL)
        block_names = names.summary(bot)
        print("block names on this server:\n" + block_names)
        system_prompt = (SYSTEM_PROMPT + "\n\nBlock names on this server "
                         "(mine_block short names cover all of them):\n" + block_names)

        for turn in range(1, MAX_TURNS + 1):
            if holding_diamond(bot):
                print(f"\n*** GOAL REACHED: holding {GOAL_ITEM} after {turn-1} turns ***")
                bot.chat("found a diamond!")
                bot.wait(1)
                return
            if bot.dead:
                bot.wait_for(lambda: not bot.dead, timeout=30)
                bot.wait(3)

            snapshot = state.observe(bot)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content":
                    f"Turn {turn}/{MAX_TURNS}.\nRecent history (oldest first):\n"
                    + ("\n".join(history[-HISTORY:]) or "(none yet)")
                    + "\n\nCurrent state:\n" + json.dumps(snapshot)},
            ]
            try:
                msg = ask_model(bot, messages)
            except Exception as e:
                print(f"[{turn}] model call failed: {e}")
                bot.wait(5)
                continue

            name, args, reasoning = llm.parse_choice(msg)
            if reasoning:
                print(f"\n[{turn}] thinking: {reasoning[:300]}")
            if name is None:
                history.append(f"turn {turn}: (no tool chosen - you must call a tool)")
                continue

            print(f"[{turn}] -> {name}({args})")
            result = actions.run(bot, name, args)
            print(f"[{turn}] <- {result}")
            history.append(f"turn {turn}: {name}({json.dumps(args)}) -> {result}")

        print("\nturn budget used up without a diamond")
    finally:
        print("final inventory:", dict(bot.item_counts()))
        bot.disconnect()


if __name__ == "__main__":
    main()
