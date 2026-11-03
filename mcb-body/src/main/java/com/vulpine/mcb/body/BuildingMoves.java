package com.vulpine.mcb.body;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Companion moves (edits "doors" / "vines" of a plan request): how the planner and the motor see
 * wooden doors, fence gates and vines, and the body opening doors on its way.
 * - A wooden door or fence gate reads as passable (no collision): the body opens it when it comes
 *   within reach, the way a player walks through (iron doors need redstone and stay walls).
 * - Without vines, a vine cell reads as plain air: it is never climbed (ladders and scaffolding are).
 * Trapdoors are left as they are: open ones are already passable, closed ones are floor.
 */
final class BuildingMoves {
    private BuildingMoves() {}

    private static final Set<String> VINES = Set.of("minecraft:vine", "minecraft:twisting_vines",
            "minecraft:twisting_vines_plant", "minecraft:weeping_vines", "minecraft:weeping_vines_plant",
            "minecraft:cave_vines", "minecraft:cave_vines_plant");
    private static final double OPEN_REACH = 1.5; // opened once the body is this close

    static boolean opensByHand(String block) {
        return block != null && !block.equals("minecraft:iron_door")
                && (block.endsWith("_door") || block.endsWith("_fence_gate"));
    }

    static Map<String, Object> view(Map<String, Object> cell, boolean doors, boolean vines) {
        if (cell == null) return null;
        Object block = cell.get("block");
        if (doors && block instanceof String b && opensByHand(b) && !List.of().equals(cell.get("collision_boxes"))) {
            var open = new HashMap<>(cell);
            open.put("collision_boxes", List.of());
            return open;
        }
        if (!vines && VINES.contains(block)) {
            var air = new HashMap<>(cell);
            air.put("block", "minecraft:air");
            return air;
        }
        return cell;
    }

    /** Open closed wooden doors and fence gates around the body's feet and head. */
    static void openNear(ServerPlayer player) {
        var level = player.level();
        BlockPos feet = player.blockPosition();
        for (int dx = -1; dx <= 1; dx++) for (int dz = -1; dz <= 1; dz++) for (int dy = 0; dy <= 1; dy++) {
            BlockPos pos = feet.offset(dx, dy, dz);
            BlockState state = level.getBlockState(pos);
            String id = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
            if (!opensByHand(id) || !state.hasProperty(BlockStateProperties.OPEN) || state.getValue(BlockStateProperties.OPEN))
                continue;
            if (player.position().distanceTo(Vec3.atBottomCenterOf(pos)) > OPEN_REACH) continue;
            state.useWithoutItem(level, player, new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false));
        }
    }
}
