package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;

import java.util.List;

/**
 * One block being broken by holding attack across ticks. BodyRuntime ticks it: step() aims at the block
 * each tick (the body does not move) and reports null while running, otherwise the outcome reason.
 */
final class BreakTask {
    static final int TIMEOUT_TICKS = 400;

    final long requestSequence;
    final BlockPos pos;
    final BlockState original;
    int ticks;

    private BreakTask(long requestSequence, BlockPos pos, BlockState original) {
        this.requestSequence = requestSequence;
        this.pos = pos;
        this.original = original;
    }

    /** Validates, equips the tool and aims; throws Fail for out_of_reach, no_line_of_sight, wrong_tool, ... */
    static BreakTask start(ServerPlayer player, JsonObject request, long sequence) {
        BlockPos pos = BodyBuild.pos(request);
        var level = player.level();
        var state = level.getBlockState(pos);
        if (state.isAir()) throw new BodyActions.Fail("nothing_to_break");
        if (state.getDestroySpeed(level, pos) < 0) throw new BodyActions.Fail("unbreakable");
        BodyBuild.nearestHit(player, pos); // out_of_reach / no_line_of_sight before touching the inventory
        String tool = request.has("tool") ? request.get("tool").getAsString() : bestTool(player, state);
        if (tool != null) BodyBuild.selectItem(player, tool);
        else player.getInventory().setSelectedSlot(emptyHotbarSlot(player));
        var held = player.getItemInHand(net.minecraft.world.InteractionHand.MAIN_HAND);
        if (state.requiresCorrectToolForDrops() && !held.isCorrectToolForDrops(state)) throw new BodyActions.Fail("wrong_tool");
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
        var at = hit.getLocation();
        BodyBuild.aim(player, (float) Math.toDegrees(Math.atan2(-(at.x - eye.x), at.z - eye.z)),
                PlacementLogic.pitchTo(at.x - eye.x, at.y - eye.y, at.z - eye.z));
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
    private static int emptyHotbarSlot(ServerPlayer player) {
        var inventory = player.getInventory();
        for (int i = 0; i < 9; i++) if (inventory.getItem(i).isEmpty()) return i;
        return inventory.getSelectedSlot();
    }

    List<Integer> posList() { return List.of(pos.getX(), pos.getY(), pos.getZ()); }
}
