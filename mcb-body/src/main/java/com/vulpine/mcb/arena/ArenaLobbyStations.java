package com.vulpine.mcb.arena;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The physical stations inside the {@code "lobby"} arena (pure; docs/LOBBY_MENU.md, "Station layout"). Coordinates are
 * local to the lobby arena's minimum corner and a Python test keeps them equal to {@code mclab.arena.designs.LOBBY_STATIONS}.
 * Right-clicking a station's sign or its stone button dispatches its payload plus {@code "station": id} as a {@code "lobby"}
 * player command; the help sign (no button, an empty payload) opens the game menu instead. Python rewrites the sign text
 * through {@code arena_lobby_view}.
 */
public final class ArenaLobbyStations {
    public static final String LOBBY_ARENA = "lobby";

    /** A station: its id, its wall sign's cell, its button's cell ({@code -1, -1, -1} for none) and its click payload. */
    public record Station(String id, int signX, int signY, int signZ, int buttonX, int buttonY, int buttonZ, Map<String, Object> payload) {
        /** The help sign: the mod opens the game menu for it, with no Python round trip. */
        public boolean help() { return payload.isEmpty(); }

        public boolean hasButton() { return !(buttonX == -1 && buttonY == -1 && buttonZ == -1); }

        public boolean isSign(int lx, int ly, int lz) { return lx == signX && ly == signY && lz == signZ; }

        public boolean isButton(int lx, int ly, int lz) { return hasButton() && lx == buttonX && ly == buttonY && lz == buttonZ; }

        /** What a click on this station dispatches: its payload and {@code "station": id}. */
        public Map<String, Object> click() {
            var out = new LinkedHashMap<String, Object>(payload);
            out.put("station", id);
            return out;
        }
    }

    // One station per line, in this exact form: a Python parity test reads them.
    public static final List<Station> STATIONS = List.of(new Station[] {
            new Station("join_red", 2, 3, 7, 2, 2, 7, Map.of("action", "join", "team", "red")),
            new Station("join_blue", 2, 3, 11, 2, 2, 11, Map.of("action", "join", "team", "blue")),
            new Station("join_green", 2, 3, 15, 2, 2, 15, Map.of("action", "join", "team", "green")),
            new Station("join_yellow", 2, 3, 19, 2, 2, 19, Map.of("action", "join", "team", "yellow")),
            new Station("ready", 22, 3, 12, 22, 2, 12, Map.of("action", "ready_toggle")),
            new Station("help", 12, 3, 22, -1, -1, -1, Map.of()),
    });

    private ArenaLobbyStations() {}

    /** The station whose sign or button is at this world cell of the lobby arena at {@code origin}, else null. */
    public static Station at(int[] origin, int worldX, int worldY, int worldZ) {
        if (origin == null || origin.length < 3) return null;
        int lx = worldX - origin[0], ly = worldY - origin[1], lz = worldZ - origin[2];
        for (var station : STATIONS) if (station.isSign(lx, ly, lz) || station.isButton(lx, ly, lz)) return station;
        return null;
    }

    public static Station byId(String id) {
        for (var station : STATIONS) if (station.id().equals(id)) return station;
        return null;
    }
}
