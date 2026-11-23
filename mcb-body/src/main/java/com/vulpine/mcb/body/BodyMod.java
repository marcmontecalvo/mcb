package com.vulpine.mcb.body;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.message.v1.ServerMessageEvents;
import org.slf4j.LoggerFactory;

import java.io.IOException;

/** mcb-body: the Carpet body's nervous system. Independent of mcb-lab. */
public final class BodyMod implements ModInitializer {
    private BodyRuntime runtime;

    @Override
    public void onInitialize() {
        CommandRegistrationCallback.EVENT.register((dispatcher, registry, environment) -> McbCommand.register(dispatcher));
        ServerMessageEvents.CHAT_MESSAGE.register((message, sender, params) ->
                BodyRuntime.playerChat(sender, message.signedContent()));
        ServerLivingEntityEvents.AFTER_DEATH.register((entity, source) -> BodyRuntime.bodyDied(entity));
        ServerLifecycleEvents.SERVER_STARTED.register(server -> {
            try { runtime = new BodyRuntime(server); }
            catch (IOException error) { throw new IllegalStateException("Cannot bind body bridge", error); }
        });
        ServerTickEvents.START_SERVER_TICK.register(server -> { if (runtime != null) runtime.preTick(); });
        ServerTickEvents.END_SERVER_TICK.register(server -> { if (runtime != null) runtime.postTick(); });
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> { if (runtime != null) runtime.close(); });
        LoggerFactory.getLogger("mcb-body").info("MCB body bridge loaded");
    }
}
