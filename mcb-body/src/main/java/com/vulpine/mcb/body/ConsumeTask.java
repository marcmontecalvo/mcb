package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.BiConsumer;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.ItemStack;

/**
 * Holding the use button on something to consume it (the eat and drink ops): starts the use with
 * ServerPlayerGameMode.useItem, then each tick waits for the game to finish it. It is done when the held stack
 * shrank, emptied or became another item (the last potion leaves a glass bottle); a use that stops with the stack
 * whole was interrupted. What each op reports besides the item and the ticks is its own (the report callback).
 */
final class ConsumeTask implements BodyEntity.Task {
    /** Milk and potions take 32 ticks and food about as long; longer than this the use is stuck. */
    static final int TIMEOUT_TICKS = 200;

    private final String name;
    private final long sequence;
    private final String item;
    private final int countBefore;
    private final BiConsumer<ServerPlayer, Map<String, Object>> report;
    private int ticks;

    private ConsumeTask(String name, long sequence, String item, int countBefore,
                        BiConsumer<ServerPlayer, Map<String, Object>> report) {
        this.name = name; this.sequence = sequence; this.item = item; this.countBefore = countBefore; this.report = report;
    }

    private static String itemId(ItemStack stack) {
        return stack.isEmpty() ? null : BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
    }

    /** Starts using the held stack; interrupted when the game does not begin a use. */
    private static ConsumeTask use(String name, ServerPlayer player, long sequence,
                                   BiConsumer<ServerPlayer, Map<String, Object>> report) {
        var stack = player.getMainHandItem();
        var result = player.gameMode.useItem(player, player.level(), stack, InteractionHand.MAIN_HAND);
        if (!result.consumesAction() || !player.isUsingItem()) throw new BodyActions.Fail("interrupted");
        return new ConsumeTask(name, sequence, itemId(stack), stack.getCount(), report);
    }

    /** eat: the named food, else the most nourishing in the inventory. */
    static ConsumeTask eat(ServerPlayer player, JsonObject request, long sequence) {
        var foods = new LinkedHashMap<String, Integer>();
        var inventory = player.getInventory();
        for (int i = 0; i < 36; i++) {
            var food = inventory.getItem(i).get(DataComponents.FOOD);
            if (food != null) foods.put(itemId(inventory.getItem(i)), food.nutrition());
        }
        String item = request.has("item") ? request.get("item").getAsString() : ActionLogic.bestFood(foods);
        if (item == null || !foods.containsKey(item)) throw new BodyActions.Fail("no_food");
        BodyBuild.selectItem(player, item);
        var stack = player.getMainHandItem();
        if (!player.canEat(stack.get(DataComponents.FOOD).canAlwaysEat())) throw new BodyActions.Fail("not_hungry");
        int before = player.getFoodData().getFoodLevel();
        return use("eat", player, sequence, (p, out) -> {
            out.put("item", item);
            out.put("food_before", before);
            out.put("food_after", p.getFoodData().getFoodLevel());
            out.put("saturation_after", p.getFoodData().getSaturationLevel());
        });
    }

    /** drink: the first potion (or milk) PotionLogic picks, moved into the hand like eat does for food. */
    static ConsumeTask drink(ServerPlayer player, JsonObject request, long sequence) {
        var drink = PotionLogic.parseDrink(request);
        int slot = PotionLogic.pickDrink(BodyPotion.held(player), drink);
        if (slot < 0) throw new BodyActions.Fail("no_potion");
        BodyBuild.selectSlot(player, slot);
        var stack = player.getMainHandItem();
        String item = itemId(stack);
        String potion = item.equals(PotionLogic.MILK) ? PotionLogic.MILK_NAME : BodyPotion.potionId(stack);
        float before = player.getHealth();
        return use("drink", player, sequence, (p, out) -> {
            out.put("item", item);
            if (potion != null) out.put("potion", potion);
            out.put("health_before", before);
            out.put("health_after", p.getHealth());
            out.put("effects", BodyPotion.activeEffects(p));
        });
    }

    @Override public String name() { return name; }
    @Override public long requestSequence() { return sequence; }

    @Override public String step(ServerPlayer player) {
        ticks++;
        if (player.isUsingItem()) return ticks > TIMEOUT_TICKS ? "interrupted" : null;
        var held = player.getMainHandItem();
        return PotionLogic.consumed(item, countBefore, itemId(held), held.getCount()) ? "done" : "interrupted";
    }

    @Override public void stop(ServerPlayer player) { if (player.isUsingItem()) player.stopUsingItem(); }

    @Override public Map<String, Object> result(ServerPlayer player) {
        var out = new LinkedHashMap<String, Object>();
        report.accept(player, out);
        out.put("ticks", ticks);
        return out;
    }
}
