package com.vulpine.mcb.lab.mixin;
import com.vulpine.mcb.lab.LabRuntime;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Player;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
@Mixin(ItemEntity.class)
public abstract class ItemEpisodeMixin {
    @Unique private int mcblab$beforeCount;
    @Unique private String mcblab$itemKind;
    @Inject(method="playerTouch",at=@At("HEAD"))
    private void beforePickup(Player player, CallbackInfo ci) {
        mcblab$beforeCount=((ItemEntity)(Object)this).getItem().getCount();
        mcblab$itemKind=net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(
                ((ItemEntity)(Object)this).getItem().getItem()).toString();
    }
    // Native playerTouch restores the discarded stack count before RETURN.
    // Player.take is reached only after Inventory.add succeeds, while the
    // remaining count still reflects the actual normal inventory transfer.
    @Inject(method="playerTouch",at=@At(value="INVOKE",
            target="Lnet/minecraft/world/entity/player/Player;take(Lnet/minecraft/world/entity/Entity;I)V"))
    private void afterPickup(Player player, CallbackInfo ci) {
        if (player instanceof ServerPlayer p) {
            var item=(ItemEntity)(Object)this;
            LabRuntime.coursePickup(p,item,mcblab$beforeCount,item.getItem().getCount(),mcblab$itemKind);
        }
    }
}
