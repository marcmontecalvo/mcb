package com.vulpine.mcb.arena;

import com.vulpine.mcb.body.McbCommand;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerEntityEvents;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.fabricmc.fabric.api.event.player.UseItemCallback;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.item.BucketItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;

/**
 * What a registered arena refuses, always, to every player (fake players and the body included): breaking and placing
 * blocks, buckets, fire and bone meal, and any mob that is not a wave mob. Static entry points for the Fabric events, the
 * mixins and the body's own build and break ops; each answers from the registry and is false/allowing when none is loaded.
 */
public final class ArenaGuard {
    private ArenaGuard() {}

    /** Fire this near an arena's bounds could spread into it (spread reaches one cell sideways, four up). */
    public static final int FIRE_REACH = 4;

    public static String dimension(Level level) { return level.dimension().identifier().toString(); }

    /** The arena holding this block cell, else null. */
    public static ArenaRecord arenaAt(Level level, BlockPos pos) {
        var runtime = ArenaRuntime.active();
        return runtime == null ? null : runtime.registry().at(dimension(level), pos.getX(), pos.getY(), pos.getZ());
    }

    /** Whether a block cell lies inside a registered arena: nothing may break or place there. */
    public static boolean protectedCell(Level level, BlockPos pos) { return arenaAt(level, pos) != null; }

    /** Whether an entity stands inside a registered arena (mob griefing is refused there). */
    public static boolean protectedPoint(Level level, double x, double y, double z) {
        var runtime = ArenaRuntime.active();
        return runtime != null && runtime.registry().atPoint(dimension(level), x, y, z) != null;
    }

    /** Whether the cell is inside, or within {@code margin} blocks of, a registered arena. */
    public static boolean nearArena(Level level, BlockPos pos, int margin) {
        var runtime = ArenaRuntime.active();
        if (runtime == null) return false;
        String dimension = dimension(level);
        for (var arena : runtime.registry().all()) {
            if (arena.dimension().equals(dimension) && arena.box().inflate(margin).contains(pos.getX(), pos.getY(), pos.getZ())) return true;
        }
        return false;
    }

    /**
     * An item an arena refuses to anyone when used on or near it: a bucket, flint and steel, a fire charge, bone meal, or
     * an item that puts an entity in the world. Block items are not here: opening a door with stone in hand must work,
     * so placing is stopped at {@code BlockItem.place} instead (BlockPlaceMixin).
     */
    public static boolean restricted(ItemStack stack) {
        if (stack.isEmpty()) return false;
        var item = stack.getItem();
        if (item instanceof BucketItem) return true;
        String id = BuiltInRegistries.ITEM.getKey(item).toString();
        return ArenaLogic.restrictedItem(id) || ArenaLogic.entityItem(id);
    }

    /** How far from the bounds a restricted item counts as used on the arena. */
    public static int reach(ItemStack stack) { return ArenaLogic.INTERACT_MARGIN; }

    public static void register() {
        PlayerBlockBreakEvents.BEFORE.register((level, player, pos, state, entity) -> !protectedCell(level, pos));
        UseBlockCallback.EVENT.register((player, level, hand, hit) -> {
            var clicked = hit.getBlockPos();
            var arena = arenaAt(level, clicked);
            if (arena != null && ArenaLobbyStations.LOBBY_ARENA.equals(arena.name())) {
                var station = ArenaLobbyStations.at(arena.origin(), clicked.getX(), clicked.getY(), clicked.getZ());
                if (station != null) {
                    if (level instanceof ServerLevel && hand == InteractionHand.MAIN_HAND && player instanceof ServerPlayer serverPlayer) {
                        if (station.help()) ArenaLobby.openFor(serverPlayer);
                        else McbCommand.dispatch(serverPlayer, "lobby", station.click());
                    }
                    var o = arena.origin();
                    // the sign never opens its edit screen; the button presses visibly (vanilla uses it) unless the hand
                    // holds what the arena refuses
                    if (station.isSign(clicked.getX() - o[0], clicked.getY() - o[1], clicked.getZ() - o[2])) return InteractionResult.SUCCESS;
                    return restricted(player.getItemInHand(hand)) ? InteractionResult.FAIL : InteractionResult.PASS;
                }
            }
            var stack = player.getItemInHand(hand);
            if (ArenaLobby.isMenuCompass(stack)) {
                // the Game Menu compass opens the menu on any block too (and never binds to a lodestone)
                if (level instanceof ServerLevel && player instanceof ServerPlayer serverPlayer) ArenaLobby.openFor(serverPlayer);
                return InteractionResult.SUCCESS;
            }
            if (!restricted(stack)) return InteractionResult.PASS;
            int reach = reach(stack);
            return nearArena(level, clicked, reach) || nearArena(level, clicked.relative(hit.getDirection()), reach)
                    ? InteractionResult.FAIL : InteractionResult.PASS;
        });
        UseItemCallback.EVENT.register((player, level, hand) -> {
            var stack = player.getItemInHand(hand);
            if (ArenaLobby.isMenuCompass(stack)) {
                if (level instanceof ServerLevel && player instanceof ServerPlayer serverPlayer) ArenaLobby.openFor(serverPlayer);
                return InteractionResult.SUCCESS;
            }
            return restricted(stack) && nearArena(level, player.blockPosition(), reach(stack)) ? InteractionResult.FAIL : InteractionResult.PASS;
        });
        ServerEntityEvents.ALLOW_LOAD.register(ArenaGuard::allowLoad);
    }

    /**
     * The entity-load gate: false discards the entity. A dropped Game Menu compass never lands (the lobby hands it back);
     * from disk, stale wave mobs, litter and lobby boards go; otherwise only mobs added fresh inside an arena are judged.
     */
    static boolean allowLoad(Entity entity, ServerLevel level, EntitySpawnReason reason, boolean fromDisk) {
        if (entity instanceof ItemEntity item && ArenaLobby.isMenuCompass(item.getItem())) return false;
        var runtime = ArenaRuntime.active();
        if (runtime == null) return true;
        if (fromDisk) return !runtime.staleWaveMob(entity) && !runtime.staleLitter(entity, level) && !runtime.lobby().staleBoard(entity);
        if (!(entity instanceof Mob mob)) return true;
        var arena = runtime.registry().atPoint(dimension(level), entity.getX(), entity.getY(), entity.getZ());
        return arena == null || runtime.admitMob(arena, mob, reason);
    }
}
