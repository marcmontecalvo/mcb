package com.vulpine.mcb.body;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.DoubleArgumentType;
import com.mojang.brigadier.arguments.IntegerArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import com.mojang.brigadier.builder.RequiredArgumentBuilder;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;

import java.util.Map;

/**
 * /mcb status|stop|ready|goto|gather|build|ask|attack|defense. Open to every player (permission 0): this only records the request;
 * the Python companion service checks the owner and allowlist before acting on anything but stop's cancel.
 */
final class McbCommand {
    private McbCommand() {}

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
                                                "threat_radius", IntegerArgumentType.getInteger(context, "blocks"))))))));
    }

    /** Online player names, for tab completion of a target. */
    private static java.util.concurrent.CompletableFuture<com.mojang.brigadier.suggestion.Suggestions> suggestPlayers(
            com.mojang.brigadier.context.CommandContext<CommandSourceStack> context,
            com.mojang.brigadier.suggestion.SuggestionsBuilder builder) {
        return net.minecraft.commands.SharedSuggestionProvider.suggest(
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
        BodyRuntime.playerCommand(player, sub, args);
        player.sendSystemMessage(Component.literal(reply(sub)));
        return 1;
    }
}
