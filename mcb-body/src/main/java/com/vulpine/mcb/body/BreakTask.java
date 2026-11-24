package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;

import java.util.Comparator;
import java.util.List;
import java.util.function.Predicate;
import java.util.function.ToDoubleFunction;

/**
 * One block being broken by holding attack across ticks. BodyRuntime ticks it: step() aims at the block
 * each tick (the body does not move) and reports null while running, otherwise the outcome reason.
 */
final class BreakTask {
    static final int TIMEOUT_TICKS = 400;
    /**
     * Ticks over the whole task without an entity-free ray before entity_in_way (run 20261111T135214868983Z: a
     * sheep froze a break 15 s). Counted in total, not in a row: each held tick aborts the dig in Carpet
     * (ABORT_DESTROY_BLOCK), so a mob crossing the ray now and then would otherwise restart it until the timeout.
     */
    static final int ENTITY_WAIT_TICKS = 20;

    final long requestSequence;
    final BlockPos pos;
    final BlockState original;
    int ticks;
    /** Ticks waited after the block went for a fluid beside the cell to flow in (LeakPlug.SETTLE). */
    int settle;
    /** Ticks so far with an entity on every ray. */
    int blocked;
    /** False this tick when an entity is on every ray: BodyRuntime holds the attack so nothing is hit. */
    boolean clear = true;

    private BreakTask(long requestSequence, BlockPos pos, BlockState original) {
        this.requestSequence = requestSequence;
        this.pos = pos;
        this.original = original;
    }

    /** Validates, equips the tool and aims; throws Fail for out_of_reach, no_line_of_sight, wrong_tool, ... */
    static BreakTask start(ServerPlayer player, JsonObject request, long sequence) {
        BlockPos pos = BodyBuild.pos(request);
        var level = player.level();
        BodyBuild.checkArena(player, pos);
        var state = level.getBlockState(pos);
        if (state.isAir()) throw new BodyActions.Fail("nothing_to_break");
        if (state.getDestroySpeed(level, pos) < 0) throw new BodyActions.Fail("unbreakable");
        BodyBuild.nearestHit(player, pos); // out_of_reach / no_line_of_sight before touching the inventory
        String tool = request.has("tool") ? request.get("tool").getAsString() : bestTool(player, state);
        if (tool != null) BodyBuild.selectItem(player, tool);
        else player.getInventory().setSelectedSlot(emptyHotbarSlot(player));
        var held = player.getItemInHand(net.minecraft.world.InteractionHand.MAIN_HAND);
        // drops_optional: an escape dig by hand (all tools broken) breaks stone for the way out, not the drop.
        boolean dropsOptional = request.has("drops_optional") && request.get("drops_optional").getAsBoolean();
        if (!dropsOptional && state.requiresCorrectToolForDrops() && !held.isCorrectToolForDrops(state))
            throw new BodyActions.Fail("wrong_tool");
        var task = new BreakTask(sequence, pos, state);
        task.aim(player);
        return task;
    }

    /** Null while running; "done" when the block is gone or changed; else a failure reason. */
    String step(ServerPlayer player) {
        ticks++;
        if (!player.level().getBlockState(pos).equals(original)) return "done";
        if (ticks > TIMEOUT_TICKS) return "timeout";
        try { aim(player); }
        catch (BodyActions.Fail fail) { return fail.getMessage(); }
        return null;
    }

    private void aim(ServerPlayer player) {
        var hit = BodyBuild.nearestHit(player, pos);
        var eye = player.getEyePosition();
        if (CarpetBodyAdapter.entityOnRay(player, hit.getLocation())) {
            // Carpet's attack would strike the mob or player instead of the block: aim past it or hold.
            hit = clearest(BodyBuild.hits(player, pos, List.of(Direction.values()), false, new int[2]),
                    h -> h.getLocation().distanceToSqr(eye), h -> CarpetBodyAdapter.entityOnRay(player, h.getLocation()));
        }
        clear = hit != null;
        blocked = blockedAfter(blocked, clear);
        if (hit == null) return;
        var at = hit.getLocation();
        BodyBuild.aim(player, (float) Math.toDegrees(Math.atan2(-(at.x - eye.x), at.z - eye.z)),
                PlacementLogic.pitchTo(at.x - eye.x, at.y - eye.y, at.z - eye.z));
    }

    /** The nearest point whose ray no entity crosses; null when every ray is blocked. */
    static <T> T clearest(List<T> points, ToDoubleFunction<T> distance, Predicate<T> entityOnRay) {
        return points.stream().filter(entityOnRay.negate()).min(Comparator.comparingDouble(distance)).orElse(null);
    }

    /** Blocked-tick count after this tick; entity_in_way once more than ENTITY_WAIT_TICKS were blocked. */
    static int blockedAfter(int blocked, boolean clear) {
        if (clear) return blocked;
        if (++blocked > ENTITY_WAIT_TICKS) throw new BodyActions.Fail("entity_in_way");
        return blocked;
    }

    /** Best inventory item for the block by destroy speed (correct-for-drops preferred); null means bare hand. */
    private static String bestTool(ServerPlayer player, BlockState state) {
        var inventory = player.getInventory();
        String best = null;
        float bestSpeed = 1.0f; // an empty hand
        boolean bestCorrect = false;
        for (int i = 0; i < 36; i++) {
            ItemStack stack = inventory.getItem(i);
            if (stack.isEmpty()) continue;
            boolean correct = stack.isCorrectToolForDrops(state);
            float speed = stack.getDestroySpeed(state);
            if ((correct && !bestCorrect) || (correct == bestCorrect && speed > bestSpeed)) {
                best = net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
                bestSpeed = speed; bestCorrect = correct;
            }
        }
        return best;
    }

    /** With no tool, attack with an empty hotbar slot so a block is never struck with an unrelated item. */
    static int emptyHotbarSlot(ServerPlayer player) {
        var inventory = player.getInventory();
        for (int i = 0; i < 9; i++) if (inventory.getItem(i).isEmpty()) return i;
        return inventory.getSelectedSlot();
    }

    List<Integer> posList() { return List.of(pos.getX(), pos.getY(), pos.getZ()); }
}
