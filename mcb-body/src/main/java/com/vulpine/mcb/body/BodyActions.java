package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.inventory.AbstractFurnaceMenu;
import net.minecraft.world.inventory.BlastFurnaceMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.CraftingMenu;
import net.minecraft.world.inventory.FurnaceMenu;
import net.minecraft.world.inventory.RecipeBookMenu;
import net.minecraft.world.inventory.SmokerMenu;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.CraftingRecipe;
import net.minecraft.world.item.crafting.RecipeType;
import net.minecraft.world.item.crafting.ShapedRecipe;
import net.minecraft.world.item.crafting.ShapelessRecipe;
import net.minecraft.world.item.crafting.SingleRecipeInput;
import net.minecraft.world.level.block.AbstractFurnaceBlock;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Legal player actions through menus: craft, furnace load/collect, equip. Nothing here creates an
 * item; stacks only move by the same menu clicks and recipe placement a real player's client triggers.
 * Everything runs synchronously on the server thread (open, act, close within one call).
 */
final class BodyActions {
    private BodyActions() {}

    /** A refused action; the message is the reason code reported in the *_failed event. */
    static final class Fail extends RuntimeException {
        Fail(String reason) { super(reason); }
    }

    private static final int MAX_CRAFTS = 256;
    private static final int PLAYER_SLOTS_FIRST = 3, PLAYER_SLOTS_LAST = 38; // furnace menu: 27 inventory + 9 hotbar

    // ---- craft
    static Map<String, Object> craft(MinecraftServer server, ServerPlayer player, JsonObject request) {
        String recipeId = request.get("recipe").getAsString();
        int times = request.get("times").getAsInt();
        if (times < 1 || times > MAX_CRAFTS) throw new Fail("invalid_times");
        var id = Identifier.tryParse(recipeId);
        var holder = id == null ? java.util.Optional.<net.minecraft.world.item.crafting.RecipeHolder<?>>empty()
                : server.getRecipeManager().byKey(ResourceKey.create(Registries.RECIPE, id));
        if (holder.isEmpty() || !(holder.get().value() instanceof CraftingRecipe recipe)) throw new Fail("unknown_recipe");
        boolean small = recipe instanceof ShapedRecipe shaped ? shaped.getWidth() <= 2 && shaped.getHeight() <= 2
                : recipe instanceof ShapelessRecipe && recipe.placementInfo().ingredients().size() <= 4;
        RecipeBookMenu menu;
        if (small) {
            menu = player.inventoryMenu;
        } else {
            if (!request.has("table")) throw new Fail("no_table");
            var pos = pos(request.getAsJsonArray("table"));
            if (!player.level().getBlockState(pos).is(Blocks.CRAFTING_TABLE)) throw new Fail("no_table");
            if (!player.isWithinBlockInteractionRange(pos, 0)) throw new Fail("out_of_reach");
            open(player, pos);
            if (!(player.containerMenu instanceof CraftingMenu table)) throw new Fail("no_table");
            menu = table;
        }
        int crafted = 0, items = 0;
        String stop = "times_done";
        try {
            var grid = (net.minecraft.world.inventory.AbstractCraftingMenu) menu;
            for (int i = 0; i < times; i++) {
                if (menu.handlePlacement(false, false, holder.get(), player.level(), player.getInventory())
                        == RecipeBookMenu.PostPlaceAction.PLACE_GHOST_RECIPE) { stop = "missing_ingredients"; break; }
                var result = grid.getResultSlot();
                if (!result.hasItem()) { stop = "no_result"; break; }
                int produced = result.getItem().getCount();
                menu.clicked(result.index, 0, ContainerInput.QUICK_MOVE, player);
                if (result.hasItem()) { stop = "inventory_full"; break; }
                crafted++;
                items += produced;
            }
        } finally {
            if (menu == player.inventoryMenu) player.inventoryMenu.removed(player); // grid back to the inventory
            else player.closeContainer();
        }
        if (crafted == 0) throw new Fail(stop);
        var out = new LinkedHashMap<String, Object>();
        out.put("recipe", recipeId);
        out.put("crafted", crafted);
        out.put("crafted_items", items);
        out.put("stopped", stop);
        return out;
    }

