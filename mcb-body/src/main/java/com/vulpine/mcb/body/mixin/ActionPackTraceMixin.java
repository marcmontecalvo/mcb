package com.vulpine.mcb.body.mixin;

import com.vulpine.mcb.body.BodyRuntime;
import net.minecraft.server.level.ServerPlayer;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** String target keeps Carpet implementation types confined to CarpetBodyAdapter. */
@Mixin(targets = "carpet.helpers.EntityPlayerActionPack", remap = false)
public abstract class ActionPackTraceMixin {
    @Shadow @Final private ServerPlayer player;

    @Inject(method = "onUpdate", at = @At("HEAD"), remap = false)
    private void beforeUpdate(CallbackInfo ci) {
        BodyRuntime.trace(player, "carpet_action_head");
    }

    @Inject(method = "onUpdate", at = @At("TAIL"), remap = false)
    private void afterUpdate(CallbackInfo ci) {
        BodyRuntime.trace(player, "carpet_action_tail");
    }
}
