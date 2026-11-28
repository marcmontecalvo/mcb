package com.vulpine.mcb.arena;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import net.minecraft.core.Holder;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.NbtOps;
import net.minecraft.nbt.NumericTag;
import net.minecraft.nbt.StringTag;
import net.minecraft.nbt.Tag;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.common.ClientboundClearDialogPacket;
import net.minecraft.network.protocol.common.ClientboundShowDialogPacket;
import net.minecraft.resources.Identifier;
import net.minecraft.server.dialog.ActionButton;
import net.minecraft.server.dialog.CommonButtonData;
import net.minecraft.server.dialog.CommonDialogData;
import net.minecraft.server.dialog.Dialog;
import net.minecraft.server.dialog.DialogAction;
import net.minecraft.server.dialog.MultiActionDialog;
import net.minecraft.server.dialog.NoticeDialog;
import net.minecraft.server.dialog.action.Action;
import net.minecraft.server.dialog.action.CustomAll;
import net.minecraft.server.dialog.action.StaticAction;
import net.minecraft.server.dialog.body.DialogBody;
import net.minecraft.server.dialog.body.PlainMessage;
import net.minecraft.server.dialog.input.BooleanInput;
import net.minecraft.server.dialog.input.InputControl;
import net.minecraft.server.dialog.input.NumberRangeInput;
import net.minecraft.server.dialog.input.SingleOptionInput;
import net.minecraft.server.dialog.input.TextInput;
import net.minecraft.server.level.ServerPlayer;

/**
 * Draws the lobby's menu screens (ArenaLobbyMenu.Screen) as vanilla server-sent dialogs, which stock 26.3 clients show
 * without a mod. Every button sends the custom click {@code mcb:menu} carrying only a token ({@link ArenaLobbyMenu#TOKEN_KEY});
 * a submit button's click also carries the screen's input values (a dynamic custom click). Server thread only.
 *
 * <p>Every dialog's after-action is {@code none}: the dialog stays up until the mod replaces or clears it. A vanilla
 * client's "waiting for response" screen cannot be cleared by the server ({@code ClientboundClearDialogPacket} only
 * closes a dialog screen), so a close button or a {@code close} op on a {@code wait_for_response} screen would strand the
 * player; instead a click that dispatches to Python is guarded by the session (a second one waits for the answer).
 */
final class ArenaLobbyDialogs {
    private ArenaLobbyDialogs() {}

    static final Identifier CLICK = Identifier.parse(ArenaLobbyMenu.CLICK_ID);
    static final int BODY_WIDTH = 300, INPUT_WIDTH = 300, BUTTON_WIDTH = 150;

    /** A menu screen, with {@code notice} (null for none) as its first body line this one showing. */
    static Dialog screen(String id, ArenaLobbyMenu.Screen screen, ArenaLobbyMenu.Line notice) {
        var body = new ArrayList<DialogBody>();
        if (notice != null) body.add(message(notice));
        for (var line : screen.body()) body.add(message(line));
        var inputs = new ArrayList<net.minecraft.server.dialog.Input>();
        for (var input : screen.inputs()) inputs.add(input(input));
        var common = common(screen.title(), body, inputs);
        var exit = button(screen.exit(), ArenaLobbyMenu.exitToken(id));
        if (screen.buttons().isEmpty()) return new NoticeDialog(common, exit);
        var buttons = new ArrayList<ActionButton>();
        for (int i = 0; i < screen.buttons().size(); i++) buttons.add(button(screen.buttons().get(i), ArenaLobbyMenu.token(id, i)));
        return new MultiActionDialog(common, buttons, Optional.of(exit), screen.columns());
    }

    /** A notice of the mod's own (loading, not here): one line and a Close button. */
    static Dialog notice(String text, String color) {
        var close = new ArenaLobbyMenu.Button("Close", null, "white", 0, ArenaLobbyMenu.Kind.CLOSE, null, Map.of());
        return new NoticeDialog(common("MCB Arena", List.of(message(new ArenaLobbyMenu.Line(text, color, false))), List.of()),
                button(close, ArenaLobbyMenu.CLOSE_TOKEN));
    }

