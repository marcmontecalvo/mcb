package com.vulpine.mcb.arena;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Every registered arena, persisted as one JSON file under the world folder (mcb_arenas.json), so protection and the
 * arena list survive a restart. Server thread only. Saves are atomic: a temp file, then a move over the real one.
 */
public final class ArenaRegistry {
    static final int SCHEMA = 1;

    private final Path file;
    private final Map<String, ArenaRecord> arenas = new LinkedHashMap<>();

    private ArenaRegistry(Path file) { this.file = file; }

    /** Reads the file (a missing one is an empty registry); a damaged or unknown-schema file stops the load loudly. */
    public static ArenaRegistry load(Path file) {
        var registry = new ArenaRegistry(file);
        if (!Files.exists(file)) return registry;
        try {
            var root = JsonParser.parseString(Files.readString(file, StandardCharsets.UTF_8)).getAsJsonObject();
            if (!root.has("schema") || root.get("schema").getAsInt() != SCHEMA)
                throw new IllegalStateException(file.getFileName() + ": unknown schema " + root.get("schema"));
            for (var e : root.getAsJsonArray("arenas")) {
                var record = ArenaRecord.fromJson(e.getAsJsonObject());
                registry.arenas.put(record.name(), record);
            }
        } catch (IOException | RuntimeException error) {
            if (error instanceof IllegalStateException state && state.getMessage().contains("schema")) throw state;
            throw new IllegalStateException("Cannot read " + file.getFileName() + " (mcb_arenas): " + error, error);
        }
        return registry;
    }

    public List<ArenaRecord> all() { return List.copyOf(arenas.values()); }

    public ArenaRecord get(String name) { return arenas.get(name); }

    /** Names the arena that overlaps the box in that dimension, else null. */
    public String overlapping(String dimension, ArenaBox box) {
        for (var a : arenas.values()) if (a.dimension().equals(dimension) && a.box().intersects(box)) return a.name();
        return null;
    }

    public void add(ArenaRecord record) {
        if (arenas.containsKey(record.name())) throw new ArenaFail("name_exists", record.name());
        String clash = overlapping(record.dimension(), record.box());
        if (clash != null) throw new ArenaFail("overlap", "overlaps arena " + clash);
        arenas.put(record.name(), record);
    }

    public ArenaRecord remove(String name) { return arenas.remove(name); }

    /** The arena holding this block cell, else null. */
    public ArenaRecord at(String dimension, int x, int y, int z) {
        if (arenas.isEmpty()) return null;
        for (var a : arenas.values()) if (a.box().contains(x, y, z) && a.dimension().equals(dimension)) return a;
        return null;
    }

    /** The arena holding this entity position, else null. */
    public ArenaRecord atPoint(String dimension, double x, double y, double z) {
        if (arenas.isEmpty()) return null;
        for (var a : arenas.values()) if (a.box().containsPoint(x, y, z) && a.dimension().equals(dimension)) return a;
        return null;
    }

    public void save() {
        var root = new JsonObject();
        root.addProperty("schema", SCHEMA);
        var list = new JsonArray();
        for (var a : arenas.values()) list.add(a.toJson());
        root.add("arenas", list);
        var temp = file.resolveSibling(file.getFileName() + ".tmp");
        try {
            Files.writeString(temp, new GsonBuilder().setPrettyPrinting().create().toJson(root), StandardCharsets.UTF_8);
            try { Files.move(temp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE); }
            catch (java.nio.file.AtomicMoveNotSupportedException e) { Files.move(temp, file, StandardCopyOption.REPLACE_EXISTING); }
        } catch (IOException error) {
            throw new IllegalStateException("Cannot save " + file.getFileName() + ": " + error, error);
        }
    }

    /** Names of every arena, in registration order. */
    public List<String> names() { return new ArrayList<>(arenas.keySet()); }
}
