package com.vulpine.mclab.mixin;
import com.vulpine.mclab.BridgeRuntime;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Player;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
@Mixin(ItemEntity.class)
public abstract class ItemEpisodeMixin {
    @Unique private int mclab$beforeCount;
    @Inject(method="playerTouch",at=@At("HEAD"))
    private void beforePickup(Player player, CallbackInfo ci) {
        mclab$beforeCount=((ItemEntity)(Object)this).getItem().getCount();
    }
    // Native playerTouch restores the discarded stack count before RETURN.
    // Player.take is reached only after Inventory.add succeeds, while the
    // remaining count still reflects the actual normal inventory transfer.
    @Inject(method="playerTouch",at=@At(value="INVOKE",
            target="Lnet/minecraft/world/entity/player/Player;take(Lnet/minecraft/world/entity/Entity;I)V"))
    private void afterPickup(Player player, CallbackInfo ci) {
        if (player instanceof ServerPlayer p) {
            var item=(ItemEntity)(Object)this;
            BridgeRuntime.coursePickup(p,item,mclab$beforeCount,item.getItem().getCount());
        }
    }
}
