package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Pure decisions of the wave-3 ops (no Minecraft types), checked by ActionLogicTest. */
final class ActionLogic {
    private ActionLogic() {}

    static final int ATTACK_TIMEOUT_TICKS = 200;
    static final float FULL_STRENGTH = 0.95f;
    static final int HOTBAR_KEPT = 3;

    record Stack(String item, int count) {}
    record Slot(int index, String item, int count, boolean toolOrFood) {}

    /** Animals and hostile mobs: not tamed, not a player and not a villager. */
    static boolean allowedTarget(boolean animal, boolean hostile, boolean tamed, boolean player, boolean villager) {
        return allowedTarget(animal, hostile, tamed, player, villager, false, false);
    }

    /**
     * As the five-argument form, for a request the owner commanded (allowPlayer, set by Python only for an
     * attack job against a named player): a player may then be the target, never the body itself. Auto-defence
     * never sets it. Villagers and tamed animals stay refused whoever asks.
     */
    static boolean allowedTarget(boolean animal, boolean hostile, boolean tamed, boolean player, boolean villager,
                                 boolean allowPlayer, boolean self) {
        if (self || villager || tamed) return false;
        if (player) return allowPlayer;
        return animal || hostile;
    }

    /** A player can be hurt unless in creative or spectator mode or invulnerable. */
    static boolean hurtable(boolean creative, boolean spectator, boolean invulnerable) {
        return !creative && !spectator && !invulnerable;
    }

    /**
     * Why a target is refused, or null: "not_allowed" (a kind the op never hits) or "invulnerable" (a commanded player
     * target that cannot be hurt; hurtable is only asked of a player).
     */
    static String refusal(boolean animal, boolean hostile, boolean tamed, boolean player, boolean villager,
                          boolean allowPlayer, boolean self, boolean hurtable) {
        if (!allowedTarget(animal, hostile, tamed, player, villager, allowPlayer, self)) return "not_allowed";
        return player && !hurtable ? "invulnerable" : null;
    }

    /** Attack only with a full cooldown. */
    static boolean shouldSwing(float strengthScale) { return strengthScale >= FULL_STRENGTH; }

    /** Best food by nutrition (first wins ties); null when the map is empty. */
    static String bestFood(Map<String, Integer> nutritionByItem) {
        String best = null;
        int bestValue = -1;
        for (var e : nutritionByItem.entrySet()) if (e.getValue() > bestValue) { best = e.getKey(); bestValue = e.getValue(); }
        return best;
    }

    /** Default deposit: every stack except tools, food and the first hotbar slots. */
    static List<Slot> defaultDeposit(List<Slot> inventory) {
        var out = new ArrayList<Slot>();
        for (var s : inventory) if (s.index() >= HOTBAR_KEPT && !s.toolOrFood() && s.count() > 0) out.add(s);
        return out;
    }

    /** Total per item of a stack list. */
    static Map<String, Integer> totals(List<Stack> stacks) {
        var out = new LinkedHashMap<String, Integer>();
        for (var s : stacks) out.merge(s.item(), s.count(), Integer::sum);
        return out;
    }

    /** First requested item whose total exceeds what is available, or null when all can be moved. */
    static String firstMissing(List<Stack> requested, List<Stack> available) {
        var have = totals(available);
        for (var e : totals(requested).entrySet()) if (have.getOrDefault(e.getKey(), 0) < e.getValue()) return e.getKey();
        return null;
    }
}
