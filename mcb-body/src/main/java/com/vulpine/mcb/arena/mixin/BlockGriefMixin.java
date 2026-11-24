package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaGuard;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.level.Level;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** A mob (a ravager on leaves, a wither, a silverfish waking friends) breaks no block of a registered arena. */
@Mixin(Level.class)
public abstract class BlockGriefMixin {
    @Inject(method = "destroyBlock(Lnet/minecraft/core/BlockPos;ZLnet/minecraft/world/entity/Entity;I)Z", at = @At("HEAD"), cancellable = true)
    private void mcbarena$noMobBreaking(BlockPos pos, boolean drop, Entity breaker, int limit, CallbackInfoReturnable<Boolean> cir) {
        if (breaker instanceof Mob && ArenaGuard.protectedCell((Level) (Object) this, pos)) cir.setReturnValue(false);
    }
}
