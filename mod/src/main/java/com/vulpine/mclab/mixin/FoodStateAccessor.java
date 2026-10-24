package com.vulpine.mclab.mixin;

import net.minecraft.world.food.FoodData;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** Pinned 26.3 hunger fields needed for authoritative reset fingerprints. */
@Mixin(FoodData.class)
public interface FoodStateAccessor {
    @Accessor("exhaustionLevel") float mclab$getExhaustion();
    @Accessor("tickTimer") int mclab$getTickTimer();
}