    // ---- furnace
    static Map<String, Object> furnace(MinecraftServer server, ServerPlayer player, JsonObject request) {
        var pos = pos(request.getAsJsonArray("pos"));
        String action = request.get("action").getAsString();
        if (!action.equals("load") && !action.equals("collect")) throw new Fail("invalid_action");
        if (!(player.level().getBlockState(pos).getBlock() instanceof AbstractFurnaceBlock)) throw new Fail("not_a_furnace");
        if (!player.isWithinBlockInteractionRange(pos, 0)) throw new Fail("out_of_reach");
        open(player, pos);
        if (!(player.containerMenu instanceof AbstractFurnaceMenu menu)) throw new Fail("cannot_open");
        var out = new LinkedHashMap<String, Object>();
        try {
            if (action.equals("load")) {
                if (!request.has("input") && !request.has("fuel")) throw new Fail("nothing_to_load");
                if (request.has("input")) {
                    int moved = moveInto(player, menu, AbstractFurnaceMenu.INGREDIENT_SLOT, request.get("input").getAsString(),
                            request.get("count").getAsInt(), stack -> smeltable(server, player, menu, stack), "not_smeltable");
                    out.put("input_moved", moved);
                }
                if (request.has("fuel")) {
                    int moved = moveInto(player, menu, AbstractFurnaceMenu.FUEL_SLOT, request.get("fuel").getAsString(),
                            request.get("fuel_count").getAsInt(), stack -> menu.getSlot(AbstractFurnaceMenu.FUEL_SLOT).mayPlace(stack), "not_fuel");
                    out.put("fuel_moved", moved);
                }
            } else {
                var result = menu.getResultSlot();
                int before = result.getItem().getCount();
                String item = result.hasItem() ? itemId(result.getItem()) : "";
                if (before > 0) menu.clicked(AbstractFurnaceMenu.RESULT_SLOT, 0, ContainerInput.QUICK_MOVE, player);
                int collected = before - result.getItem().getCount();
                if (before > 0 && collected == 0) throw new Fail("inventory_full");
                out.put("collected", collected);
                out.put("collected_item", item);
            }
            out.put("furnace", furnaceState(menu));
        } finally { player.closeContainer(); }
        return out;
    }

    private static boolean smeltable(MinecraftServer server, ServerPlayer player, AbstractFurnaceMenu menu, ItemStack stack) {
        var input = new SingleRecipeInput(stack);
        var level = player.level();
        var manager = server.getRecipeManager();
        if (menu instanceof FurnaceMenu) return manager.getRecipeFor(RecipeType.SMELTING, input, level).isPresent();
        if (menu instanceof BlastFurnaceMenu) return manager.getRecipeFor(RecipeType.BLASTING, input, level).isPresent();
        if (menu instanceof SmokerMenu) return manager.getRecipeFor(RecipeType.SMOKING, input, level).isPresent();
        return false;
    }

    private static Map<String, Object> furnaceState(AbstractFurnaceMenu menu) {
        var out = new LinkedHashMap<String, Object>();
        out.put("input", stack(menu.getSlot(AbstractFurnaceMenu.INGREDIENT_SLOT).getItem()));
        out.put("fuel", stack(menu.getSlot(AbstractFurnaceMenu.FUEL_SLOT).getItem()));
        out.put("output", stack(menu.getSlot(AbstractFurnaceMenu.RESULT_SLOT).getItem()));
        out.put("lit", menu.isLit());
        out.put("cook_progress", menu.getBurnProgress());
        return out;
    }

