"""The tools an AI agent can choose between.

Each tool is a predetermined series of in-game actions, the same kind of thing
the scripts in examples/ do, wrapped so a language model can pick one by name.
A tool takes the bot plus keyword arguments and returns a short text report of
what happened. That report goes back to the model so it can choose the next
tool.

Tools never raise for ordinary in-game failure (no trees nearby, path blocked,
and so on); they report it instead, so the agent can try something else.
"""
import math
import time

from luantibot import BotError, recipes
from bot_tools import names

#: items the recipe book crafts and places (MineClone2 / VoxeLibre ids)
TABLE = "mcl_crafting_table:crafting_table"
FURNACE = "mcl_furnaces:furnace"

#: what `mine_block` accepts as a short name; each resolves to every matching
#: node on the server (so "iron" covers stone_with_iron, deepslate_with_iron, ...)
SHORT_NAMES = ("stone", "coal", "iron", "gold", "diamond", "sand", "dirt", "trees")


# ---------------------------------------------------------------- helpers

def _near(bot, name, radius=6):
    """Nearest node of a type that is already within a short walk."""
    hits = bot.find_blocks(name, radius=radius)
    return hits[0] if hits else None


def _side_spots(bot):
    """The four nodes beside the bot at foot level, sorted best first.

    Each entry is (spot, needs_digging). A spot is usable when it has solid
    floor under it; it is ready now when it and the node above it are empty
    (flat, open ground), and needs digging when a wall or slope fills it.
    """
    x, y, z = bot.node_pos
    ready, dig = [], []
    for dx, dz in bot.HEADINGS:
        spot = (x + dx, y, z + dz)
        if not bot.world.is_walkable((spot[0], y - 1, spot[2])):
            continue                     # no floor: a drop or unloaded map
        if bot.world.is_free(spot) and bot.world.is_free((spot[0], y + 1, spot[2])):
            ready.append((spot, False))
        else:
            dig.append((spot, True))
    return ready + dig


def _place_from_inventory(bot, item):
    """Put a block from the inventory down beside the bot; return its position.

    Prefers an open spot on flat ground at the bot's own level. If the ground
    around the bot is not flat (in a hole, against a hillside), it digs one of
    the side spots out first, so placement never depends on uneven terrain.
    """
    if bot.inventory.count(item) == 0:
        return None
    bot.settle(timeout=5.0)
    for spot, needs_digging in _side_spots(bot):
        above = (spot[0], spot[1] + 1, spot[2])
        # clearing the head-height node too keeps the spot reachable and
        # stops gravel or sand from falling in on top
        if needs_digging and not bot._clear([above, spot]):
            continue
        try:
            bot.place_block(spot, item=item)
        except BotError:
            continue
        bot.wait(1)
        if bot.block_at(spot) == item:
            return spot
    # nothing workable right here: fall back to searching further afield
    spot = bot.find_placement_spot()
    if spot is None:
        return None
    bot.place_block(spot, item=item)
    bot.wait(1)
    return spot if bot.block_at(spot) == item else None


#: recipe-book ingredients that really mean "any item in this group" -- the
#: game's own recipes say group:tree / group:wood, so any species will do
ANY_OF_GROUP = {recipes.LOG: ("tree", "logs"), recipes.WOOD: ("wood", "planks")}


def _groups(bot, item):
    """An item's groups, from the server's item definitions (or node ones)."""
    defs = bot.client.itemdefs
    d = defs.get(item) if defs else None
    if d is None or not d.groups:
        d = bot.world.nodedefs.by_name.get(item)
    return d.groups if d is not None else {}


def _substitute(bot, rows):
    """Fill generic log/plank cells with whatever species the bot carries.

    Sticks to one species while it has enough (so planks come out uniform),
    and mixes species only when no single one covers the recipe.
    """
    pools = {}
    out = []
    for row in rows:
        new_row = []
        for cell in row:
            if cell not in ANY_OF_GROUP:
                new_row.append(cell)
                continue
            group, label = ANY_OF_GROUP[cell]
            if group not in pools:
                have = {n: c for n, c in bot.item_counts().items()
                        if _groups(bot, n).get(group)}
                pools[group] = dict(sorted(have.items(), key=lambda kv: -kv[1]))
            pool = pools[group]
            pick = next((n for n, c in pool.items() if c > 0), None)
            if pick is None:
                need = sum(r.count(cell) for r in rows)
                raise BotError(f"need {need} {label} (any kind), have "
                               f"{sum(bot.item_counts()[n] for n in pools[group]) or 0}")
            pool[pick] -= 1
            new_row.append(pick)
        out.append(new_row)
    return out


