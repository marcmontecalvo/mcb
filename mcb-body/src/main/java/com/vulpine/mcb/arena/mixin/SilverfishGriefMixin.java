package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaGuard;
import net.minecraft.world.entity.ai.goal.RandomStrollGoal;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** A silverfish inside a registered arena does not hide in a block. (Extends the goal's parent to reach its {@code mob}.) */
@Mixin(targets = "net.minecraft.world.entity.monster.Silverfish$SilverfishMergeWithStoneGoal")
public abstract class SilverfishGriefMixin extends RandomStrollGoal {
    private SilverfishGriefMixin() { super(null, 0.0); }

    @Inject(method = "canUse", at = @At("HEAD"), cancellable = true)
    private void mcbarena$noMerging(CallbackInfoReturnable<Boolean> cir) {
        if (ArenaGuard.protectedPoint(mob.level(), mob.getX(), mob.getY(), mob.getZ())) cir.setReturnValue(false);
    }
}
