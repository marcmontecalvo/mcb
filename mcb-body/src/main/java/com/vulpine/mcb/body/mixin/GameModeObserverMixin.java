package com.vulpine.mcb.body.mixin;

import com.vulpine.mcb.body.BodyRuntime;
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

/** Reports this mod's bodies' digging and breaks. Never gates a break (that is the lab's job). */
@Mixin(ServerPlayerGameMode.class)
public abstract class GameModeObserverMixin {
    @Shadow @Final protected ServerPlayer player;
    @Unique private String mcbbody$beforeBlock;

    @Inject(method = "handleBlockBreakAction", at = @At("HEAD"))
    private void onDig(BlockPos pos, ServerboundPlayerActionPacket.Action action,
                       Direction face, int height, int sequence, CallbackInfo ci) {
        BodyRuntime.interaction(player, "dig_packet", () -> Map.of(
                "action", action.toString(), "position", List.of(pos.getX(), pos.getY(), pos.getZ()),
                "face", face.toString(), "client_sequence", sequence));
    }

    @Inject(method = "destroyBlock", at = @At("HEAD"))
    private void beforeBreak(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        mcbbody$beforeBlock = BuiltInRegistries.BLOCK.getKey(player.level().getBlockState(pos).getBlock()).toString();
    }

    @Inject(method = "destroyBlock", at = @At("RETURN"))
    private void afterBreak(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        if (!cir.getReturnValue() || mcbbody$beforeBlock == null) return;
        BodyRuntime.interaction(player, "block_broken", () -> Map.of(
                "block", mcbbody$beforeBlock, "position", List.of(pos.getX(), pos.getY(), pos.getZ())));
    }
}
