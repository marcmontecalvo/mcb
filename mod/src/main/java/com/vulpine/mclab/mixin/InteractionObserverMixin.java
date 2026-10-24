package com.vulpine.mclab.mixin;

import com.vulpine.mclab.BridgeRuntime;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.protocol.game.ServerboundAttackPacket;
import net.minecraft.network.protocol.game.ServerboundUseItemOnPacket;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import net.minecraft.world.entity.LivingEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.List;
import java.util.Map;

@Mixin(ServerGamePacketListenerImpl.class)
public abstract class InteractionObserverMixin {
    @Shadow public ServerPlayer player;

    private void use(ServerboundUseItemOnPacket packet, String phase) {
        BridgeRuntime.interaction(player, phase, () -> {
            var hit = packet.hitResult();
            var pos = hit.getBlockPos();
            var placed = pos.relative(hit.getDirection());
            var stack = player.getItemInHand(packet.hand());
            return Map.of("client_sequence", packet.sequence(), "face", hit.getDirection().toString(),
                    "position", List.of(pos.getX(), pos.getY(), pos.getZ()),
                    "held_item", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(),
                    "held_count", stack.getCount(), "adjacent_block",
                    BuiltInRegistries.BLOCK.getKey(player.level().getBlockState(placed).getBlock()).toString());
        });
    }

    @Inject(method = "handleUseItemOn", at = @At("HEAD"))
    private void beforeUse(ServerboundUseItemOnPacket packet, CallbackInfo ci) { use(packet, "use_packet"); }

    @Inject(method = "handleUseItemOn", at = @At("RETURN"))
    private void afterUse(ServerboundUseItemOnPacket packet, CallbackInfo ci) { use(packet, "use_result"); }

    private void attack(ServerboundAttackPacket packet, String phase) {
        BridgeRuntime.interaction(player, phase, () -> {
            var entity = player.level().getEntity(packet.entityId());
            if (entity == null) return Map.of("target_present", false);
            return Map.of("target_present", true, "uuid", entity.getUUID().toString(),
                    "position", List.of(entity.getX(), entity.getY(), entity.getZ()),
                    "health", entity instanceof LivingEntity living ? living.getHealth() : -1,
                    "attack_strength", player.getAttackStrengthScale(0),
                    "within_entity_range", player.isWithinEntityInteractionRange(entity, 0));
        });
    }

    @Inject(method = "handleAttack", at = @At("HEAD"))
    private void beforeAttack(ServerboundAttackPacket packet, CallbackInfo ci) { attack(packet, "attack_packet"); }

    @Inject(method = "handleAttack", at = @At("RETURN"))
    private void afterAttack(ServerboundAttackPacket packet, CallbackInfo ci) { attack(packet, "attack_result"); }
}
