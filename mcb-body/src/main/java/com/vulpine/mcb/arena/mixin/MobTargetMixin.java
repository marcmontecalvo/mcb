package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaRuntime;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Guards {@link Mob#setTarget(LivingEntity)} so arena allied mobs never target teammates or non-participants, and
 * neutral wave mobs never target non-participants outside the match.
 */
@Mixin(Mob.class)
public abstract class MobTargetMixin {
    @Inject(method = "setTarget", at = @At("HEAD"), cancellable = true)
    private void mcbarena$guardTarget(LivingEntity target, CallbackInfo ci) {
        if (target == null) return;
        var self = (Mob) (Object) this;
        if (!ArenaRuntime.canMobTarget(self, target)) {
            ci.cancel();
        }
    }
}
