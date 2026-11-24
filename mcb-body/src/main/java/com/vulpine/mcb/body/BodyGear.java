package com.vulpine.mcb.body;

import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.ItemAttributeModifiers;
import net.minecraft.world.item.enchantment.EnchantmentHelper;
import net.minecraft.world.item.enchantment.Enchantments;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The wear op: puts on the best armor carried in each slot and a shield in the off hand
 * (GearLogic decides; this reads the game's items and moves them). Reports what is worn.
 */
final class BodyGear {
    private BodyGear() {}

    private static EquipmentSlot equipment(GearLogic.Slot slot) {
        return switch (slot) {
            case HEAD -> EquipmentSlot.HEAD;
            case CHEST -> EquipmentSlot.CHEST;
            case LEGS -> EquipmentSlot.LEGS;
            case FEET -> EquipmentSlot.FEET;
            case OFFHAND -> EquipmentSlot.OFFHAND;
        };
    }

    private static GearLogic.Slot slotOf(ItemStack stack) {
        if (stack.isEmpty()) return null;
        if (stack.has(DataComponents.BLOCKS_ATTACKS)) return GearLogic.Slot.OFFHAND;
        var equippable = stack.get(DataComponents.EQUIPPABLE);
        if (equippable == null) return null;
        return switch (equippable.slot()) {
            case HEAD -> GearLogic.Slot.HEAD;
            case CHEST -> GearLogic.Slot.CHEST;
            case LEGS -> GearLogic.Slot.LEGS;
            case FEET -> GearLogic.Slot.FEET;
            default -> null;
        };
    }

    private static GearLogic.Piece piece(ServerPlayer player, int index, ItemStack stack, GearLogic.Slot slot) {
        var modifiers = stack.getOrDefault(DataComponents.ATTRIBUTE_MODIFIERS, ItemAttributeModifiers.EMPTY);
        var at = equipment(slot);
        int protection = player.level().registryAccess().lookupOrThrow(Registries.ENCHANTMENT)
                .get(Enchantments.PROTECTION).map(h -> EnchantmentHelper.getItemEnchantmentLevel(h, stack)).orElse(0);
        int left = stack.isDamageableItem() ? stack.getMaxDamage() - stack.getDamageValue() : Integer.MAX_VALUE;
        return new GearLogic.Piece(index, BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(), slot,
                modifiers.compute(Attributes.ARMOR, 0, at), modifiers.compute(Attributes.ARMOR_TOUGHNESS, 0, at),
                protection, left);
    }

    static Map<String, Object> wear(ServerPlayer player) {
        var inv = player.getInventory();
        var carried = new ArrayList<GearLogic.Piece>();
        for (int i = 0; i < 36; i++) {
            var stack = inv.getItem(i);
            var slot = slotOf(stack);
            if (slot != null) carried.add(piece(player, i, stack, slot));
        }
        var worn = new EnumMap<GearLogic.Slot, GearLogic.Piece>(GearLogic.Slot.class);
        var other = EnumSet.noneOf(GearLogic.Slot.class);
        for (var slot : GearLogic.Slot.values()) {
            var stack = player.getItemBySlot(equipment(slot));
            if (stack.isEmpty()) continue;
            if (slotOf(stack) == slot) worn.put(slot, piece(player, -1, stack, slot));
            else other.add(slot);
        }
        var moves = GearLogic.plan(carried, worn, other);
        var put = new ArrayList<String>();
        for (var move : moves) {
            var at = equipment(move.to());
            var on = player.getItemBySlot(at).copy();
            var stack = inv.getItem(move.from()).copy();
            player.setItemSlot(at, stack);
            inv.setItem(move.from(), on);  // what was worn goes back where the new piece was
            put.add(BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
        }
        if (!moves.isEmpty()) player.containerMenu.broadcastChanges();
        var out = new LinkedHashMap<String, Object>();
        out.put("put_on", put);
        out.put("worn", worn(player));
        var on = new ArrayList<GearLogic.Piece>();
        for (var slot : GearLogic.Slot.values()) {
            var stack = player.getItemBySlot(equipment(slot));
            if (slotOf(stack) == slot) on.add(piece(player, -1, stack, slot));
        }
        out.put("armor", GearLogic.armor(on));  // the attribute itself catches up on the next tick
        return out;
    }

    /** Each gear slot's item (absent: empty). */
    static Map<String, Object> worn(ServerPlayer player) {
        var out = new LinkedHashMap<String, Object>();
        for (var slot : GearLogic.Slot.values()) {
            var stack = player.getItemBySlot(equipment(slot));
            if (!stack.isEmpty()) out.put(GearLogic.slotName(slot), BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
        }
        return out;
    }
}
