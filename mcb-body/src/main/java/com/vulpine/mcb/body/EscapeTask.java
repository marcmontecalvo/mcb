package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Function;

/**
 * The escape op: sprint-jump away from every hostile within the radius, new spawns
 * included, until none is within ``clear``. Unlike a walk away (a route to one spot), it
 * steers each tick: the safe heading with the least hostile pressure (EscapeLogic).
 * Ends done ("clear" or "timeout" in the result), or fails "cornered" when no heading is
 * safe for CORNERED_TICKS. Docs: COMPANION_COMBAT_PLAN_2026-11-23.md section 3.
 */
final class EscapeTask implements BodyEntity.Task {
    /** A jump clears about 1.25 blocks above the 1.8-block body. */
    private static final double JUMP_ROOM = 1.3;

    private final long sequence;
    private final EscapeLogic.Params params;
    private final Function<ServerPlayer, RouteGeometry.Reader> reader;
    private final double startX, startZ;
    private int ticks, clearTicks, corneredTicks, jumps, heading = -1;
    private double nearest = Double.POSITIVE_INFINITY;
    private String end = "clear";
    /** Set by step for controls. */
    private boolean forward, jump, sprint;

    private EscapeTask(long sequence, EscapeLogic.Params params, Function<ServerPlayer, RouteGeometry.Reader> reader,
                       double x, double z) {
        this.sequence = sequence; this.params = params; this.reader = reader; this.startX = x; this.startZ = z;
    }

    static EscapeTask start(ServerPlayer player, JsonObject request, long sequence,
                            Function<ServerPlayer, RouteGeometry.Reader> reader) {
        return new EscapeTask(sequence, EscapeLogic.parse(request), reader, player.getX(), player.getZ());
    }

    @Override public String name() { return "escape"; }
    @Override public long requestSequence() { return sequence; }

    @Override public String step(ServerPlayer player) {
        ticks++;
        jump = false;
        var mobs = new ArrayList<double[]>();
        nearest = Double.POSITIVE_INFINITY;
        var box = player.getBoundingBox().inflate(params.radius(), FightLogic.MAX_DY, params.radius());
        for (Entity e : player.level().getEntities(player, box)) {
            if (!(e instanceof LivingEntity living) || !living.isAlive()) continue;
            if (!RouteReflex.HOSTILES.contains(BodyEntity.typeId(e))) continue;
            double d = Math.sqrt(player.distanceToSqr(e));
            if (d > params.radius()) continue;
            mobs.add(new double[] {e.getX(), e.getZ()});
            nearest = Math.min(nearest, d);
        }
        clearTicks = nearest > params.clear() ? clearTicks + 1 : 0;
        String over = EscapeLogic.end(ticks, params.maxTicks(), clearTicks, corneredTicks);
        if (over != null) {
            forward = false;
            if (over.equals("cornered")) return over;
            end = over;
            return "done";
        }
        if (!player.onGround()) return null;  // mid-jump: hold the heading taken off on

        var geometry = new RouteGeometry(reader.apply(player));
        var velocity = player.getDeltaMovement();
        var pose = new RouteMotor.Pose(player.getX(), player.getY(), player.getZ(), velocity.x, velocity.z,
                player.getYRot(), true, player.getFoodData().getFoodLevel());
        int bx = (int) Math.floor(player.getX()), bz = (int) Math.floor(player.getZ());
        double y = Math.rint(player.getY() * 2) / 2;
        boolean[] safe = new boolean[8];
        int[] rise = new int[8];
        for (int h = 0; h < 8; h++) {
            Integer offset = nextCell(geometry, pose, bx + EscapeLogic.DX[h], bz + EscapeLogic.DZ[h], y, h % 2 == 1);
            safe[h] = offset != null;
            rise[h] = offset == null ? 0 : offset;
        }
        heading = EscapeLogic.heading(player.getX(), player.getZ(), mobs, safe, heading);
        if (heading < 0) { corneredTicks++; forward = false; sprint = false; return null; }
        corneredTicks = 0;
        int dx = EscapeLogic.DX[heading], dz = EscapeLogic.DZ[heading];
        BodyBuild.aim(player, (float) Math.toDegrees(Math.atan2(-dx, dz)), 0);
        forward = true;
        sprint = EscapeLogic.sprint(player.getFoodData().getFoodLevel());
        if (rise[heading] > 0) jump = true;  // a step up needs a jump at any pace
        else {
            // sprint-jump only over level ground: the landing is two cells on
            boolean room = geometry.clear(player.getX(), player.getZ(), y + 1.8, y + 1.8 + JUMP_ROOM)
                    && geometry.clear(bx + dx + .5, bz + dz + .5, y + 1.8, y + 1.8 + JUMP_ROOM);
            boolean level = rise[heading] == 0
                    && geometry.standable(new RouteMotor.Point(bx + 2 * dx, y, bz + 2 * dz));
            jump = EscapeLogic.jump(true, sprint, room, level);
        }
        if (jump) jumps++;
        return null;
    }

    /** The feet offset of the safe cell that way (level first, a step up, then drops), or null. */
    private static Integer nextCell(RouteGeometry geometry, RouteMotor.Pose pose, int cx, int cz, double y, boolean diagonal) {
        for (int offset : FightLogic.STEP_OFFSETS) {
            var cell = new RouteMotor.Point(cx, y + offset, cz);
            if (!geometry.standable(cell)) continue;
            boolean ok = diagonal
                    ? offset == 0 && geometry.safeDiagonal(pose, cell, y, .6, false, FightLogic.MAX_DROP)
                    : geometry.safe(pose, cell, y, .6, offset > 0, FightLogic.MAX_DROP);
            if (ok) return offset;
        }
        return null;
    }

    @Override public BodyRuntime.Controls controls(ServerPlayer player, int tick) {
        if (!forward && !jump) return null;
        return new BodyRuntime.Controls("native-escape", tick, tick, tick + 2, 2, forward ? 1 : 0, 0, 0, 0, jump,
                false, sprint && forward, false, false, false, false, player.getInventory().getSelectedSlot());
    }

    @Override public Map<String, Object> result(ServerPlayer player) {
        var out = new LinkedHashMap<String, Object>();
        out.put("end", end);
        out.put("ticks", ticks);
        out.put("jumps", jumps);
        out.put("distance", Math.hypot(player.getX() - startX, player.getZ() - startZ));
        out.put("nearest", Double.isFinite(nearest) ? nearest : null);
        out.put("health", player.getHealth());
        return out;
    }
}