def _craft(bot, item, times=1):
    """Craft from the recipe book with any species of log/planks.

    Returns {item name: count made}; planks come out as whatever species the
    logs were (birch logs -> birch planks).
    """
    rows, _needs_table = recipes.get(item)
    made = {}
    for _ in range(times):
        result = bot.craft(_substitute(bot, rows))
        if result is None:
            break
        made[result.name] = made.get(result.name, 0) + result.count
    return made


def _ensure_table(bot):
    """Open a crafting table, placing or crafting one first if needed.

    Returns (position, placed): `placed` is True when the bot put the table
    down itself, so the caller knows to take it back afterwards.
    """
    pos = _near(bot, names.resolve(bot)["crafting_table"] or [TABLE])
    placed = False
    if pos is None:
        if bot.inventory.count(TABLE) == 0:
            _craft(bot, TABLE)
        pos = _place_from_inventory(bot, TABLE)
        if pos is None:
            raise BotError("could not place a crafting table")
        placed = True
    if bot.open_node(pos) < 9:
        if placed:
            _pick_up(bot, pos)
        raise BotError(f"crafting table at {pos} did not open")
    return pos, placed


def _pick_up(bot, pos):
    """Break a block the bot placed and collect it; True if it came back."""
    item = bot.block_at(pos)
    before = bot.inventory.count(item)
    bot.clear_craft_grid()               # nothing may be left in a 3x3 grid
    try:
        if not bot.dig(pos, timeout=15):
            return False
    except BotError:
        return False
    _collect_drop(bot, pos)
    return bot.inventory.count(item) > before


# ------------------------------------------------------------------ tools

def gather_wood(bot, count=4):
    """Chop trees by hand until carrying `count` logs (any species).

    Each tree is felled from the bottom: walk (or dig) up beside the lowest
    log, cut the two at foot and head height, step into the gap, and chop the
    rest of the trunk overhead so the drops fall onto the bot.
    """
    count = max(1, min(int(count), 16))
    trees = names.resolve(bot)["trees"] or list(recipes.LOGS)
    carrying = lambda: sum(bot.inventory.count(l) for l in trees)
    start = have = carrying()
    skipped = set()
    for _attempt in range(8):
        if have >= count:
            break
        log = next((p for p in bot.scan(trees, limit=60) if p not in skipped), None)
        if log is None:
            break
        base, top = _trunk(bot, log, trees)
        try:
            felled = _fell(bot, base, top, trees, want=count - have)
        except BotError:
            felled = 0
        if not felled:
            skipped.update((base[0], y, base[2]) for y in range(base[1], top + 1))
        have = carrying()
    got = have - start
    if have >= count:
        return f"ok: gathered {got} logs, now carrying {have}"
    return f"partial: gathered {got} logs, carrying {have}; no more reachable trees"


def _trunk(bot, log, trees):
    """The lowest log of the trunk `log` belongs to, and the trunk's top y."""
    x, y, z = log
    while bot.block_at((x, y - 1, z)) in trees:
        y -= 1
    top = log[1]
    while bot.block_at((x, top + 1, z)) in trees:
        top += 1
    return (x, y, z), top


def _fell(bot, base, top, trees, want):
    """Chop one trunk from the bottom up; returns how many logs were cut."""
    if not _stand_next_to(bot, base):
        return 0
    x, y, z = base
    cut = 0
    # the bottom two are at foot and head height from beside the trunk
    for p in ((x, y, z), (x, y + 1, z)):
        if bot.block_at(p) in trees and bot.dig(p, timeout=30, approach=False):
            cut += 1
    if not cut:
        return 0
    # stand in the gap: the rest of the trunk is straight overhead, and
    # everything chopped from there falls onto the bot
    if bot.pathfinder.standable(base) and bot.goto(base, tolerance=0, timeout=10, dig=False):
        for h in range(y + 2, top + 1):
            p = (x, h, z)
            if cut >= want or not bot.in_reach(p):
                break
            if bot.block_at(p) in trees and bot.dig(p, timeout=30, approach=False):
                cut += 1
        bot.wait(2)                      # let the last drops land and be picked up
    else:
        _collect_drop(bot, base)
    return cut


