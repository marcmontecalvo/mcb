package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaGuard;
import com.vulpine.mcb.arena.ArenaLogic;
import java.util.concurrent.CompletableFuture;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.PlayerSpawnFinder;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.phys.Vec3;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * A player who joins, or respawns without a bed, at a world spawn inside a built arena (the arena server's lobby)
 * starts on that cell, as the bot does. The game would search the spawn's column from the top down and stand them on
 * the lobby's roof: outside adventure mode it takes the highest block, and the spawn radius does not change that.
 * The cell is an arena entry, checked to stand on a floor with headroom when the plan was validated.
 */
@Mixin(PlayerSpawnFinder.class)
public abstract class SpawnFinderMixin {
    @Inject(method = "findSpawn", at = @At("HEAD"), cancellable = true)
    private static void mcbarena$exactLobbySpawn(ServerLevel level, BlockPos pos, CallbackInfoReturnable<CompletableFuture<Vec3>> cir) {
        var arena = ArenaGuard.arenaAt(level, pos);
        if (ArenaLogic.keepsSpawnExact(arena == null ? null : arena.state())) {
            cir.setReturnValue(CompletableFuture.completedFuture(Vec3.atBottomCenterOf(pos)));
        }
    }
}
