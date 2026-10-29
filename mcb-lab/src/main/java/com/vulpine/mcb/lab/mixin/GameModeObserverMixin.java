package com.vulpine.mcb.lab.mixin;

import com.vulpine.mcb.lab.LabRuntime;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.protocol.game.ServerboundPlayerActionPacket;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.level.ServerPlayerGameMode;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import java.util.List;
import java.util.Map;

@Mixin(ServerPlayerGameMode.class)
public abstract class GameModeObserverMixin {
    @Shadow @Final protected ServerPlayer player;
    @Unique private String mcblab$beforeBlock;

    @Inject(method = "handleBlockBreakAction", at = @At("HEAD"))
    private void onDig(BlockPos pos, ServerboundPlayerActionPacket.Action action,
                       Direction face, int height, int sequence, CallbackInfo ci) {
        LabRuntime.interaction(player, "dig_packet", () -> Map.of(
                "action", action.toString(), "position", List.of(pos.getX(), pos.getY(), pos.getZ()),
                "face", face.toString(), "client_sequence", sequence));
    }

    @Inject(method = "destroyBlock", at = @At("HEAD"), cancellable = true)
    private void beforeBreak(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        if (!LabRuntime.onServerThread()) return;
        if (!LabRuntime.allowBreak(player,pos)) { cir.setReturnValue(false); return; }
        LabRuntime.beforeCourseBreak(player,pos);
        mcblab$beforeBlock = BuiltInRegistries.BLOCK.getKey(player.level().getBlockState(pos).getBlock()).toString();
    }

    @Inject(method = "destroyBlock", at = @At("RETURN"))
    private void afterBreak(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        LabRuntime.afterCourseBreak(player,pos,cir.getReturnValue());
        if (!cir.getReturnValue() || mcblab$beforeBlock == null) return;
        LabRuntime.interaction(player, "block_broken", () -> Map.of(
                "block", mcblab$beforeBlock, "position", List.of(pos.getX(), pos.getY(), pos.getZ())));
    }
}
