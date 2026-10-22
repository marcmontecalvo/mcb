package com.vulpine.mclab;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.MinecraftServer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** G0 evidence only. Verification is incremental on-thread; disk writes are off-thread. */
public final class BootstrapMod implements ModInitializer {
    private static final Logger LOG = LoggerFactory.getLogger("mclab");
    private static final Gson GSON = new Gson();
    private final ThreadPoolExecutor writer = new ThreadPoolExecutor(
            1, 1, 0, TimeUnit.SECONDS, new ArrayBlockingQueue<>(4), runnable -> {
                Thread thread = new Thread(runnable, "mclab-evidence");
                thread.setDaemon(true);
                return thread;
            }, new ThreadPoolExecutor.AbortPolicy());
    private JsonObject spec;
    private int cursor;
    private int mismatches;
    private int ox;
    private int oy;
    private int oz;
    private final List<String> examples = new ArrayList<>();
    private boolean verified;
    private int previousPlayers = -1;

    public record PlayerSnapshot(String name, String uuid, double x, double y, double z) {}
    public record Evidence(int schema_version, String launch_id, String phase, String course_id, String spec_hash,
                           int server_tick, int checked_blocks, int mismatches,
                           List<String> examples, List<PlayerSnapshot> players,
                           boolean human_inspection_confirmed) {}

    @Override
    public void onInitialize() {
        // Mod initialization precedes simulation; no running tick is blocked on file I/O.
        try {
            spec = JsonParser.parseString(Files.readString(
                    Path.of("world/datapacks/mclab/evaluator/active.json"))).getAsJsonObject();
            var origin = spec.getAsJsonArray("origin");
            ox = origin.get(0).getAsInt();
            oy = origin.get(1).getAsInt();
            oz = origin.get(2).getAsInt();
        } catch (IOException | RuntimeException error) {
            throw new IllegalStateException("Missing/invalid generated active course", error);
        }
        ServerTickEvents.END_SERVER_TICK.register(this::tick);
        ServerLifecycleEvents.SERVER_STOPPED.register(server -> writer.shutdown());
        LOG.info("MCLab bootstrap verifier loaded; body/scoring are not implemented");
    }

    private void tick(MinecraftServer server) {
        // Datapack initializes after 4 seconds; warm up before validating geometry.
        if (server.getTickCount() < 160) return;
        var level = server.overworld();
        if (!verified) {
            int count = 49 * 41 * 49;
            for (int budget = 0; budget < 2048 && cursor < count; budget++, cursor++) {
                int x = cursor % 49 - 24;
                int z = (cursor / 49) % 49 - 24;
                int y = cursor / (49 * 49) - 20;
                var pos = new BlockPos(ox + x, oy + y, oz + z);
                if (!level.getChunkSource().hasChunk(pos.getX() >> 4, pos.getZ() >> 4)) return;
                String expected = "minecraft:air";
                for (var element : spec.getAsJsonArray("geometry")) {
                    var geometry = element.getAsJsonObject();
                    var a = geometry.getAsJsonArray("from");
                    var b = geometry.getAsJsonArray("to");
                    if (x >= a.get(0).getAsInt() && x <= b.get(0).getAsInt()
                            && y >= a.get(1).getAsInt() && y <= b.get(1).getAsInt()
                            && z >= a.get(2).getAsInt() && z <= b.get(2).getAsInt()) {
                        expected = geometry.get("block").getAsString();
                    }
                }
                var state = level.getBlockState(pos);
                String observed = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
                if (!observed.equals(expected)) {
                    mismatches++;
                    if (examples.size() < 10) examples.add(pos + ": " + observed + " != " + expected);
                }
            }
            if (cursor == count) {
                verified = true;
                LOG.info("MCLab arena verification: {} blocks, {} mismatches", count, mismatches);
                publish(server, "arena_verified");
            }
        }
        int players = server.getPlayerList().getPlayers().size();
        if (verified && players != previousPlayers) {
            previousPlayers = players;
            publish(server, "player_count_changed");
        }
    }

    private void publish(MinecraftServer server, String phase) {
        // Copy values on-thread; never retain player/world objects in the writer.
        var players = server.getPlayerList().getPlayers().stream()
                .map(p -> new PlayerSnapshot(p.getName().getString(), p.getUUID().toString(),
                        p.getX(), p.getY(), p.getZ())).toList();
        var evidence = new Evidence(1, System.getProperty("mclab.launch_id", "unmanaged"),
                phase, spec.get("course_id").getAsString(),
                spec.get("spec_hash").getAsString(), server.getTickCount(), cursor,
                mismatches, List.copyOf(examples), players, false);
        writer.execute(() -> {
            try {
                Path destination = Path.of("mclab-bootstrap-evidence.json");
                Path temporary = Path.of("mclab-bootstrap-evidence.json.tmp");
                Files.writeString(temporary, GSON.toJson(evidence));
                Files.move(temporary, destination, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            } catch (IOException error) {
                LOG.error("Cannot persist bootstrap evidence", error);
            }
        });
    }
}
