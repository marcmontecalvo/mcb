package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaRuntime;
import java.util.ArrayDeque;
import java.util.Optional;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Instant health from a potion (drunk, splashed or from a lingering cloud): the heal inside it is credited to whoever
 * drank or threw it (the indirect source, else the direct one), not to the healed body itself. The healer before is put
 * back after, so a nested effect cannot leave the wrong one set.
 */
@Mixin(targets = "net.minecraft.world.effect.HealOrHarmMobEffect")  // package-private: named, not referenced
public abstract class HealEffectMixin {
    @Unique private static final ThreadLocal<ArrayDeque<Optional<Entity>>> mcbarena$before = ThreadLocal.withInitial(ArrayDeque::new);

    @Inject(method = "applyInstantaneousEffect", at = @At("HEAD"))
    private void mcbarena$from(ServerLevel level, Entity source, Entity indirect, LivingEntity target, int amplifier, double health,
                               CallbackInfo ci) {
        mcbarena$before.get().push(Optional.ofNullable(ArenaRuntime.healing(indirect != null ? indirect : source)));
    }

    @Inject(method = "applyInstantaneousEffect", at = @At("RETURN"))
    private void mcbarena$done(ServerLevel level, Entity source, Entity indirect, LivingEntity target, int amplifier, double health,
                               CallbackInfo ci) {
        var stack = mcbarena$before.get();
        ArenaRuntime.healing(stack.isEmpty() ? null : stack.pop().orElse(null));
    }
}
