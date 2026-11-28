package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaLobby;
import com.vulpine.mcb.arena.ArenaLobbyMenu;
import net.minecraft.network.protocol.common.ServerboundCustomClickActionPacket;
import net.minecraft.server.network.ServerCommonPacketListenerImpl;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * The lobby menu's {@code mcb:menu} dialog clicks, with the player who sent them ({@code MinecraftServer.handleCustomClickAction}
 * is told no player). The hook sits where vanilla hands the click to the server, after {@code ensureRunningOnSameThread}
 * has moved the packet onto the server thread; other ids, and clicks from the configuration phase, go on to vanilla.
 */
@Mixin(ServerCommonPacketListenerImpl.class)
public abstract class CustomClickMixin {
    @Inject(method = "handleCustomClickAction",
            at = @At(value = "INVOKE",
                    target = "Lnet/minecraft/server/MinecraftServer;handleCustomClickAction(Lnet/minecraft/resources/Identifier;Ljava/util/Optional;)V"),
            cancellable = true)
    private void mcbarena$menuClick(ServerboundCustomClickActionPacket packet, CallbackInfo ci) {
        if (!((Object) this instanceof ServerGamePacketListenerImpl game)) return;
        if (!ArenaLobbyMenu.CLICK_ID.equals(packet.id().toString())) return;
        ci.cancel();
        ArenaLobby.clicked(game.player, packet.payload());
    }
}
