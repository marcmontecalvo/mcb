package com.vulpine.mcb.body;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.DoubleArgumentType;
import com.mojang.brigadier.arguments.IntegerArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import com.mojang.brigadier.builder.RequiredArgumentBuilder;
import com.mojang.brigadier.context.CommandContext;
import com.mojang.brigadier.suggestion.Suggestions;
import com.mojang.brigadier.suggestion.SuggestionsBuilder;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.commands.SharedSuggestionProvider;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;

import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

/**
 * /mcb status|stop|ready|goto|gather|build|ask|attack|defense|lobby. Open to every player (permission 0): this only
 * records the request; the Python companion service checks authority before acting on anything but stop's cancel.
 */
public final class McbCommand {
    private static final List<String> TEAMS = List.of("red", "blue", "green", "yellow");
    private static final List<String> MAPS = List.of("crossroads", "duel", "hall", "crash_site", "vault");
    private static final List<String> MODES = List.of("teams", "waves", "race");
    private static final List<String> TOGGLES = List.of("on", "off");
    private static final List<String> RESPAWN_MODES = List.of("elimination", "player", "team");
    private static final List<String> KITS = List.of("fighter", "archer", "tank", "skirmisher", "random");
    private static final List<String> KIT_MODES = List.of("uniform", "per_team", "class_picker", "everyone_random",
            "random_shared");
    private static final List<String> ALLIED_MOBS = List.of("skeleton", "zombie", "pillager", "vindicator",
            "spider", "witch", "iron_golem", "blaze", "breeze");

    private McbCommand() {}

    /** Emits a structured player command event from in-world interactions (such as lobby stations). */
    public static void dispatch(ServerPlayer player, String sub, Map<String, Object> args) {
        BodyHub.playerCommand(player, sub, args);
    }

