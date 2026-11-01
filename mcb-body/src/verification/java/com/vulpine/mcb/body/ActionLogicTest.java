package com.vulpine.mcb.body;

import java.util.List;
import java.util.Map;

public final class ActionLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    public static void main(String[] args) {
        check(ActionLogic.allowedTarget(true, false, false, false), "cow ok");
        check(!ActionLogic.allowedTarget(true, true, false, false), "tamed refused");
        check(!ActionLogic.allowedTarget(false, false, false, true), "villager refused");
        check(!ActionLogic.allowedTarget(true, false, true, false), "player refused");
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
