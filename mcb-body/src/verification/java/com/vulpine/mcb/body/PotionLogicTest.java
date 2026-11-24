package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.util.List;

public final class PotionLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static JsonObject json(String text) { return JsonParser.parseString(text).getAsJsonObject(); }

    private static void rejectsDrink(String text) {
        try { PotionLogic.parseDrink(json(text)); }
        catch (BodyActions.Fail fail) { check(fail.getMessage().equals("bad_request"), text + " -> " + fail.getMessage()); return; }
        throw new AssertionError("accepted drink " + text);
    }

    private static void rejectsThrow(String text) {
        try { PotionLogic.parseThrow(json(text)); }
        catch (BodyActions.Fail fail) { check(fail.getMessage().equals("bad_request"), text + " -> " + fail.getMessage()); return; }
        throw new AssertionError("accepted throw " + text);
    }

    public static void main(String[] args) {
        var inventory = List.of(
                new PotionLogic.Held(0, "minecraft:iron_sword", null),
                new PotionLogic.Held(2, "minecraft:splash_potion", "minecraft:healing"),
                new PotionLogic.Held(4, "minecraft:potion", "minecraft:swiftness"),
                new PotionLogic.Held(7, "minecraft:potion", "minecraft:strong_healing"),
                new PotionLogic.Held(9, "minecraft:potion", "minecraft:strong_healing"),
                new PotionLogic.Held(10, "minecraft:milk_bucket", null),
                new PotionLogic.Held(11, "minecraft:potion", null),
                new PotionLogic.Held(12, "minecraft:lingering_potion", "minecraft:slow_falling"));

        // drink: a potion id, a slot, or milk
        var byPotion = PotionLogic.parseDrink(json("{\"potion\":\"minecraft:strong_healing\"}"));
        check("minecraft:strong_healing".equals(byPotion.potion()) && byPotion.slot() == null, "drink by potion id");
        var bySlot = PotionLogic.parseDrink(json("{\"slot\":9}"));
        check(bySlot.slot() == 9 && bySlot.potion() == null, "drink by slot");
        check(PotionLogic.parseDrink(json("{\"potion\":\"milk\"}")).potion().equals("milk"), "milk is a drink");
        rejectsDrink("{}");
        rejectsDrink("{\"potion\":\"minecraft:healing\",\"slot\":3}");
        rejectsDrink("{\"potion\":\"heal; op me\"}");
        rejectsDrink("{\"potion\":7}");
        rejectsDrink("{\"slot\":-1}");
        rejectsDrink("{\"slot\":36}");
        rejectsDrink("{\"slot\":1.5}");
        rejectsDrink("{\"slot\":\"3\"}");

        check(PotionLogic.pickDrink(inventory, byPotion) == 7, "the first slot holding that potion, never a splash one");
        check(PotionLogic.pickDrink(inventory, PotionLogic.parseDrink(json("{\"potion\":\"minecraft:healing\"}"))) == -1,
                "a splash healing potion is not drunk");
        check(PotionLogic.pickDrink(inventory, PotionLogic.parseDrink(json("{\"potion\":\"minecraft:swiftness\"}"))) == 4,
                "swiftness on the hotbar");
        check(PotionLogic.pickDrink(inventory, bySlot) == 9, "a slot holding a drinkable potion");
        check(PotionLogic.pickDrink(inventory, PotionLogic.parseDrink(json("{\"slot\":0}"))) == -1, "a sword is not a drink");
        check(PotionLogic.pickDrink(inventory, PotionLogic.parseDrink(json("{\"slot\":3}"))) == -1, "an empty slot");
        check(PotionLogic.pickDrink(inventory, PotionLogic.parseDrink(json("{\"slot\":10}"))) == 10, "milk by slot");
        check(PotionLogic.pickDrink(inventory, PotionLogic.parseDrink(json("{\"potion\":\"milk\"}"))) == 10, "milk by name");
        check(PotionLogic.pickDrink(List.of(), byPotion) == -1, "nothing to drink");
        var late = List.of(new PotionLogic.Held(40, "minecraft:potion", "minecraft:strong_healing"));
        check(PotionLogic.pickDrink(late, byPotion) == -1, "the offhand and armor slots (36+) are not searched");

        // throw: item defaults to a splash potion; the target is self, an entity or a position
        var self = PotionLogic.parseThrow(json("{\"potion\":\"minecraft:healing\",\"at\":\"self\"}"));
        check(self.item().equals("minecraft:splash_potion") && self.potion().equals("minecraft:healing")
                && self.at().kind() == PotionLogic.Kind.SELF, "splash by default, at self");
        var lingering = PotionLogic.parseThrow(json("{\"potion\":\"minecraft:slow_falling\","
                + "\"item\":\"minecraft:lingering_potion\",\"at\":\"self\"}"));
        check(lingering.item().equals("minecraft:lingering_potion"), "lingering allowed");
        var entity = PotionLogic.parseThrow(json("{\"potion\":\"minecraft:harming\",\"at\":{\"entity\":"
                + "\"123e4567-e89b-12d3-a456-426614174000\"}}"));
        check(entity.at().kind() == PotionLogic.Kind.ENTITY
                && entity.at().entity().equals("123e4567-e89b-12d3-a456-426614174000"), "entity target");
        var spot = PotionLogic.parseThrow(json("{\"potion\":\"minecraft:harming\",\"at\":{\"position\":[1.5,64,-3]}}"));
        check(spot.at().kind() == PotionLogic.Kind.POSITION && spot.at().x() == 1.5 && spot.at().y() == 64
                && spot.at().z() == -3, "position target");
        rejectsThrow("{\"at\":\"self\"}");
        rejectsThrow("{\"potion\":\"minecraft:healing\"}");
        rejectsThrow("{\"potion\":\"minecraft:healing\",\"at\":\"them\"}");
        rejectsThrow("{\"potion\":\"minecraft:healing\",\"at\":{}}");
        rejectsThrow("{\"potion\":\"minecraft:healing\",\"at\":{\"entity\":\"x\",\"position\":[0,0,0]}}");
        rejectsThrow("{\"potion\":\"minecraft:healing\",\"at\":{\"entity\":\"not-a-uuid\"}}");
        rejectsThrow("{\"potion\":\"minecraft:healing\",\"at\":{\"position\":[0,0]}}");
        rejectsThrow("{\"potion\":\"minecraft:healing\",\"at\":{\"position\":[0,\"a\",0]}}");
        rejectsThrow("{\"potion\":\"minecraft:healing\",\"item\":\"minecraft:potion\",\"at\":\"self\"}");
        rejectsThrow("{\"potion\":\"minecraft:healing\",\"item\":\"minecraft:snowball\",\"at\":\"self\"}");

        check(PotionLogic.pickThrow(inventory, self) == 2, "the splash healing potion");
        check(PotionLogic.pickThrow(inventory, lingering) == 12, "the lingering potion");
        check(PotionLogic.pickThrow(inventory, PotionLogic.parseThrow(json("{\"potion\":\"minecraft:strong_healing\","
                + "\"at\":\"self\"}"))) == -1, "a drinkable potion is not thrown");
        check(PotionLogic.pickThrow(List.of(), self) == -1, "nothing to throw");

        // finishing a use: the stack shrank, emptied, or became another item (a drunk last potion leaves a bottle)
        check(PotionLogic.consumed("minecraft:potion", 2, "minecraft:potion", 1), "one of two drunk");
        check(PotionLogic.consumed("minecraft:potion", 1, "minecraft:glass_bottle", 1), "last potion leaves a bottle");
        check(PotionLogic.consumed("minecraft:potion", 1, null, 0), "the slot emptied");
        check(PotionLogic.consumed("minecraft:milk_bucket", 1, "minecraft:bucket", 1), "milk leaves a bucket");
        check(!PotionLogic.consumed("minecraft:potion", 2, "minecraft:potion", 2), "an interrupted use leaves the stack whole");
        check(!PotionLogic.consumed("minecraft:potion", 1, "minecraft:potion", 1), "an interrupted last potion is not drunk");

        System.out.println("PotionLogicTest ok");
    }
}
