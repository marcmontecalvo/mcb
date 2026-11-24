package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaGuard;
import java.util.ArrayList;
import java.util.List;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.ServerExplosion;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** An explosion destroys no block of a registered arena (nor sets fire there: fire is placed only on blocks it blows). */
@Mixin(ServerExplosion.class)
public abstract class ExplosionMixin {
    @Shadow @Final private ServerLevel level;

    @Inject(method = "calculateExplodedPositions", at = @At("RETURN"), cancellable = true)
    private void mcbarena$keepArena(CallbackInfoReturnable<List<BlockPos>> cir) {
        var positions = cir.getReturnValue();
        if (positions == null || positions.isEmpty()) return;
        var kept = new ArrayList<BlockPos>(positions.size());
        for (var pos : positions) if (!ArenaGuard.protectedCell(level, pos)) kept.add(pos);
        if (kept.size() != positions.size()) cir.setReturnValue(kept);
    }
}