def craft(bot, item, times=1):
    """Craft a known item, using (and if needed placing) a crafting table.

    Logs and planks of any tree species are used interchangeably.
    """
    # "planks", or a specific species' planks, all mean the planks recipe
    if item not in recipes.RECIPES and (item.split(":")[-1] in ("planks", "wood")
                                        or _groups(bot, item).get("wood")):
        item = recipes.WOOD
    entry = recipes.get(item)
    if entry is None:
        return f"failed: no recipe for {item}. Known: {', '.join(recipes.RECIPES)}"
    _rows, needs_table = entry
    bot.clear_craft_grid()
    table, placed = _ensure_table(bot) if needs_table else (None, False)
    try:
        made = _craft(bot, item, max(1, min(int(times), 16)))
    finally:
        # take back a table we put down, so it travels with us to the next craft
        picked_up = placed and _pick_up(bot, table)
    note = "; picked the crafting table back up" if picked_up else (
        f"; crafting table left at {table}" if placed else "")
    if not made:
        return f"failed: crafting {item} produced nothing (missing ingredients?){note}"
    return "ok: crafted " + ", ".join(f"{n} x {name}" for name, n in made.items()) + note


def mine_block(bot, block, count=1):
    """Find the nearest blocks of a type on the loaded map and dig them."""
    wanted = names.lookup(bot, block)
    name = block if len(wanted) > 1 else wanted[0]
    count = max(1, min(int(count), 16))
    if not bot.scan(wanted, limit=1):
        return f"failed: no {name} visible on the loaded map ({', '.join(wanted[:4])})"
    before = dict(bot.item_counts())
    dug = 0
    skipped = set()
    attempts = 0
    while dug < count and attempts < count + 5:
        # rescan each time: after moving, a different block may be nearest
        target = next((p for p in bot.scan(wanted, limit=40) if p not in skipped), None)
        if target is None:
            break
        attempts += 1
        if not bot.can_dig(target):
            return f"failed: no tool good enough to dig {name}"
        try:
            if not _stand_next_to(bot, target):
                skipped.add(target)
                continue
            if bot.block_at(target) not in wanted:
                continue                 # cleared on the way in (e.g. by gravity)
            if bot.dig(target, timeout=60, approach=False):
                dug += 1
                _collect_drop(bot, target)
            else:
                skipped.add(target)
        except BotError:
            skipped.add(target)
    gained = {k: v - before.get(k, 0) for k, v in bot.item_counts().items()
              if v > before.get(k, 0)}
    return f"{'ok' if dug else 'failed'}: dug {dug} x {name}, picked up {gained or 'nothing'}"


def _beside(pos):
    """Places to stand right next to a block: level with it, or one lower so
    it sits at head height."""
    x, y, z = pos
    return [(x + dx, y + dy, z + dz) for dy in (0, -1) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))]


def _stand_next_to(bot, pos, timeout=90):
    """Get the bot standing directly beside `pos`, digging a way in if needed.

    Walks to an open spot beside the block when one is reachable. Otherwise it
    picks the nearest spot with a floor and tunnels toward it, mining whatever
    is in the way (dirt over buried stone, a wall in a cave).
    """
    spots = _beside(pos)
    if bot.node_pos in spots:
        return True
    deadline = time.monotonic() + timeout
    here = bot.node_pos
    spots.sort(key=lambda s: math.dist(s, here))

    # 1. walk to an open spot (trying a few: a near one may be walled off)
    for spot in [s for s in spots if bot.pathfinder.standable(s)][:3]:
        left = deadline - time.monotonic()
        if left < 5:
            return False
        if bot.goto(spot, tolerance=0, timeout=min(left, 40), dig=False):
            return True

    # 2. nothing open to walk to: mine a way to the nearest spot with a floor
    for spot in spots:
        if bot.world.is_walkable((spot[0], spot[1] - 1, spot[2])):
            left = deadline - time.monotonic()
            return left > 5 and bot.tunnel_to(spot, lambda: bot.node_pos in spots,
                                              timeout=left)
    return False


def _collect_drop(bot, pos):
    """Step into the gap the block left so its drop is picked up."""
    x, y, z = pos
    for spot in (pos, (x, y - 1, z)):
        if bot.pathfinder.standable(spot):
            bot.goto(spot, tolerance=0, timeout=10, dig=False)
            break
    bot.wait(1.5)                        # pickup happens on the next server tick or two


def mine_down(bot, target_y):
    """Dig a staircase down to a y level (fights mobs, avoids lava and water)."""
    target_y = int(target_y)
    if target_y >= bot.node_pos[1]:
        return f"failed: already at y={bot.node_pos[1]}, which is not above {target_y}"
    reached = bot.mine_to_depth(target_y, timeout=600)
    return (f"{'ok: reached' if reached else 'stopped:'} y={bot.node_pos[1]} "
            f"(target {target_y}), hp {bot.hp}")


