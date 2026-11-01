package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.entity.BarrelBlockEntity;
import net.minecraft.world.level.block.entity.ChestBlockEntity;
import net.minecraft.world.level.block.entity.ShulkerBoxBlockEntity;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** container op: chest, barrel and shulker box deposit / withdraw / peek through legal menu clicks. */
final class BodyContainer {
    private BodyContainer() {}

    private static final int PLAYER_SLOTS = 36; // the last 36 menu slots are the player's inventory

    static Map<String, Object> run(ServerPlayer player, JsonObject request) {
        BlockPos pos = BodyBuild.pos(request);
        String action = request.has("action") ? request.get("action").getAsString() : "";
        if (!List.of("deposit", "withdraw", "peek").contains(action)) throw new BodyActions.Fail("invalid_action");
        var level = player.level();
        var entity = level.getBlockEntity(pos);
        if (!(entity instanceof ChestBlockEntity || entity instanceof BarrelBlockEntity || entity instanceof ShulkerBoxBlockEntity))
            throw new BodyActions.Fail("not_a_container");
        var requested = requested(request);
        BodyBuild.checkPermission(player, pos);
        var hit = BodyBuild.nearestHit(player, pos); // out_of_reach / no_line_of_sight
        boolean sneaking = player.isShiftKeyDown();
        player.setShiftKeyDown(false);
        try {
            level.getBlockState(pos).useWithoutItem(level, player, hit);
        } finally { player.setShiftKeyDown(sneaking); }
        var menu = player.containerMenu;
        if (menu == player.inventoryMenu) throw new BodyActions.Fail("cannot_open");
        var moved = new ArrayList<Map<String, Object>>();
        try {
            int boxSlots = menu.slots.size() - PLAYER_SLOTS;
            if (action.equals("deposit")) {
                if (requested == null) requested = defaultDeposit(player, menu, boxSlots);
                for (var stack : requested) moved.add(transfer(player, menu, boxSlots, stack, true));
            } else if (action.equals("withdraw")) {
                if (requested == null) throw new BodyActions.Fail("missing_item");
                for (var stack : requested) moved.add(transfer(player, menu, boxSlots, stack, false));
            }
            var out = new LinkedHashMap<String, Object>();
            out.put("pos", List.of(pos.getX(), pos.getY(), pos.getZ()));
            out.put("moved", moved.stream().filter(m -> (int) m.get("count") > 0).toList());
            out.put("contents", contents(menu, boxSlots));
            return out;
        } finally { player.closeContainer(); }
    }

    private static List<ActionLogic.Stack> requested(JsonObject request) {
        if (!request.has("items")) return null;
        var out = new ArrayList<ActionLogic.Stack>();
        for (var element : request.getAsJsonArray("items")) {
            var entry = element.getAsJsonObject();
            String item = entry.get("item").getAsString();
            int count = entry.get("count").getAsInt();
            var id = Identifier.tryParse(item);
            if (id == null || !BuiltInRegistries.ITEM.containsKey(id)) throw new BodyActions.Fail("unknown_item");
            if (count < 1 || count > 2304) throw new BodyActions.Fail("invalid_count");
            out.add(new ActionLogic.Stack(item, count));
        }
        return out;
    }

    private static String itemId(ItemStack stack) { return BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(); }

    /** Everything except tools, food and the first hotbar slots, as whole-inventory totals. */
    private static List<ActionLogic.Stack> defaultDeposit(ServerPlayer player, AbstractContainerMenu menu, int boxSlots) {
        var slots = new ArrayList<ActionLogic.Slot>();
        var inv = player.getInventory();
        for (int i = 0; i < PLAYER_SLOTS; i++) {
            var s = inv.getItem(i);
            if (s.isEmpty()) continue;
            boolean keep = s.isDamageableItem() || s.has(DataComponents.FOOD) || s.has(DataComponents.TOOL);
            slots.add(new ActionLogic.Slot(i, itemId(s), s.getCount(), keep));
        }
        var out = new ArrayList<ActionLogic.Stack>();
        for (var e : ActionLogic.totals(ActionLogic.defaultDeposit(slots).stream()
                .map(s -> new ActionLogic.Stack(s.item(), s.count())).toList()).entrySet())
            out.add(new ActionLogic.Stack(e.getKey(), e.getValue()));
        return out;
    }

