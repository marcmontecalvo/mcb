package com.vulpine.mcb.body;

import com.google.gson.JsonObject;

import java.util.EnumSet;
import java.util.Set;

/** Which first-poll reset steps run for a body. keep_state keeps what Carpet restored from playerdata. */
final class BodyReset {
    enum Step {
        STOP_RIDING, SURVIVAL_MODE, ABILITIES, NOT_INVULNERABLE, STANDING_POSE, MOTION_AND_POSITION,
        CLEAR_INVENTORY, CLEAR_EQUIPMENT, CLEAR_EFFECTS, RESET_ATTRIBUTES, RESET_HEALTH_AND_FOOD,
        CLEAR_FIRE_AND_FREEZE, SELECT_FIRST_SLOT
    }

    private BodyReset() {}

    /** Steps that always run: they make the body controllable. */
    private static final Set<Step> CONTROLLABLE = EnumSet.of(Step.STOP_RIDING, Step.SURVIVAL_MODE, Step.ABILITIES,
            Step.NOT_INVULNERABLE, Step.STANDING_POSE, Step.MOTION_AND_POSITION);

    static Set<Step> steps(boolean keepState) {
        var steps = EnumSet.copyOf(CONTROLLABLE);
        if (!keepState) steps.addAll(EnumSet.complementOf(EnumSet.copyOf(CONTROLLABLE)));
        return steps;
    }

    /** Absent means false; anything but a JSON boolean is rejected. */
    static boolean parseKeepState(JsonObject request) {
        if (!request.has("keep_state")) return false;
        var value = request.get("keep_state");
        if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isBoolean())
            throw new IllegalArgumentException("invalid_keep_state");
        return value.getAsBoolean();
    }
}
