package com.vulpine.mcb.body;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.IntegerArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import com.mojang.brigadier.builder.RequiredArgumentBuilder;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;

import java.util.Map;

/**
 * /mcb status|stop|goto|gather|build. Open to every player (permission 0): this only records the request;
 * the Python companion service checks the owner and allowlist before acting on anything but stop's cancel.
 */
final class McbCommand {
    private McbCommand() {}

    static void register(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal("mcb")
                .then(send("status"))
                .then(send("stop"))
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
                                        "name", StringArgumentType.getString(context, "name")))))));
    }

    private static LiteralArgumentBuilder<CommandSourceStack> send(String sub) {
        return Commands.literal(sub).executes(context -> run(context.getSource(), sub, Map.of()));
    }

    private static RequiredArgumentBuilder<CommandSourceStack, Integer> coordinate(String name) {
        return Commands.argument(name, IntegerArgumentType.integer(-30_000_000, 30_000_000));
    }

    private static int run(CommandSourceStack source, String sub, Map<String, Object> args) {
        var player = source.getPlayer();
        if (player == null) return 0; // console and command blocks are not players
        BodyRuntime.playerCommand(player, sub, args);
        player.sendSystemMessage(Component.literal(sub.equals("stop") ? "Stopped." : "Sent."));
        return 1;
    }
}