    /**
     * Move up to count of an item from the player's inventory slots of an open menu into one menu slot,
     * by pickup / place / return clicks. Returns how many arrived.
     */
    private static int moveInto(ServerPlayer player, net.minecraft.world.inventory.AbstractContainerMenu menu, int target,
                                String itemId, int count, java.util.function.Predicate<ItemStack> acceptable, String refusal) {
        if (count < 1 || count > 64) throw new Fail("invalid_count");
        if (Identifier.tryParse(itemId) == null) throw new Fail("unknown_item");
        int remaining = count, moved = 0;
        boolean found = false;
        for (int i = PLAYER_SLOTS_FIRST; i <= PLAYER_SLOTS_LAST && remaining > 0; i++) {
            var source = menu.getSlot(i).getItem();
            if (source.isEmpty() || !itemId(source).equals(itemId)) continue;
            found = true;
            if (!acceptable.test(source)) throw new Fail(refusal);
            var held = menu.getSlot(target).getItem();
            if (!held.isEmpty() && !ItemStack.isSameItemSameComponents(held, source)) throw new Fail("slot_occupied");
            int before = held.getCount();
            int take = Math.min(remaining, source.getCount());
            menu.clicked(i, 0, ContainerInput.PICKUP, player); // lift the whole stack
            if (take >= menu.getCarried().getCount()) menu.clicked(target, 0, ContainerInput.PICKUP, player);
            else for (int k = 0; k < take; k++) menu.clicked(target, 1, ContainerInput.PICKUP, player); // one at a time
            if (!menu.getCarried().isEmpty()) menu.clicked(i, 0, ContainerInput.PICKUP, player); // put the rest back
            if (!menu.getCarried().isEmpty()) throw new Fail("could_not_return_stack");
            int arrived = menu.getSlot(target).getItem().getCount() - before;
            if (arrived <= 0) break;
            moved += arrived;
            remaining -= arrived;
        }
        if (!found) throw new Fail("missing_" + (target == AbstractFurnaceMenu.FUEL_SLOT ? "fuel" : "input"));
        return moved;
    }

    // ---- equip
    static Map<String, Object> equip(ServerPlayer player, JsonObject request) {
        String item = request.get("item").getAsString();
        if (Identifier.tryParse(item) == null || !BuiltInRegistries.ITEM.containsKey(Identifier.parse(item))) throw new Fail("unknown_item");
        var inventory = player.getInventory();
        int found = -1;
        for (int i = 0; i < 36 && found < 0; i++)
            if (!inventory.getItem(i).isEmpty() && itemId(inventory.getItem(i)).equals(item)) found = i;
        if (found < 0) throw new Fail("missing_item");
        int hotbar = found;
        if (found >= 9) {
            hotbar = -1;
            for (int i = 0; i < 9 && hotbar < 0; i++) if (inventory.getItem(i).isEmpty()) hotbar = i;
            if (hotbar < 0) hotbar = inventory.getSelectedSlot();
            if (player.containerMenu != player.inventoryMenu) player.closeContainer();
            // Menu slots: main inventory 9..35 keep their index; the swap button is the hotbar index.
            player.inventoryMenu.clicked(found, hotbar, ContainerInput.SWAP, player);
            if (!itemId(inventory.getItem(hotbar)).equals(item)) throw new Fail("swap_failed");
        }
        inventory.setSelectedSlot(hotbar);
        var out = new LinkedHashMap<String, Object>();
        out.put("item", item);
        out.put("hotbar_slot", hotbar);
        return out;
    }

    // ---- helpers
    private static void open(ServerPlayer player, BlockPos pos) {
        var level = player.level();
        var hit = new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false);
        level.getBlockState(pos).useWithoutItem(level, player, hit);
    }

    private static BlockPos pos(com.google.gson.JsonArray a) {
        if (a == null || a.size() != 3) throw new Fail("invalid_pos");
        return new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
    }

    private static String itemId(ItemStack stack) { return BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(); }

    private static Map<String, Object> stack(ItemStack stack) {
        return stack.isEmpty() ? Map.of("item", "none", "count", 0) : Map.of("item", itemId(stack), "count", stack.getCount());
    }
}
