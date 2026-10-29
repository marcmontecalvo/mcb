package com.vulpine.mcb.lab.mixin;

import com.vulpine.mcb.lab.LabRuntime;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.LivingEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Observe health loss before fake-player death handling can restore/discard it. */
@Mixin(LivingEntity.class)
public abstract class NaturalHealthMixin {
    @Inject(method="setHealth",at=@At("HEAD"))
    private void naturalHealth(float health,CallbackInfo ci) {
        if((Object)this instanceof ServerPlayer player) {
            LabRuntime.naturalHealthChanged(player,player.getHealth(),
                    Math.max(0,Math.min(health,player.getMaxHealth())));
        }
    }
}
