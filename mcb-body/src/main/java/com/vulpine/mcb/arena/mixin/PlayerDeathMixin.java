package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaLogic;
import com.vulpine.mcb.arena.ArenaRuntime;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * A player dying inside a registered arena (or in a running match) never drops items or experience onto the floor:
 * their inventory is cleared unless the match has {@code keep_inventory}, in which case it stays intact for respawn.
 */
@Mixin(Player.class)
public abstract class PlayerDeathMixin {
    @Inject(method = "dropEquipment", at = @At("HEAD"), cancellable = true)
    private void mcbarena$noDeathDrops(ServerLevel level, CallbackInfo ci) {
        if (!((Object) this instanceof ServerPlayer self)) return;
        var action = ArenaRuntime.deathInventory(self);
        if (action == ArenaLogic.DeathInventory.DROP) return;
        if (action == ArenaLogic.DeathInventory.CLEAR) {
            self.getInventory().clearContent();
            self.containerMenu.setCarried(ItemStack.EMPTY);
        }
        ci.cancel();
    }

    @Inject(method = "getBaseExperienceReward", at = @At("HEAD"), cancellable = true)
    private void mcbarena$noDeathXp(ServerLevel level, CallbackInfoReturnable<Integer> cir) {
        if ((Object) this instanceof ServerPlayer self && ArenaRuntime.suppressDeathExperience(self)) {
            cir.setReturnValue(0);
        }
    }
}
