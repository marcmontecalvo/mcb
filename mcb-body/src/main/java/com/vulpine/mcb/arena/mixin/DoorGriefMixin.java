package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaGuard;
import net.minecraft.world.entity.ai.goal.BreakDoorGoal;
import net.minecraft.world.entity.ai.goal.DoorInteractGoal;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** A zombie inside a registered arena does not break a door down. (Extends the goal's parent to reach its {@code mob}.) */
@Mixin(BreakDoorGoal.class)
public abstract class DoorGriefMixin extends DoorInteractGoal {
    private DoorGriefMixin() { super(null); }

    @Inject(method = "canUse", at = @At("HEAD"), cancellable = true)
    private void mcbarena$noBreaking(CallbackInfoReturnable<Boolean> cir) {
        if (ArenaGuard.protectedPoint(mob.level(), mob.getX(), mob.getY(), mob.getZ())) cir.setReturnValue(false);
    }
}
