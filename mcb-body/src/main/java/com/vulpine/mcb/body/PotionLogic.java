package com.vulpine.mcb.body;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * The drink and throw_potion ops' requests and choices (pure, no Minecraft types; checked by PotionLogicTest).
 * Which slot to use, what the request may say, and when a use has finished. Docs: BODY_ACTION_OPS.md.
 */
final class PotionLogic {
    private PotionLogic() {}

    static final String POTION = "minecraft:potion", MILK = "minecraft:milk_bucket", SPLASH = "minecraft:splash_potion",
            LINGERING = "minecraft:lingering_potion";
    /** The name a request gives milk, which has no potion id. */
    static final String MILK_NAME = "milk";
    /** The main inventory and hotbar: what the body can move into its hand (the offhand and armor are not drunk from). */
    static final int SLOTS = 36;
    private static final Pattern ID = Pattern.compile("[a-z0-9_.-]+:[a-z0-9_./-]+");
    private static final Set<String> DRINK_FIELDS = Set.of("potion", "slot"), THROW_FIELDS = Set.of("potion", "item", "at");
    private static final Set<String> AT_FIELDS = Set.of("entity", "position");
    private static final Set<String> THROWN = Set.of(SPLASH, LINGERING);
    private static final Set<String> ENVELOPE = TrialLogic.ENVELOPE;

    /** One inventory slot as the choice sees it: the item, and its potion id (null when it names none). */
    record Held(int slot, String item, String potion) {}

    /** What to drink: a potion id (or "milk"), or a slot. Exactly one is set. */
    record Drink(String potion, Integer slot) {}

    enum Kind { SELF, ENTITY, POSITION }

    /** Where to throw: at the body, at an entity by uuid, or at a point (x, y, z). */
    record Target(Kind kind, String entity, double x, double y, double z) {}

    record Throw(String potion, String item, Target at) {}

    private static BodyActions.Fail bad() { return new BodyActions.Fail("bad_request"); }

    private static void fields(JsonObject request, Set<String> allowed) {
        for (var key : request.keySet()) if (!allowed.contains(key) && !ENVELOPE.contains(key)) throw bad();
    }

    private static String text(JsonElement e) {
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) throw bad();
        return e.getAsString();
    }

    private static String potionId(JsonElement e) {
        String id = text(e);
        if (!ID.matcher(id).matches()) throw bad();
        return id;
    }

    private static double number(JsonElement e) {
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) throw bad();
        double v = e.getAsDouble();
        if (!Double.isFinite(v)) throw bad();
        return v;
    }

    static Drink parseDrink(JsonObject request) {
        fields(request, DRINK_FIELDS);
        if (request.has("potion") == request.has("slot")) throw bad();  // exactly one way to name it
        if (request.has("slot")) {
            double slot = number(request.get("slot"));
            if (slot != Math.rint(slot) || slot < 0 || slot >= SLOTS) throw bad();
            return new Drink(null, (int) slot);
        }
        String potion = text(request.get("potion"));
        if (!potion.equals(MILK_NAME) && !ID.matcher(potion).matches()) throw bad();
        return new Drink(potion, null);
    }

    static Throw parseThrow(JsonObject request) {
        fields(request, THROW_FIELDS);
        String potion = potionId(request.get("potion"));
        String item = request.has("item") ? text(request.get("item")) : SPLASH;
        if (!THROWN.contains(item)) throw bad();
        if (!request.has("at")) throw bad();
        return new Throw(potion, item, target(request.get("at")));
    }

    private static Target target(JsonElement at) {
        if (at.isJsonPrimitive()) {
            if (!"self".equals(text(at))) throw bad();
            return new Target(Kind.SELF, null, 0, 0, 0);
        }
        if (!at.isJsonObject()) throw bad();
        var o = at.getAsJsonObject();
        for (var key : o.keySet()) if (!AT_FIELDS.contains(key)) throw bad();
        if (o.has("entity") == o.has("position")) throw bad();
        if (o.has("entity")) {
            String id = text(o.get("entity"));
            try { UUID.fromString(id); } catch (IllegalArgumentException e) { throw bad(); }
            return new Target(Kind.ENTITY, id, 0, 0, 0);
        }
        var xyz = o.get("position");
        if (!xyz.isJsonArray() || xyz.getAsJsonArray().size() != 3) throw bad();
        var a = xyz.getAsJsonArray();
        return new Target(Kind.POSITION, null, number(a.get(0)), number(a.get(1)), number(a.get(2)));
    }

    /** The slot to drink from (the first that matches), or -1: a drinkable potion of that id, milk, or that slot. */
    static int pickDrink(List<Held> inventory, Drink request) {
        for (var held : inventory) {
            if (held.slot() < 0 || held.slot() >= SLOTS) continue;
            if (request.slot() != null) {
                if (held.slot() == request.slot()) return drinkable(held) ? held.slot() : -1;
                continue;
            }
            if (request.potion().equals(MILK_NAME) ? held.item().equals(MILK)
                    : held.item().equals(POTION) && request.potion().equals(held.potion())) return held.slot();
        }
        return -1;
    }

    private static boolean drinkable(Held held) { return held.item().equals(POTION) || held.item().equals(MILK); }

    /** The slot to throw from (the first that matches the item and the potion), or -1. */
    static int pickThrow(List<Held> inventory, Throw request) {
        for (var held : inventory)
            if (held.slot() >= 0 && held.slot() < SLOTS && held.item().equals(request.item())
                    && request.potion().equals(held.potion())) return held.slot();
        return -1;
    }

    /**
     * Whether a use that has stopped finished: the held stack shrank, emptied or became another item (the last
     * potion leaves a glass bottle, milk a bucket). An interrupted use leaves the stack as it was.
     */
    static boolean consumed(String itemBefore, int countBefore, String itemNow, int countNow) {
        return countNow == 0 || itemNow == null || !itemNow.equals(itemBefore) || countNow < countBefore;
    }
}
