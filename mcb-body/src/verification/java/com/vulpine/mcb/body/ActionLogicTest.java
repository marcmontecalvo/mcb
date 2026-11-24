package com.vulpine.mcb.body;

import java.util.List;
import java.util.Map;

public final class ActionLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    public static void main(String[] args) {
        check(ActionLogic.allowedTarget(true, false, false, false, false), "cow ok");
        // a vex is Ownable (its evoker), not a pet: the vault raid's fight op refused one (20261124T043026Z)
        check(!ActionLogic.pet(true, true), "an owned monster is no pet");
        check(ActionLogic.pet(true, false), "an owned animal is a pet");
        check(!ActionLogic.pet(false, false), "no owner, no pet");
        check(!ActionLogic.allowedTarget(true, false, true, false, false), "tamed refused");
        check(!ActionLogic.allowedTarget(false, false, false, false, true), "villager refused");
        check(!ActionLogic.allowedTarget(true, false, false, true, false), "player refused");
        check(ActionLogic.allowedTarget(false, true, false, false, false), "zombie ok");
        check(!ActionLogic.allowedTarget(false, true, false, true, false), "hostile player refused");
        check(!ActionLogic.allowedTarget(false, true, false, false, true), "hostile villager refused");
        check(!ActionLogic.allowedTarget(false, true, true, false, false), "tamed hostile refused");
        check(!ActionLogic.allowedTarget(false, false, false, false, false), "neither animal nor hostile refused");
        // a commanded attack (the owner's /mcb attack) may name a player; the five-argument form never does
        check(ActionLogic.allowedTarget(false, false, false, true, false, true, false), "commanded player ok");
        check(!ActionLogic.allowedTarget(false, false, false, true, false, false, false), "player refused unless commanded");
        check(!ActionLogic.allowedTarget(false, false, false, true, false, true, true), "never the body itself");
        check(!ActionLogic.allowedTarget(false, true, false, false, true, true, false), "villager refused even when commanded");
        check(ActionLogic.allowedTarget(false, true, false, false, false, true, false), "zombie ok when commanded");
        check(!ActionLogic.allowedTarget(false, false, false, false, false, true, false), "commanded does not widen the mob kinds");
        check(!ActionLogic.allowedTarget(true, false, true, false, false, true, false), "tamed refused even when commanded");
        // what a commanded player target ends with
        check(ActionLogic.refusal(false, false, false, true, false, true, false, true) == null, "hurtable player fought");
        check("invulnerable".equals(ActionLogic.refusal(false, false, false, true, false, true, false, false)),
                "creative, spectator or invulnerable player ends invulnerable");
        check("not_allowed".equals(ActionLogic.refusal(false, false, false, true, false, false, false, true)), "player not commanded");
        check("not_allowed".equals(ActionLogic.refusal(false, false, false, true, false, true, true, true)), "self");
        check(ActionLogic.refusal(true, false, false, false, false, false, false, false) == null, "hurtable is only a player's question");
        check(ActionLogic.hurtable(false, false, false) && !ActionLogic.hurtable(true, false, false)
                && !ActionLogic.hurtable(false, true, false) && !ActionLogic.hurtable(false, false, true), "hurtable");
        check(!ActionLogic.shouldSwing(0.5f) && ActionLogic.shouldSwing(1f), "cooldown");
        check(ActionLogic.bestFood(Map.of("bread", 5)).equals("bread") && ActionLogic.bestFood(Map.of()) == null, "food");
        var inv = List.of(new ActionLogic.Slot(0, "a", 1, true), new ActionLogic.Slot(3, "log", 64, false),
                new ActionLogic.Slot(5, "beef", 2, true), new ActionLogic.Slot(9, "dirt", 3, false));
        check(ActionLogic.defaultDeposit(inv).size() == 2, "deposit skips tools, food, hotbar head");
        var have = List.of(new ActionLogic.Stack("log", 64), new ActionLogic.Stack("log", 10));
        check(ActionLogic.firstMissing(List.of(new ActionLogic.Stack("log", 74)), have) == null, "enough");
        check("log".equals(ActionLogic.firstMissing(List.of(new ActionLogic.Stack("log", 75)), have)), "missing");
        System.out.println("ActionLogicTest ok");
    }
}
