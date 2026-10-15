"""Work out what the blocks the agent cares about are called on this server.

Different games (and versions of the same game) name things differently:
coal ore is `mcl_core:stone_with_coal` in MineClone2, `default:stone_with_coal`
in Minetest Game, `mcl_deepslate:deepslate_with_coal` deep underground, and
`something:coal_ore` elsewhere. Rather than hardcode one game, this matches
patterns against the full node list the server sends on connect
(TOCLIENT_NODEDEF), so the same agent works on any of them.
"""
import re


def _ore(kind):
    # stone_with_iron, deepslate_with_iron, iron_ore, ironore, ore_iron
    # -- but not ironblock, iron_bars, raw_iron_block, ...
    return re.compile(rf"(?:_with_{kind}|(?:^|_){kind}_?ore|^ore_{kind})$")

#: kind -> (local-name regex, group that also qualifies a node, or None)
PATTERNS = {
    "trees":          (re.compile(r"(?:tree|log|trunk)$"), "tree"),
    "stone":          (re.compile(r"^stone$"), None),
    "dirt":           (re.compile(r"^dirt$"), None),
    "sand":           (re.compile(r"^sand$"), None),
    "coal_ore":       (_ore("coal"), None),
    "iron_ore":       (_ore("iron"), None),
    "gold_ore":       (_ore("gold"), None),
    "diamond_ore":    (_ore("diamond"), None),
    "crafting_table": (re.compile(r"crafting_table|workbench"), None),
    "furnace":        (re.compile(r"^furnace(?:_active)?$"), None),
    "lava":           (re.compile(r"lava"), "lava"),
}

#: words that disqualify a "tree" match (leaves, saplings, decorations)
_NOT_WOOD = re.compile(r"leaves|sapling|leaf|fence|stair|slab|door")


def resolve(bot):
    """Return {kind: [node names on this server]}; cached on the bot."""
    cached = getattr(bot, "_agent_names", None)
    if cached is not None:
        return cached
    names = {kind: [] for kind in PATTERNS}
    for name, d in bot.world.nodedefs.by_name.items():
        local = name.split(":")[-1]
        for kind, (pattern, group) in PATTERNS.items():
            if pattern.search(local) or (group and d.groups.get(group)):
                if kind == "trees" and (_NOT_WOOD.search(local) or not d.walkable):
                    continue
                names[kind].append(name)
    for kind in names:
        names[kind].sort()
    bot._agent_names = names
    return names


def lookup(bot, block):
    """Node names for a short kind ("iron", "iron_ore", "trees") or a full id."""
    names = resolve(bot)
    for key in (block, f"{block}_ore"):
        if names.get(key):
            return names[key]
    return [block]


def summary(bot):
    """One line per kind, for the system prompt."""
    return "\n".join(f"- {kind}: {', '.join(found[:6]) or '(none on this server)'}"
                     for kind, found in resolve(bot).items())
