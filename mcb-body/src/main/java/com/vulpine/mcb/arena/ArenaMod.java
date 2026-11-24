package com.vulpine.mcb.arena;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import org.slf4j.LoggerFactory;

/** The arena runtime's entry point: protection events from the start, the registry once the server is up. */
public final class ArenaMod implements ModInitializer {
    @Override
    public void onInitialize() {
        ArenaGuard.register();
        ServerLifecycleEvents.SERVER_STARTED.register(ArenaRuntime::start);
        ServerTickEvents.END_SERVER_TICK.register(server -> {
            var runtime = ArenaRuntime.active();
            if (runtime != null) runtime.tick();
        });
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> {
            var runtime = ArenaRuntime.active();
            if (runtime != null) runtime.close();
        });
        LoggerFactory.getLogger("mcb-arena").info("MCB arena runtime registered");
    }
}
