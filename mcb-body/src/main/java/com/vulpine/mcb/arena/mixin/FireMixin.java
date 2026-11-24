package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaGuard;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.util.RandomSource;
import net.minecraft.world.level.block.FireBlock;
import net.minecraft.world.level.block.state.BlockState;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Fire neither spreads nor burns blocks in or next to a registered arena. Fire inside stands still (a plan's own fire
 * stays); fire outside but within spreading reach is put out, so it cannot burn into the arena.
 */
@Mixin(FireBlock.class)
public abstract class FireMixin {
    @Inject(method = "tick", at = @At("HEAD"), cancellable = true)
    private void mcbarena$noSpread(BlockState state, ServerLevel level, BlockPos pos, RandomSource random, CallbackInfo ci) {
        if (ArenaGuard.protectedCell(level, pos)) { ci.cancel(); return; }
        if (ArenaGuard.nearArena(level, pos, ArenaGuard.FIRE_REACH)) {
            level.removeBlock(pos, false);
            ci.cancel();
        }
    }
}
