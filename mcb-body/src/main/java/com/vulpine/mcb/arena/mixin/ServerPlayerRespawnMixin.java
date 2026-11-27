package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaRuntime;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.gamerules.GameRules;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Restores a player's inventory and XP on respawn when they died in a {@code keep_inventory} arena match. */
@Mixin(ServerPlayer.class)
public abstract class ServerPlayerRespawnMixin {
    @Shadow
    private void transferInventoryXpAndScore(Player oldPlayer) {
        throw new AssertionError();
    }

    @Inject(method = "restoreFrom", at = @At("RETURN"))
    private void mcbarena$restoreKeptInventory(ServerPlayer oldPlayer, boolean alive, CallbackInfo ci) {
        if (alive) return;
        var self = (ServerPlayer) (Object) this;
        boolean kept = ArenaRuntime.consumeKeepOnRespawn(oldPlayer.getUUID());
        if (kept && !self.level().getGameRules().get(GameRules.KEEP_INVENTORY) && !oldPlayer.isSpectator()) {
            this.transferInventoryXpAndScore(oldPlayer);
        }
    }
}