    static void register(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal("mcb")
                .then(send("status"))
                .then(send("stop"))
                // /mcb ready: a waiting arena match starts once every named player has said it
                .then(send("ready"))
                .then(Commands.literal("goto")
                        .then(coordinate("x").then(coordinate("y").then(coordinate("z").executes(context ->
                                run(context.getSource(), "goto", Map.of(
                                        "x", IntegerArgumentType.getInteger(context, "x"),
                                        "y", IntegerArgumentType.getInteger(context, "y"),
                                        "z", IntegerArgumentType.getInteger(context, "z"))))))))
                .then(Commands.literal("gather")
                        .then(Commands.argument("item", StringArgumentType.word())
                                .then(Commands.argument("count", IntegerArgumentType.integer(1, 2304)).executes(context ->
                                        run(context.getSource(), "gather", Map.of(
                                                "item", StringArgumentType.getString(context, "item"),
                                                "count", IntegerArgumentType.getInteger(context, "count")))))))
                .then(Commands.literal("ask")
                        .then(Commands.argument("text", StringArgumentType.greedyString()).executes(context -> {
                            String text;
                            try { text = CompanionChat.requireText(StringArgumentType.getString(context, "text")); }
                            catch (IllegalArgumentException error) {
                                context.getSource().sendFailure(Component.literal("Ask 1 to 256 characters."));
                                return 0;
                            }
                            return run(context.getSource(), "ask", Map.of("text", text));
                        })))
                .then(Commands.literal("build")
                        .then(Commands.argument("name", StringArgumentType.word()).executes(context ->
                                run(context.getSource(), "build", Map.of(
                                        "name", StringArgumentType.getString(context, "name"))))))
                // /mcb attack <player|mob|hostile> [weapon]: one word, which Python resolves to an online player
                // first, else a mob type; the owner and allowlist check is Python's too.
                .then(Commands.literal("attack")
                        .then(Commands.argument("target", StringArgumentType.word())
                                .suggests(McbCommand::suggestPlayers)
                                .executes(context -> run(context.getSource(), "attack", Map.of(
                                        "target", StringArgumentType.getString(context, "target"))))
                                .then(Commands.argument("weapon", StringArgumentType.word()).executes(context ->
                                        run(context.getSource(), "attack", Map.of(
                                                "target", StringArgumentType.getString(context, "target"),
                                                "weapon", StringArgumentType.getString(context, "weapon")))))))
                // /mcb defense: the settings; floor <hearts> and radius <blocks> change them
                .then(Commands.literal("defense")
                        .executes(context -> run(context.getSource(), "defense", Map.of()))
                        .then(Commands.literal("floor")
                                .then(Commands.argument("hearts", DoubleArgumentType.doubleArg(0.5, 9.5)).executes(context ->
                                        run(context.getSource(), "defense", Map.of(
                                                "floor_hearts", DoubleArgumentType.getDouble(context, "hearts"))))))
                        .then(Commands.literal("radius")
                                .then(Commands.argument("blocks", IntegerArgumentType.integer(2, 32)).executes(context ->
                                        run(context.getSource(), "defense", Map.of(
                                                "threat_radius", IntegerArgumentType.getInteger(context, "blocks")))))))
                .then(lobbyCommand()));
    }

    private static LiteralArgumentBuilder<CommandSourceStack> lobbyCommand() {
        return Commands.literal("lobby")
                .executes(context -> run(context.getSource(), "lobby", Map.of("action", "status")))
                .then(lobbyAction("status"))
                .then(lobbyAction("play"))
                .then(lobbyAction("watch"))
                .then(lobbyAction("rematch"))
                .then(lobbyAction("spectate"))
                .then(lobbyAction("leave"))
                .then(lobbyAction("unready"))
                .then(lobbyAction("reopen"))
                .then(Commands.literal("ready")
                        .executes(context -> run(context.getSource(), "lobby", Map.of("action", "ready")))
                        .then(Commands.argument("revision", IntegerArgumentType.integer(1)).executes(context ->
                                run(context.getSource(), "lobby", Map.of(
                                        "action", "ready",
                                        "seen_revision", IntegerArgumentType.getInteger(context, "revision"))))))
                .then(Commands.literal("join")
                        .then(Commands.argument("team", StringArgumentType.word())
                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(TEAMS, builder))
                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                        "action", "join",
                                        "team", StringArgumentType.getString(context, "team"))))))
                .then(Commands.literal("map")
                        .then(Commands.argument("name", StringArgumentType.word())
                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(MAPS, builder))
                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                        "action", "map",
                                        "map", StringArgumentType.getString(context, "name"))))))
                .then(Commands.literal("mode")
                        .then(Commands.argument("mode", StringArgumentType.word())
                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(MODES, builder))
                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                        "action", "mode",
                                        "mode", StringArgumentType.getString(context, "mode"))))))
                .then(Commands.literal("uneven")
                        .then(Commands.argument("state", StringArgumentType.word())
                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(TOGGLES, builder))
                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                        "action", "uneven",
                                        "value", StringArgumentType.getString(context, "state"))))))
                .then(Commands.literal("respawns")
                        .then(Commands.argument("mode", StringArgumentType.word())
                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(RESPAWN_MODES, builder))
                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                        "action", "respawns",
                                        "respawn_mode", StringArgumentType.getString(context, "mode"),
                                        "respawn_count", 0)))
                                .then(Commands.argument("count", IntegerArgumentType.integer(0, 99)).executes(context ->
                                        run(context.getSource(), "lobby", Map.of(
                                                "action", "respawns",
                                                "respawn_mode", StringArgumentType.getString(context, "mode"),
                                                "respawn_count", IntegerArgumentType.getInteger(context, "count")))))))
                .then(Commands.literal("host")
                        .then(Commands.argument("player", StringArgumentType.word())
                                .suggests(McbCommand::suggestPlayers)
                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                        "action", "host",
                                        "player", StringArgumentType.getString(context, "player"))))))
                .then(Commands.literal("bot")
                        .then(Commands.literal("add")
                                .then(Commands.argument("team", StringArgumentType.word())
                                        .suggests((context, builder) -> SharedSuggestionProvider.suggest(TEAMS, builder))
                                        .executes(context -> run(context.getSource(), "lobby", Map.of(
                                                "action", "bot_add",
                                                "team", StringArgumentType.getString(context, "team"))))
                                        .then(Commands.argument("name", StringArgumentType.word()).executes(context ->
                                                run(context.getSource(), "lobby", Map.of(
                                                        "action", "bot_add",
                                                        "team", StringArgumentType.getString(context, "team"),
                                                        "name", StringArgumentType.getString(context, "name")))))))
                        .then(Commands.literal("rename")
                                .then(Commands.argument("bot", StringArgumentType.word())
                                        .then(Commands.argument("name", StringArgumentType.word()).executes(context ->
                                                run(context.getSource(), "lobby", Map.of(
                                                        "action", "bot_rename",
                                                        "bot", StringArgumentType.getString(context, "bot"),
                                                        "name", StringArgumentType.getString(context, "name")))))))
                        .then(Commands.literal("move")
                                .then(Commands.argument("bot", StringArgumentType.word())
                                        .then(Commands.argument("team", StringArgumentType.word())
                                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(TEAMS, builder))
                                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                                        "action", "bot_move",
                                                        "bot", StringArgumentType.getString(context, "bot"),
                                                        "team", StringArgumentType.getString(context, "team")))))))
                        .then(Commands.literal("remove")
                                .then(Commands.argument("bot", StringArgumentType.word()).executes(context ->
                                        run(context.getSource(), "lobby", Map.of(
                                                "action", "bot_remove",
                                                "bot", StringArgumentType.getString(context, "bot")))))))
                .then(Commands.literal("kit")
                        .then(Commands.argument("kit_id", StringArgumentType.word())
                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(KITS, builder))
                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                        "action", "kit_pick",
                                        "kit", StringArgumentType.getString(context, "kit_id"))))))
                .then(Commands.literal("kitmode")
                        .then(Commands.argument("mode", StringArgumentType.word())
                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(KIT_MODES, builder))
                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                        "action", "kit_mode",
                                        "mode", StringArgumentType.getString(context, "mode"))))))
                .then(Commands.literal("kitreroll")
                        .then(Commands.argument("state", StringArgumentType.word())
                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(TOGGLES, builder))
                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                        "action", "kit_reroll",
                                        "value", StringArgumentType.getString(context, "state"))))))
                .then(Commands.literal("mob")
                        .then(Commands.literal("add")
                                .then(Commands.argument("team", StringArgumentType.word())
                                        .suggests((context, builder) -> SharedSuggestionProvider.suggest(TEAMS, builder))
                                        .then(Commands.argument("mob", StringArgumentType.word())
                                                .suggests((context, builder) -> SharedSuggestionProvider.suggest(ALLIED_MOBS, builder))
                                                .executes(context -> run(context.getSource(), "lobby", Map.of(
                                                        "action", "mob_add",
                                                        "team", StringArgumentType.getString(context, "team"),
                                                        "mob", StringArgumentType.getString(context, "mob"),
                                                        "count", 1)))
                                                .then(Commands.argument("count", IntegerArgumentType.integer(1, 32)).executes(context ->
                                                        run(context.getSource(), "lobby", Map.of(
                                                                "action", "mob_add",
                                                                "team", StringArgumentType.getString(context, "team"),
                                                                "mob", StringArgumentType.getString(context, "mob"),
                                                                "count", IntegerArgumentType.getInteger(context, "count"))))))))
                        .then(Commands.literal("remove")
                                .then(Commands.argument("group_id", StringArgumentType.word()).executes(context ->
                                        run(context.getSource(), "lobby", Map.of(
                                                "action", "mob_remove",
                                                "group_id", StringArgumentType.getString(context, "group_id")))))));
    }

    private static LiteralArgumentBuilder<CommandSourceStack> lobbyAction(String action) {
        return Commands.literal(action).executes(context ->
                run(context.getSource(), "lobby", Map.of("action", action)));
    }

    /** Online player names, for tab completion of a target. */
    private static CompletableFuture<Suggestions> suggestPlayers(
            CommandContext<CommandSourceStack> context,
            SuggestionsBuilder builder) {
        return SharedSuggestionProvider.suggest(
                context.getSource().getServer().getPlayerList().getPlayerNamesArray(), builder);
    }

    private static LiteralArgumentBuilder<CommandSourceStack> send(String sub) {
        return Commands.literal(sub).executes(context -> run(context.getSource(), sub, Map.of()));
    }

    private static RequiredArgumentBuilder<CommandSourceStack, Integer> coordinate(String name) {
        return Commands.argument(name, IntegerArgumentType.integer(-30_000_000, 30_000_000));
    }

    /** The sender's own acknowledgement; a waiting match answers ready itself. */
    static String reply(String sub) {
        return switch (sub) {
            case "stop" -> "Stopped.";
            case "ready" -> "Ready.";
            default -> "Sent.";
        };
    }

    private static int run(CommandSourceStack source, String sub, Map<String, Object> args) {
        var player = source.getPlayer();
        if (player == null) return 0; // console and command blocks are not players
        BodyHub.playerCommand(player, sub, args);
        player.sendSystemMessage(Component.literal(reply(sub)));
        return 1;
    }
}
