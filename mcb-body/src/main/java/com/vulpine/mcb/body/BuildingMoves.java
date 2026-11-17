package com.vulpine.mcb.body;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Companion moves (edits "doors" / "vines" of a plan request): how the planner and the motor see
 * wooden doors, fence gates and vines, and the body opening doors on its way.
 * - A wooden door or fence gate reads as passable (no collision): the body opens it when it comes
 *   within reach, the way a player walks through (iron doors need redstone and stay walls).
 * - Without vines, a vine cell reads as plain air: it is never climbed (ladders and scaffolding are).
 * Trapdoors are left as they are: open ones are already passable, closed ones are floor.
 * A door the body opened is closed behind it, as a player keeps mobs out (closeBehind).
 */
final class BuildingMoves {
    private BuildingMoves() {}

    private static final Set<String> VINES = Set.of("minecraft:vine", "minecraft:twisting_vines",
            "minecraft:twisting_vines_plant", "minecraft:weeping_vines", "minecraft:weeping_vines_plant",
            "minecraft:cave_vines", "minecraft:cave_vines_plant");
    private static final double OPEN_REACH = 1.5; // opened once the body is this close
    private static final double PASSED_BY = 2.5;   // an opened door never walked through closes this far off
    /** Doors each body opened, and whether it has been in the doorway since. */
    private static final Map<UUID, Map<BlockPos, Boolean>> OPENED = new HashMap<>();

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
            OPENED.computeIfAbsent(player.getUUID(), k -> new HashMap<>()).put(pos.immutable(), false);
        }
    }

    /** Close each door this body opened once it has walked through and out of the doorway. */
    static void closeBehind(ServerPlayer player) {
        var doors = OPENED.get(player.getUUID());
        if (doors == null) return;
        var level = player.level();
        var box = player.getBoundingBox();
        for (var it = doors.entrySet().iterator(); it.hasNext(); ) {
            var door = it.next();
            BlockPos pos = door.getKey();
            BlockState state = level.getBlockState(pos);
            if (!state.hasProperty(BlockStateProperties.OPEN) || !state.getValue(BlockStateProperties.OPEN)) {
                it.remove(); // broken, or closed already
                continue;
            }
            // Either half of a door may be the one recorded: the doorway spans the cell below and above.
            var doorway = new AABB(pos.getX(), pos.getY() - 1, pos.getZ(), pos.getX() + 1, pos.getY() + 2, pos.getZ() + 1);
            if (box.intersects(doorway)) { door.setValue(true); continue; }
            if (!door.getValue() && player.position().distanceTo(Vec3.atBottomCenterOf(pos)) <= PASSED_BY) continue;
            state.useWithoutItem(level, player, new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false));
            it.remove();
        }
        if (doors.isEmpty()) OPENED.remove(player.getUUID());
    }
}