    private static int total(AbstractContainerMenu menu, int from, int to, String item) {
        int n = 0;
        for (int i = from; i < to; i++) { var s = menu.getSlot(i).getItem(); if (!s.isEmpty() && itemId(s).equals(item)) n += s.getCount(); }
        return n;
    }

    /** Moves up to stack.count of an item between the box and the player's slots; fails when it cannot all move. */
    private static Map<String, Object> transfer(ServerPlayer player, AbstractContainerMenu menu, int boxSlots,
                                                ActionLogic.Stack stack, boolean deposit) {
        int total = menu.slots.size();
        int fromLo = deposit ? boxSlots : 0, fromHi = deposit ? total : boxSlots;
        int toLo = deposit ? 0 : boxSlots, toHi = deposit ? boxSlots : total;
        if (total(menu, fromLo, fromHi, stack.item()) < stack.count()) throw new BodyActions.Fail("missing_item");
        int before = total(menu, toLo, toHi, stack.item());
        int remaining = stack.count();
        for (int i = fromLo; i < fromHi && remaining > 0; i++) {
            var source = menu.getSlot(i).getItem();
            if (source.isEmpty() || !itemId(source).equals(stack.item())) continue;
            int take = Math.min(remaining, source.getCount());
            if (take == source.getCount()) {
                menu.clicked(i, 0, ContainerInput.QUICK_MOVE, player);
            } else {
                menu.clicked(i, 0, ContainerInput.PICKUP, player);
                for (int k = 0; k < take; k++) {
                    int target = targetSlot(menu, toLo, toHi, menu.getCarried());
                    if (target < 0) break;
                    menu.clicked(target, 1, ContainerInput.PICKUP, player); // one item
                }
                menu.clicked(i, 0, ContainerInput.PICKUP, player); // the rest goes back
                if (!menu.getCarried().isEmpty()) throw new BodyActions.Fail("could_not_return_stack");
            }
            int arrived = total(menu, toLo, toHi, stack.item()) - before;
            remaining = stack.count() - arrived;
        }
        int arrived = total(menu, toLo, toHi, stack.item()) - before;
        if (arrived < stack.count()) {
            if (arrived > 0) { /* partial moves stay moved; the failure reports the shortfall */ }
            throw new BodyActions.Fail(deposit ? "container_full" : "inventory_full");
        }
        var out = new LinkedHashMap<String, Object>();
        out.put("item", stack.item());
        out.put("count", arrived);
        return out;
    }

    private static int targetSlot(AbstractContainerMenu menu, int lo, int hi, ItemStack carried) {
        int empty = -1;
        for (int i = lo; i < hi; i++) {
            var s = menu.getSlot(i).getItem();
            if (!menu.getSlot(i).mayPlace(carried)) continue;
            if (s.isEmpty()) { if (empty < 0) empty = i; }
            else if (ItemStack.isSameItemSameComponents(s, carried) && s.getCount() < s.getMaxStackSize()) return i;
        }
        return empty;
    }

    private static List<Map<String, Object>> contents(AbstractContainerMenu menu, int boxSlots) {
        var out = new ArrayList<Map<String, Object>>();
        for (int i = 0; i < boxSlots; i++) {
            var s = menu.getSlot(i).getItem();
            if (s.isEmpty()) continue;
            var entry = new LinkedHashMap<String, Object>();
            entry.put("slot", i);
            entry.put("item", itemId(s));
            entry.put("count", s.getCount());
            out.add(entry);
        }
        return out;
    }
}
