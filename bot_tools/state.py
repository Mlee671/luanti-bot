"""A compact description of the game state, for the AI to decide on.

The model never sees raw map data -- that would be enormous. Instead it gets
the bot's vitals, its inventory, and for each block type that matters on the
road to diamonds, how many are known and where the nearest one is. Block
types are matched against this server's own node names (see names.py), so the
report works whatever the game calls them.
"""
import math

from bot_tools import names

#: kinds from names.PATTERNS worth reporting every turn
INTERESTING = ("trees", "stone", "coal_ore", "iron_ore", "diamond_ore",
               "crafting_table", "furnace", "lava")


def observe(bot):
    """Return a JSON-serialisable snapshot of what the bot knows right now."""
    here = bot.node_pos
    found = names.resolve(bot)
    nearby = {}
    for label in INTERESTING:
        if not found[label]:
            continue
        hits = bot.scan(found[label], limit=200)
        if hits:
            p = hits[0]
            nearby[label] = {"known": len(hits), "nearest": list(p),
                             "distance": round(math.dist(p, here), 1),
                             "block": bot.block_at(p)}
    return {
        "position": list(here),
        "hp": bot.hp,
        "dead": bot.dead,
        "inventory": dict(bot.item_counts()),
        "hostile_mobs_nearby": [m.name for m in bot.hostiles(radius=12)],
        "lowest_loaded_y": bot.world.lowest_loaded_y(),
        "known_blocks": nearby,
    }