def explore_down(bot):
    """Walk to the deepest place reachable without digging (caves), revealing map."""
    here = bot.node_pos
    reach = bot.reachable(max_expanded=30000)
    lower = [p for p in reach if p[1] < here[1] - 1]
    if lower:
        target = min(lower, key=lambda p: (p[1], math.dist(p, here)))
    elif reach:
        target = max(reach, key=lambda p: (p[0]-here[0])**2 + (p[2]-here[2])**2)
    else:
        return "failed: nowhere reachable from here"
    bot.goto(target, tolerance=1, timeout=90)
    bot.wait(4)                          # let the newly near map stream in
    return f"moved from {here} to {bot.node_pos}"


def smelt(bot, item, fuel, count=1):
    """Smelt items in a furnace, placing one from the inventory if needed."""
    count = max(1, min(int(count), bot.inventory.count(item)))
    if bot.inventory.count(item) == 0:
        return f"failed: carrying no {item}"
    if bot.inventory.count(fuel) == 0:
        return f"failed: carrying no {fuel}"
    pos = (_near(bot, names.resolve(bot)["furnace"] or [FURNACE])
           or _place_from_inventory(bot, FURNACE))
    if pos is None:
        return "failed: no furnace nearby or in inventory (craft one from 8 cobble)"
    out = bot.smelt(pos, item, fuel, count=count, timeout=60 + 15 * count)
    return f"ok: smelted -> {out}" if out else "failed: furnace produced nothing"


def defend(bot):
    """Fight every hostile mob close to the bot."""
    killed = bot.defend(radius=8, timeout=30)
    return f"killed {killed}, hp {bot.hp}"


def goto(bot, x, y, z):
    """Pathfind and walk to a coordinate."""
    ok = bot.goto((int(x), int(y), int(z)), tolerance=1, timeout=120)
    return f"{'arrived' if ok else 'could not reach'}; now at {bot.node_pos}"


# ------------------------------------------------------------- registry

def _schema(desc, **props):
    required = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}

TOOLS = {
    "gather_wood": (gather_wood, _schema(
        "Chop nearby trees by hand until carrying `count` logs: walks (or digs) "
        "up to each trunk, fells it from the bottom and collects the logs.",
        count={"type": "integer", "description": "logs wanted (default 4)", "optional": True})),
    "craft": (craft, _schema(
        "Craft an item. Places/uses a crafting table automatically when needed. "
        "Logs and planks of any tree species work: mcl_core:wood means 'planks "
        "from whatever logs you have' (birch logs give birch planks, etc.) and "
        "every recipe that needs planks accepts any kind. Known items: "
        + ", ".join(recipes.RECIPES),
        item={"type": "string", "enum": list(recipes.RECIPES)},
        times={"type": "integer", "description": "how many crafts (default 1)", "optional": True})),
    "mine_block": (mine_block, _schema(
        "Mine the nearest visible blocks of a type: walks right up to each one "
        "(digging through anything in the way), mines it, then steps in to pick "
        "up the drop. Short names (each covers every "
        "variant on this server, e.g. stone and deepslate ores): "
        + ", ".join(SHORT_NAMES) + "; or a full node id.",
        block={"type": "string"},
        count={"type": "integer", "description": "blocks to dig (default 1)", "optional": True})),
    "mine_down": (mine_down, _schema(
        "Dig a staircase down to a y level. Slow but reliable; reveals deeper map.",
        target_y={"type": "integer"})),
    "explore_down": (explore_down, _schema(
        "Walk (no digging) to the deepest reachable spot, e.g. down into a cave.")),
    "smelt": (smelt, _schema(
        "Smelt items in a furnace (placed from inventory if none nearby).",
        item={"type": "string", "description": "exact inventory item id, e.g. the raw iron iron ore dropped"},
        fuel={"type": "string", "description": "exact inventory item id: coal, or wood planks"},
        count={"type": "integer", "optional": True})),
    "defend": (defend, _schema("Fight off nearby hostile mobs.")),
    "goto": (goto, _schema(
        "Walk to a coordinate, mining a tunnel through blocks if there is no open path.",
        x={"type": "integer"}, y={"type": "integer"}, z={"type": "integer"})),
}


def openai_tool_specs():
    """The tool list in OpenAI function-calling format."""
    return [{"type": "function", "function": {"name": n, **spec}}
            for n, (_fn, spec) in TOOLS.items()]


def run(bot, name, args):
    """Execute a tool by name and return its text report."""
    if name not in TOOLS:
        return f"failed: unknown tool {name!r}"
    fn = TOOLS[name][0]
    try:
        return fn(bot, **(args or {}))
    except TypeError as e:
        return f"failed: bad arguments for {name}: {e}"
    except BotError as e:
        return f"failed: {e}"
