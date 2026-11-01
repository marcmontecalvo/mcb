package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/** Pure placement logic (no Minecraft types): state strings, yaw snapping, hit points, state matching. */
final class PlacementLogic {
    private PlacementLogic() {}

    /** Properties the game recomputes or the builder ignores (see docs/BODY_ACTION_OPS.md). */
    static final Set<String> IGNORED = Set.of("shape", "north", "east", "south", "west", "up", "down",
            "waterlogged", "powered", "power", "distance", "persistent", "open");

    record ParsedState(String block, Map<String, String> properties) {}

    /** "minecraft:oak_stairs[facing=north,half=bottom]" or a bare id. */
    static ParsedState parse(String text) {
        int open = text.indexOf('[');
        String block = open < 0 ? text : text.substring(0, open);
        if (block.isBlank() || (open >= 0 && !text.endsWith("]"))) throw new IllegalArgumentException("invalid_state");
        if (!block.contains(":")) block = "minecraft:" + block;
        var props = new TreeMap<String, String>();
        if (open >= 0) {
            String body = text.substring(open + 1, text.length() - 1);
            if (!body.isBlank()) for (String pair : body.split(",")) {
                int eq = pair.indexOf('=');
                if (eq <= 0 || eq == pair.length() - 1) throw new IllegalArgumentException("invalid_state");
                props.put(pair.substring(0, eq).trim(), pair.substring(eq + 1).trim());
            }
        }
        return new ParsedState(block, props);
    }

    static String render(ParsedState state) {
        if (state.properties().isEmpty()) return state.block();
        var parts = new ArrayList<String>();
        new TreeMap<>(state.properties()).forEach((k, v) -> parts.add(k + "=" + v));
        return state.block() + "[" + String.join(",", parts) + "]";
    }

    /** Same block, and every non-ignored property named by the target agrees (unnamed ones are free). */
    static boolean matches(ParsedState target, ParsedState placed) {
        if (!target.block().equals(placed.block())) return false;
        for (var entry : target.properties().entrySet()) {
            if (IGNORED.contains(entry.getKey())) continue;
            if (!entry.getValue().equals(placed.properties().get(entry.getKey()))) return false;
        }
        return true;
    }

    /** Minecraft yaw that looks along a horizontal direction (south = +z = 0). */
    static float yawFor(String direction) {
        return switch (direction) {
            case "south" -> 0f;
            case "west" -> 90f;
            case "north" -> 180f;
            case "east" -> -90f;
            default -> throw new IllegalArgumentException("not_horizontal:" + direction);
        };
    }

    private static final List<String> HORIZONTAL = List.of("north", "east", "south", "west");

    static String opposite(String direction) {
        return switch (direction) {
            case "north" -> "south";
            case "south" -> "north";
            case "east" -> "west";
            case "west" -> "east";
            case "up" -> "down";
            case "down" -> "up";
            default -> throw new IllegalArgumentException("direction:" + direction);
        };
    }

    /** All four horizontal directions, the one a facing property names (and its opposite) first. */
    static List<String> yawOrder(ParsedState target) {
        var out = new ArrayList<String>();
        String facing = target.properties().get("facing");
        if (facing != null && HORIZONTAL.contains(facing)) {
            out.add(facing);
            out.add(opposite(facing));
        }
        for (String d : HORIZONTAL) if (!out.contains(d)) out.add(d);
        return out;
    }

    /** Offsets (u, v) in blocks from a face centre, centre first, then the quarter points. */
    static final double[][] HIT_OFFSETS = {
            {0, 0}, {0, 0.25}, {0, -0.25}, {0.25, 0}, {-0.25, 0},
            {0.25, 0.25}, {-0.25, 0.25}, {0.25, -0.25}, {-0.25, -0.25}};

    /** Vector from a block's centre to a point on the given face: centre of the face plus (u, v). */
    static double[] facePoint(String face, double u, double v) {
        return switch (face) {
            case "up" -> new double[] {u, 0.5, v};
            case "down" -> new double[] {u, -0.5, v};
            case "north" -> new double[] {u, v, -0.5};
            case "south" -> new double[] {u, v, 0.5};
            case "east" -> new double[] {0.5, v, u};
            case "west" -> new double[] {-0.5, v, u};
            default -> throw new IllegalArgumentException("direction:" + face);
        };
    }

    /** Pitch (degrees, positive looks down) toward an offset from the eye. */
    static float pitchTo(double dx, double dy, double dz) {
        return (float) -Math.toDegrees(Math.atan2(dy, Math.sqrt(dx * dx + dz * dz)));
    }
}
