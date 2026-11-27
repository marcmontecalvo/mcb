package com.vulpine.mcb.arena;

import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Clickable vanilla interaction stations inside the {@code "lobby"} arena (pure).
 *
 * <p>Coordinates are local to the lobby arena's minimum corner and mirror
 * {@code mclab.arena.designs.LOBBY_STATIONS}. Right-clicking either a station's wall block
 * or the wall banner directly above it dispatches a structured {@code "lobby"}
 * {@code player_command} event through {@code BodyHub.playerCommand}.
 */
public final class ArenaLobbyStations {
    public static final String LOBBY_ARENA = "lobby";

    public record Station(
            String label,
            int x,
            int y,
            int z,
            int bannerX,
            int bannerY,
            int bannerZ,
            Map<String, Object> payload) {
        boolean matches(int lx, int ly, int lz) {
            return (lx == x && ly == y && lz == z)
                    || (lx == bannerX && ly == bannerY && lz == bannerZ);
        }
    }

    public static final List<Station> STATIONS = List.of(
            // North showcase wall (under spectator gallery, facing spawn)
            new Station("Play", 9, 3, 5, 9, 4, 6,
                    Map.of("action", "play", "station", "Play")),
            new Station("Watch", 11, 3, 5, 11, 4, 6,
                    Map.of("action", "watch", "station", "Watch")),
            new Station("Play Again", 13, 3, 5, 13, 4, 6,
                    Map.of("action", "rematch", "station", "Play Again")),
            new Station("Ready", 15, 3, 5, 15, 4, 6,
                    Map.of("action", "ready_toggle", "station", "Ready")),
            // West wall: team join / create & leave controls
            new Station("Join Red", 1, 3, 8, 2, 4, 8,
                    Map.of("action", "join", "team", "red", "station", "Join Red")),
            new Station("Join Blue", 1, 3, 11, 2, 4, 11,
                    Map.of("action", "join", "team", "blue", "station", "Join Blue")),
            new Station("Join Green", 1, 3, 14, 2, 4, 14,
                    Map.of("action", "join", "team", "green", "station", "Join Green")),
            new Station("Join Yellow", 1, 3, 17, 2, 4, 17,
                    Map.of("action", "join", "team", "yellow", "station", "Join Yellow")),
            new Station("Leave", 1, 3, 20, 2, 4, 20,
                    Map.of("action", "leave", "station", "Leave")),
            // East wall: host rule, bot, and countdown controls
            new Station("Map", 23, 3, 8, 22, 4, 8,
                    Map.of("action", "cycle_map", "station", "Map")),
            new Station("Mode", 23, 3, 10, 22, 4, 10,
                    Map.of("action", "cycle_mode", "station", "Mode")),
            new Station("Add Bot", 23, 3, 12, 22, 4, 12,
                    Map.of("action", "bot_add", "station", "Add Bot")),
            new Station("Remove Bot", 23, 3, 14, 22, 4, 14,
                    Map.of("action", "bot_remove", "station", "Remove Bot")),
            new Station("Uneven Teams", 23, 3, 16, 22, 4, 16,
                    Map.of("action", "toggle_uneven", "station", "Uneven Teams")),
            new Station("Respawns", 23, 3, 18, 22, 4, 18,
                    Map.of("action", "cycle_respawns", "station", "Respawns")),
            new Station("Reopen", 23, 3, 20, 22, 4, 20,
                    Map.of("action", "reopen", "station", "Reopen")));

    private ArenaLobbyStations() {}

    /**
     * Maps a world block position inside the lobby arena to its lobby command payload,
     * or {@code null} when the clicked block is not a lobby station.
     */
    public static Map<String, Object> payloadFor(int[] origin, int worldX, int worldY, int worldZ) {
        if (origin == null || origin.length < 3) return null;
        int lx = worldX - origin[0];
        int ly = worldY - origin[1];
        int lz = worldZ - origin[2];
        for (var station : STATIONS) {
            if (station.matches(lx, ly, lz)) return station.payload();
        }
        return null;
    }

    /**
     * Parses labeled vanilla sign lines (e.g. {@code "[Play]"}, {@code "[Join Green]"})
     * into a structured lobby command payload, or {@code null} if unrecognized.
     */
    public static Map<String, Object> fromSignLines(List<String> lines) {
        if (lines == null || lines.isEmpty()) return null;
        for (var raw : lines) {
            if (raw == null) continue;
            String normalized = raw.trim().replace("[", "").replace("]", "").trim().toLowerCase(Locale.ROOT);
            if (normalized.isEmpty()) continue;
            switch (normalized) {
                case "play" -> { return Map.of("action", "play"); }
                case "watch", "spectate" -> { return Map.of("action", "watch"); }
                case "play again", "rematch" -> { return Map.of("action", "rematch"); }
                case "ready" -> { return Map.of("action", "ready_toggle"); }
                case "leave" -> { return Map.of("action", "leave"); }
                case "map" -> { return Map.of("action", "cycle_map"); }
                case "mode" -> { return Map.of("action", "cycle_mode"); }
                case "add bot" -> { return Map.of("action", "bot_add"); }
                case "remove bot" -> { return Map.of("action", "bot_remove"); }
                case "uneven", "uneven teams" -> { return Map.of("action", "toggle_uneven"); }
                case "respawns" -> { return Map.of("action", "cycle_respawns"); }
                case "reopen" -> { return Map.of("action", "reopen"); }
                case "red", "join red", "create red" -> { return Map.of("action", "join", "team", "red"); }
                case "blue", "join blue", "create blue" -> { return Map.of("action", "join", "team", "blue"); }
                case "green", "join green", "create green" -> { return Map.of("action", "join", "team", "green"); }
                case "yellow", "join yellow", "create yellow" -> { return Map.of("action", "join", "team", "yellow"); }
                default -> { /* inspect next line */ }
            }
        }
        return null;
    }
}