    /** The menu was asked for and never came: say so, with Try Again (asks again) and Close. */
    static Dialog retry() {
        var again = new ArenaLobbyMenu.Button("Try Again", null, "green", 0, ArenaLobbyMenu.Kind.CLOSE, null, Map.of());
        var close = new ArenaLobbyMenu.Button("Close", null, "white", 0, ArenaLobbyMenu.Kind.CLOSE, null, Map.of());
        var common = common("MCB Arena", List.of(message(new ArenaLobbyMenu.Line(ArenaLobbyMenu.NO_ANSWER, "red", false))), List.of());
        return new MultiActionDialog(common, List.of(button(again, ArenaLobbyMenu.OPEN_TOKEN)),
                Optional.of(button(close, ArenaLobbyMenu.CLOSE_TOKEN)), 1);
    }

    private static CommonDialogData common(String title, List<DialogBody> body, List<net.minecraft.server.dialog.Input> inputs) {
        return new CommonDialogData(Component.literal(title), Optional.empty(), true, false, DialogAction.NONE, List.copyOf(body),
                List.copyOf(inputs));
    }

    private static DialogBody message(ArenaLobbyMenu.Line line) {
        return new PlainMessage(ArenaBoard.text(line.text(), line.color(), line.bold(), false), BODY_WIDTH);
    }

    private static ActionButton button(ArenaLobbyMenu.Button button, String token) {
        var label = ArenaBoard.text(button.label(), button.color(), false, false);
        var data = new CommonButtonData(label, Optional.ofNullable(button.tooltip()).map(Component::literal),
                button.width() > 0 ? button.width() : BUTTON_WIDTH);
        var tag = new CompoundTag();
        tag.putString(ArenaLobbyMenu.TOKEN_KEY, token);
        Action action = button.kind() == ArenaLobbyMenu.Kind.SUBMIT
                ? new CustomAll(CLICK, Optional.of(tag))
                : new StaticAction(new ClickEvent.Custom(CLICK, Optional.of(tag)));
        return new ActionButton(data, Optional.of(action));
    }

    private static net.minecraft.server.dialog.Input input(ArenaLobbyMenu.Input input) {
        var label = Component.literal(input.label());
        InputControl control = switch (input) {
            case ArenaLobbyMenu.Dropdown d -> {
                var entries = new ArrayList<SingleOptionInput.Entry>();
                for (var option : d.options())
                    entries.add(new SingleOptionInput.Entry(option.id(), Optional.of(Component.literal(option.label())), option.id().equals(d.initial())));
                yield new SingleOptionInput(INPUT_WIDTH, List.copyOf(entries), label, true);
            }
            case ArenaLobbyMenu.Toggle t -> new BooleanInput(label, t.initial(), "true", "false");
            case ArenaLobbyMenu.Text t -> new TextInput(INPUT_WIDTH, label, true, t.initial(), t.maxLength(), Optional.empty());
            case ArenaLobbyMenu.Slider s -> new NumberRangeInput(INPUT_WIDTH, label, "options.generic_value",
                    new NumberRangeInput.RangeInfo(s.min(), s.max(), Optional.of((float) s.initial()), Optional.of((float) s.step())));
        };
        return new net.minecraft.server.dialog.Input(input.key(), control);
    }

    /** Null when the dialog encodes (what the client packet needs), else why not: a bad dialog would disconnect the player. */
    static String invalid(ServerPlayer player, Dialog dialog) {
        var ops = player.level().getServer().registryAccess().createSerializationContext(NbtOps.INSTANCE);
        return Dialog.DIRECT_CODEC.encodeStart(ops, dialog).error().map(error -> error.message()).orElse(null);
    }

    static void show(ServerPlayer player, Dialog dialog) {
        player.connection.send(new ClientboundShowDialogPacket(Holder.direct(dialog)));
    }

    static void clear(ServerPlayer player) { player.connection.send(ClientboundClearDialogPacket.INSTANCE); }

    /**
     * A click's payload as plain values (text, numbers; NBT has no booleans, so a toggle arrives as a byte), by key. The
     * token is under {@link ArenaLobbyMenu#TOKEN_KEY}; anything that is not a compound gives nothing.
     */
    static Map<String, Object> values(Optional<Tag> payload) {
        var out = new LinkedHashMap<String, Object>();
        if (payload.isEmpty() || !(payload.get() instanceof CompoundTag compound)) return out;
        for (var key : compound.keySet()) {
            var tag = compound.get(key);
            if (tag instanceof StringTag text) out.put(key, text.value());
            else if (tag instanceof NumericTag number) out.put(key, number.box());
        }
        return out;
    }
}
