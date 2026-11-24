package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaRuntime;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.LivingEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * The arena's damage and healing stats. A hit is measured as the health and absorption the entity really lost across
 * hurtServer (armour, enchantments, a blocked blow and overkill already counted in), so a killing blow counts only what
 * was left; a heal as what heal really gives back (capped at what was missing). Players' hurtServer reaches this one.
 */
@Mixin(LivingEntity.class)
public abstract class LivingStatsMixin {
    @Unique private float mcbarena$before;
    @Unique private int mcbarena$depth;

    @Inject(method = "hurtServer", at = @At("HEAD"))
    private void mcbarena$beforeHurt(ServerLevel level, DamageSource source, float amount, CallbackInfoReturnable<Boolean> cir) {
        var self = (LivingEntity) (Object) this;
        if (mcbarena$depth++ == 0) mcbarena$before = self.getHealth() + self.getAbsorptionAmount();
    }

    @Inject(method = "hurtServer", at = @At("RETURN"))
    private void mcbarena$afterHurt(ServerLevel level, DamageSource source, float amount, CallbackInfoReturnable<Boolean> cir) {
        if (--mcbarena$depth != 0) return;
        var self = (LivingEntity) (Object) this;
        float lost = mcbarena$before - Math.max(0f, self.getHealth()) - self.getAbsorptionAmount();
        if (lost > 0) ArenaRuntime.hurt(self, source, lost);
    }

    @Inject(method = "heal", at = @At("HEAD"))
    private void mcbarena$heal(float amount, CallbackInfo ci) {
        var self = (LivingEntity) (Object) this;
        if (!(self.level() instanceof ServerLevel)) return;
        float health = self.getHealth();
        if (health <= 0 || amount <= 0) return;
        float gained = Math.min(amount, self.getMaxHealth() - health);
        if (gained > 0) ArenaRuntime.healed(self, gained);
    }
}
