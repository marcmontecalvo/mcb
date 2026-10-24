package com.vulpine.mclab.mixin;

import com.vulpine.mclab.BridgeRuntime;
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
    @Unique private String mclab$beforeBlock;

    @Inject(method = "handleBlockBreakAction", at = @At("HEAD"))
    private void onDig(BlockPos pos, ServerboundPlayerActionPacket.Action action,
                       Direction face, int height, int sequence, CallbackInfo ci) {
        BridgeRuntime.interaction(player, "dig_packet", () -> Map.of(
                "action", action.toString(), "position", List.of(pos.getX(), pos.getY(), pos.getZ()),
                "face", face.toString(), "client_sequence", sequence));
    }

    @Inject(method = "destroyBlock", at = @At("HEAD"), cancellable = true)
    private void beforeBreak(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        if (!BridgeRuntime.onServerThread()) return;
        if (!BridgeRuntime.allowBreak(player,pos)) { cir.setReturnValue(false); return; }
        BridgeRuntime.beforeCourseBreak(player,pos);
        mclab$beforeBlock = BuiltInRegistries.BLOCK.getKey(player.level().getBlockState(pos).getBlock()).toString();
    }

    @Inject(method = "destroyBlock", at = @At("RETURN"))
    private void afterBreak(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        BridgeRuntime.afterCourseBreak(player,pos,cir.getReturnValue());
        if (!cir.getReturnValue() || mclab$beforeBlock == null) return;
        BridgeRuntime.interaction(player, "block_broken", () -> Map.of(
                "block", mclab$beforeBlock, "position", List.of(pos.getX(), pos.getY(), pos.getZ())));
    }
}
