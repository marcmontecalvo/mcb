package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.phys.shapes.CollisionContext;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Placing and using blocks the way a player does: aim, then a real use through the player's game mode
 * (the path a use packet takes). place_block first simulates the block's own getStateForPlacement
 * (Baritone's method) to pick the face, hit point and facing that give the requested state.
 */
final class BodyBuild {
    private BodyBuild() {}

    private static final List<Direction> PLACE_ORDER = List.of(Direction.DOWN, Direction.NORTH, Direction.EAST,
            Direction.SOUTH, Direction.WEST, Direction.UP);

    // ---- place_block
    /** B7: placing is done while sneaking, so a use never opens the neighbour (table, chest, door). */
    static Map<String, Object> place(ServerPlayer player, JsonObject request) {
        boolean sneaking = player.isShiftKeyDown();
        player.setShiftKeyDown(true);
        try { return placeSneaking(player, request); }
        finally { player.setShiftKeyDown(sneaking); }
    }

    /** A5: the packet path's checks (spawn protection, mayInteract, build limits) before acting on a block. */
    static void checkPermission(ServerPlayer player, BlockPos pos) {
        var level = player.level();
        if (pos.getY() > level.getMaxY() || pos.getY() < level.getMinY()) throw new BodyActions.Fail("protected");
        if (level.getServer().isUnderSpawnProtection(level, pos, player) || !level.mayInteract(player, pos))
            throw new BodyActions.Fail("protected");
    }

    private static Map<String, Object> placeSneaking(ServerPlayer player, JsonObject request) {
        BlockPos pos = pos(request);
        PlacementLogic.ParsedState target;
        try { target = PlacementLogic.parse(request.get("state").getAsString()); }
        catch (IllegalArgumentException e) { throw new BodyActions.Fail("invalid_state"); }
        Identifier blockId = Identifier.tryParse(target.block());
        if (blockId == null || !BuiltInRegistries.BLOCK.containsKey(blockId)) throw new BodyActions.Fail("invalid_state");
        var block = BuiltInRegistries.BLOCK.getValue(blockId);
        // The block's own item (wall_torch -> torch), unless the request names one.
        String item = request.has("item") ? request.get("item").getAsString()
                : BuiltInRegistries.ITEM.getKey(block.asItem()).toString();
        var level = player.level();
        if (!level.getBlockState(pos).canBeReplaced()) throw new BodyActions.Fail("occupied");
        // Grass, flowers, snow layers: replaceable, but their outline stops the eye rays to the
        // faces behind them, so nothing reads as visible (live run 20261102T215645669803Z: every
        // floor cell on grass). A player punches them out first; they break at once.
        var occupant = level.getBlockState(pos);
        if (!occupant.isAir() && occupant.getFluidState().isEmpty()
                && occupant.getDestroySpeed(level, pos) == 0) {
            checkPermission(player, pos);
            player.gameMode.destroyBlock(pos);
        }
        selectItem(player, item); // missing_item / unknown_item before any rotation changes
        var stack = player.getItemInHand(InteractionHand.MAIN_HAND);
        if (!(stack.getItem() instanceof BlockItem blockItem)) throw new BodyActions.Fail("not_placeable");

        float yaw0 = player.getYRot(), pitch0 = player.getXRot();
        boolean anySolid = false;
        int[] stats = new int[2];
        BlockHitResult chosen = null;
        float chosenYaw = 0, chosenPitch = 0;
        boolean chosenSneak = true;
        // Scaffolding is the one block whose item reads the sneak key: a click on the side of a column
        // goes to the top of the column only when not sneaking (ScaffoldingBlockItem.updatePlacementContext).
        boolean[] sneaks = blockItem.getBlock() == Blocks.SCAFFOLDING ? new boolean[] {true, false} : new boolean[] {true};
        Vec3 eye = player.getEyePosition();
        // Blocks to click and the faces to click them on: each neighbour of the cell, on the face toward it.
        var sources = new ArrayList<Source>();
        for (Direction d : PLACE_ORDER) {
            BlockPos neighbour = pos.relative(d);
            var nstate = level.getBlockState(neighbour);
            // Scaffolding has no collision from the side (it is walked through) yet is clicked
            // like any block; ScaffoldingBlockItem then picks the cell (the top of a column).
            if (nstate.canBeReplaced()
                    || (nstate.getCollisionShape(level, neighbour).isEmpty() && !nstate.is(Blocks.SCAFFOLDING))) continue;
            sources.add(new Source(neighbour, List.of(d.getOpposite())));
        }
        if (sneaks.length > 1) {
            // A column of scaffolding is extended by clicking its side (not sneaking): the top face
            // is above the eye of a body standing beside a tall column.
            for (BlockPos c = pos.below(); level.getBlockState(c).is(Blocks.SCAFFOLDING); c = c.below())
                sources.add(new Source(c, List.of(Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST)));
        }
        anySolid = !sources.isEmpty();
        // Why each clickable neighbour was rejected, reported when nothing works (state_unreachable).
        var diagnosis = new ArrayList<Map<String, Object>>();
        search:
        for (Source source : sources) {
            int[] own = new int[2];
            var found = hits(player, source.block(), source.faces(), true, own);
            stats[0] += own[0]; stats[1] += own[1];
            var why = new LinkedHashMap<String, Object>();
            why.put("block", List.of(source.block().getX(), source.block().getY(), source.block().getZ()));
            why.put("state", render(level.getBlockState(source.block())));
            why.put("faces", source.faces().stream().map(Direction::getSerializedName).toList());
            why.put("points_in_reach", own[0]);
            why.put("points_visible", own[1]);
            int[] rejected = new int[3]; // cannot_place, no_state, wrong_state
            var wrong = new java.util.TreeSet<String>();
            diagnosis.add(why);
            for (BlockHitResult hit : found) {
                Vec3 at = hit.getLocation();
                float pitch = PlacementLogic.pitchTo(at.x - eye.x, at.y - eye.y, at.z - eye.z);
                for (String facing : PlacementLogic.yawOrder(target)) {
                    float yaw = PlacementLogic.yawFor(facing);
                    aim(player, yaw, pitch);
                    for (boolean sneak : sneaks) {
                        player.setShiftKeyDown(sneak);
                        var context = new BlockPlaceContext(player, InteractionHand.MAIN_HAND, stack, hit);
                        if (!context.canPlace()) { rejected[0]++; continue; }
                        BlockState candidate = placementState(blockItem, context, pos);
                        if (candidate == null) { rejected[1]++; continue; }
                        if (PlacementLogic.matches(target, PlacementLogic.parse(render(candidate)))) {
                            chosen = hit; chosenYaw = yaw; chosenPitch = pitch; chosenSneak = sneak;
                            break search;
                        }
                        rejected[2]++;
                        if (wrong.size() < 4) wrong.add(render(candidate));
                    }
                }
            }
            why.put("cannot_place", rejected[0]);  // the item refuses the context (cell occupied)
            why.put("no_state", rejected[1]);      // obstructed (an entity, the body), cannot survive, or moved
            why.put("wrong_state", rejected[2]);   // placeable, but not in the requested orientation
            why.put("wrong_states", List.copyOf(wrong));
        }
        player.setShiftKeyDown(chosenSneak);
        if (chosen == null) {
            aim(player, yaw0, pitch0);
            if (!anySolid) throw new BodyActions.Fail("no_support_face");
            if (stats[0] == 0) throw new BodyActions.Fail("out_of_reach");
            var eyeAt = List.of(eye.x, eye.y, eye.z);
            throw new Unplaceable(Map.of("eye", eyeAt, "body", List.of(player.getX(), player.getY(), player.getZ()),
                    "sources", diagnosis));
        }
        aim(player, chosenYaw, chosenPitch);
        checkPermission(player, chosen.getBlockPos());
        player.gameMode.useItemOn(player, level, player.getItemInHand(InteractionHand.MAIN_HAND), InteractionHand.MAIN_HAND, chosen);
        if (player.containerMenu != player.inventoryMenu) player.closeContainer();
        String placed = render(level.getBlockState(pos));
        if (!PlacementLogic.matches(target, PlacementLogic.parse(placed))) {
            // The failure event carries the state found in the world.
            throw new PlacedWrong(placed);
        }
        var out = new LinkedHashMap<String, Object>();
        out.put("pos", List.of(pos.getX(), pos.getY(), pos.getZ()));
        out.put("placed", placed);
        out.put("face", chosen.getDirection().getSerializedName());
        out.put("hit", vec(chosen.getLocation()));
        out.put("yaw", chosenYaw);
        out.put("pitch", chosenPitch);
        return out;
    }

    private record Source(BlockPos block, List<Direction> faces) {}

    private static final java.lang.reflect.Method PLACEMENT_STATE = placementStateMethod();

    private static java.lang.reflect.Method placementStateMethod() {
        try {
            var method = BlockItem.class.getDeclaredMethod("getPlacementState", BlockPlaceContext.class);
            method.setAccessible(true);
            return method;
        } catch (ReflectiveOperationException e) {
            throw new ExceptionInInitializerError(e);
        }
    }

    /**
     * What the item itself would place for this context: the same call BlockItem.place makes, so standing
     * and wall variants (torches), survival and obstruction checks are the game's own.
     */
    private static BlockState placementState(BlockItem item, BlockPlaceContext context, BlockPos expected) {
        var updated = item.updatePlacementContext(context);
        // Scaffolding may move the placement along a column; only the requested cell counts.
        if (updated == null || !updated.getClickedPos().equals(expected)) return null;
        try {
            return (BlockState) PLACEMENT_STATE.invoke(item, updated);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("placement_state_unavailable", e);
        }
    }

    /** state_unreachable with the search's account of every clickable neighbour. */
    static final class Unplaceable extends BodyActions.Fail {
        final Map<String, Object> diagnosis;
        Unplaceable(Map<String, Object> diagnosis) { super("state_unreachable"); this.diagnosis = diagnosis; }
    }

    /** placed_wrong carries the state the world ended up with. */
    static final class PlacedWrong extends RuntimeException {
        final String placed;
        PlacedWrong(String placed) { super("placed_wrong"); this.placed = placed; }
    }

    // ---- use_block
    static Map<String, Object> use(ServerPlayer player, JsonObject request) {
        BlockPos pos = pos(request);
        var level = player.level();
        var before = level.getBlockState(pos);
        if (before.isAir()) throw new BodyActions.Fail("nothing_to_use");
        if (request.has("item")) selectItem(player, request.get("item").getAsString());
        var hit = nearestHit(player, pos);
        Vec3 eye = player.getEyePosition(), at = hit.getLocation();
        float yaw0 = player.getYRot(), pitch0 = player.getXRot();
        aim(player, (float) Math.toDegrees(Math.atan2(-(at.x - eye.x), at.z - eye.z)),
                PlacementLogic.pitchTo(at.x - eye.x, at.y - eye.y, at.z - eye.z));
        checkPermission(player, pos);
        var result = player.gameMode.useItemOn(player, level, player.getItemInHand(InteractionHand.MAIN_HAND), InteractionHand.MAIN_HAND, hit);
        if (player.containerMenu != player.inventoryMenu) player.closeContainer();
        if (!result.consumesAction()) { aim(player, yaw0, pitch0); throw new BodyActions.Fail("no_effect"); }
        var out = new LinkedHashMap<String, Object>();
        out.put("pos", List.of(pos.getX(), pos.getY(), pos.getZ()));
        out.put("before", render(before));
        out.put("after", render(level.getBlockState(pos)));
        return out;
    }

    // ---- shared helpers
    /** The closest visible, reachable point on the block (any face), as the real ray result. */
    static BlockHitResult nearestHit(ServerPlayer player, BlockPos pos) {
        int[] stats = new int[2];
        var all = hits(player, pos, List.of(Direction.values()), false, stats);
        if (all.isEmpty()) {
            if (stats[0] == 0) throw new BodyActions.Fail("out_of_reach");
            throw new BodyActions.Fail("no_line_of_sight");
        }
        Vec3 eye = player.getEyePosition();
        return all.stream().min(Comparator.comparingDouble(h -> h.getLocation().distanceToSqr(eye))).orElseThrow();
    }

    /**
     * Real ray hits on the given faces of a block: points on the face (centre and quarter points) within
     * reach whose eye ray first meets that block. stats[0] counts points in reach, stats[1] visible ones.
     */
    static List<BlockHitResult> hits(ServerPlayer player, BlockPos block, List<Direction> faces, boolean exactFace, int[] stats) {
        var level = player.level();
        Vec3 eye = player.getEyePosition();
        double reach = player.blockInteractionRange();
        var out = new ArrayList<BlockHitResult>();
        for (Direction face : faces) {
            for (double[] offset : PlacementLogic.HIT_OFFSETS) {
                double[] v = PlacementLogic.facePoint(face.getSerializedName(), offset[0], offset[1]);
                Vec3 point = Vec3.atCenterOf(block).add(v[0], v[1], v[2]);
                if (eye.distanceTo(point) > reach) continue;
                stats[0]++;
                Vec3 beyond = point.add(point.subtract(eye).normalize().scale(0.05));
                var hit = level.clip(new ClipContext(eye, beyond, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, player));
                if (hit.getType() != HitResult.Type.BLOCK || !hit.getBlockPos().equals(block)) continue;
                if (exactFace && hit.getDirection() != face) continue;
                if (eye.distanceTo(hit.getLocation()) > reach) continue;
                stats[1]++;
                out.add(hit);
            }
        }
        return out;
    }

    static void aim(ServerPlayer player, float yaw, float pitch) {
        player.setYRot(yaw);
        player.setXRot(pitch);
        player.setYHeadRot(yaw);
    }

    /** Hold the item (moving it into the hotbar like equip does). */
    static void selectItem(ServerPlayer player, String item) {
        var json = new JsonObject();
        json.addProperty("item", item);
        BodyActions.equip(player, json);
    }

    static String render(BlockState state) {
        var props = new TreeMap<String, String>();
        for (Property<?> property : state.getProperties()) props.put(property.getName(), valueName(state, property));
        return PlacementLogic.render(new PlacementLogic.ParsedState(
                BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString(), props));
    }

    private static <T extends Comparable<T>> String valueName(BlockState state, Property<T> property) {
        return property.getName(state.getValue(property));
    }

    static BlockPos pos(JsonObject request) {
        var a = request.getAsJsonArray("pos");
        if (a == null || a.size() != 3) throw new BodyActions.Fail("invalid_pos");
        return new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
    }

    static List<Double> vec(Vec3 v) { return List.of(v.x, v.y, v.z); }
}
