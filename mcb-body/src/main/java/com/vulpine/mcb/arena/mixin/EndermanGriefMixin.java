package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaGuard;
import net.minecraft.world.entity.monster.Enderman;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** An enderman inside a registered arena neither picks up nor puts down a block. */
@Mixin(targets = {"net.minecraft.world.entity.monster.Enderman$EndermanTakeBlockGoal",
        "net.minecraft.world.entity.monster.Enderman$EndermanLeaveBlockGoal"})
public abstract class EndermanGriefMixin {
    @Shadow @Final private Enderman enderman;

    @Inject(method = "canUse", at = @At("HEAD"), cancellable = true)
    private void mcbarena$noCarrying(CallbackInfoReturnable<Boolean> cir) {
        if (ArenaGuard.protectedPoint(enderman.level(), enderman.getX(), enderman.getY(), enderman.getZ())) cir.setReturnValue(false);
    }
}
