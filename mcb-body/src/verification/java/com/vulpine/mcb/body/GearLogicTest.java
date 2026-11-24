package com.vulpine.mcb.body;

import java.util.List;
import java.util.Map;
import java.util.Set;

public final class GearLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static GearLogic.Piece piece(int index, String item, GearLogic.Slot slot, double armor, double toughness,
                                         int protection, int left) {
        return new GearLogic.Piece(index, "minecraft:" + item, slot, armor, toughness, protection, left);
    }

    public static void main(String[] args) {
        var H = GearLogic.Slot.HEAD; var C = GearLogic.Slot.CHEST; var L = GearLogic.Slot.LEGS;
        var F = GearLogic.Slot.FEET; var O = GearLogic.Slot.OFFHAND;
        var ironHelmet = piece(3, "iron_helmet", H, 2, 0, 0, 165);
        var ironChest = piece(4, "iron_chestplate", C, 6, 0, 0, 240);
        var ironLegs = piece(5, "iron_leggings", L, 5, 0, 0, 225);
        var ironBoots = piece(6, "iron_boots", F, 2, 0, 0, 195);
        var shield = piece(7, "shield", O, 0, 0, 0, 336);

        // a full set and a shield carried, nothing worn: each goes on
        var moves = GearLogic.plan(List.of(ironHelmet, ironChest, ironLegs, ironBoots, shield), Map.of(), Set.of());
        check(moves.size() == 5, "five moves: " + moves);
        check(moves.contains(new GearLogic.Move(4, C)) && moves.contains(new GearLogic.Move(7, O)), "chest and shield");

        // the better piece of a slot wins: armor first, then toughness, protection, durability left
        var leather = piece(8, "leather_chestplate", C, 3, 0, 0, 80);
        var diamond = piece(9, "diamond_chestplate", C, 8, 2, 0, 528);
        check(GearLogic.plan(List.of(leather, ironChest, diamond), Map.of(), Set.of())
                .equals(List.of(new GearLogic.Move(9, C))), "the diamond chestplate");
        var enchanted = piece(10, "iron_chestplate", C, 6, 0, 2, 240);
        check(GearLogic.plan(List.of(ironChest, enchanted), Map.of(), Set.of())
                .equals(List.of(new GearLogic.Move(10, C))), "protection breaks an armor tie");
        var worn = piece(11, "iron_chestplate", C, 6, 0, 0, 30);
        check(GearLogic.better(ironChest, worn) && !GearLogic.better(worn, ironChest), "more durability left");

        // a worn piece at least as good stays on: nothing to do
        check(GearLogic.plan(List.of(leather), Map.of(C, ironChest), Set.of()).isEmpty(), "worn iron stays");
        check(GearLogic.plan(List.of(ironChest), Map.of(C, piece(40, "iron_chestplate", C, 6, 0, 0, 240)), Set.of())
                .isEmpty(), "an equal piece is no upgrade");
        check(GearLogic.plan(List.of(diamond), Map.of(C, ironChest), Set.of())
                .equals(List.of(new GearLogic.Move(9, C))), "an upgrade swaps in");

        // an off hand holding something that is not gear (a totem, a torch) is left alone
        check(GearLogic.plan(List.of(shield), Map.of(), Set.of(O)).isEmpty(), "the totem stays in the off hand");
        // a piece with no armor never goes in an armor slot (a carved pumpkin, an elytra as armor)
        check(GearLogic.plan(List.of(piece(12, "carved_pumpkin", H, 0, 0, 0, 0)), Map.of(), Set.of()).isEmpty(),
                "no armor, no move");

        check(GearLogic.slotName(O).equals("offhand") && GearLogic.slotName(H).equals("head"), "slot names");
        System.out.println("GearLogicTest ok");
    }
}
