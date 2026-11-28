package com.vulpine.mcb.arena;

import carpet.patches.EntityPlayerMPFake;
import com.vulpine.mcb.body.McbCommand;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import net.minecraft.ChatFormatting;
import net.minecraft.core.BlockPos;
import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.Tag;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.network.protocol.game.ClientboundSetSubtitleTextPacket;
import net.minecraft.network.protocol.game.ClientboundSetTitleTextPacket;
import net.minecraft.network.protocol.game.ClientboundSetTitlesAnimationPacket;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.dialog.Dialog;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Display;
import net.minecraft.world.entity.Entity;
import net.minecraft.util.Prediction;
import net.minecraft.world.entity.EntityTypes;
import net.minecraft.world.item.DyeColor;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.item.component.ItemLore;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.entity.SignBlockEntity;
import net.minecraft.world.level.block.entity.SignText;
import net.minecraft.world.level.block.entity.SignTextSlot;
import net.minecraft.world.phys.AABB;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The lobby's game menu, signs and floating board in the world (docs/LOBBY_MENU.md): each player's cached menu and the
 * dialogs drawn from it, the {@code mcb:menu} clicks coming back, the station signs' text, the text display above the
 * spawn, and the Game Menu compass. It lives in the ArenaRuntime (one server run). Server thread only.
 */
public final class ArenaLobby {
    private static final Logger LOG = LoggerFactory.getLogger("mcb-arena");
    /** The tag on the lobby's floating board, so a stale or duplicate one is found and removed. */
    public static final String DISPLAY_TAG = "mcb_lobby_display";
    /** The custom data marking the Game Menu compass: only it opens the menu (any other compass is a compass). */
    static final String MENU_ITEM_KEY = "mcb_game_menu";
    private static final CompoundTag MENU_ITEM_MARK = mark();
    static final int MENU_SLOT = 8, UPKEEP_TICKS = 20, GREET_DELAY = 40;
    /** The floating board's place, local to the lobby's minimum corner. */
    static final double DISPLAY_X = 12.5, DISPLAY_Y = 5.5, DISPLAY_Z = 12.5;
    private static final int DISPLAY_BACKGROUND = 0xA0000000, DISPLAY_LINE_WIDTH = 320;

    private final MinecraftServer server;
    private final ArenaLobbyMenu.Sessions sessions = new ArenaLobbyMenu.Sessions();
    /** The last sign text and board per arena, applied again when that arena is (re)built. */
    private final Map<String, Map<String, List<ArenaLobbyMenu.Line>>> signs = new HashMap<>();
    private final Map<String, List<ArenaLobbyMenu.Line>> boards = new HashMap<>();
    /** The board entity this run placed per arena: any other tagged board is stale. */
    private final Map<String, UUID> boardEntities = new HashMap<>();
    /** Real players who joined, and the tick their greeting title is due. */
    private final Map<UUID, Integer> greetings = new HashMap<>();

    ArenaLobby(MinecraftServer server) { this.server = server; }

    private static CompoundTag mark() {
        var tag = new CompoundTag();
        tag.putBoolean(MENU_ITEM_KEY, true);
        return tag;
    }

    private int now() { return server.getTickCount(); }

    static boolean fake(ServerPlayer player) { return player instanceof EntityPlayerMPFake; }

    // ---- the op

    /** arena_lobby_view: menus, then signs, the board, shows, closes and titles. Offline players are skipped. */
    Map<String, Object> view(ArenaRecord record, ServerLevel level, ArenaLobbyMenu.View view) {
        int menus = 0, shown = 0, written = 0;
        for (var entry : view.menus().entrySet()) {
            var player = online(entry.getKey());
            if (player == null) continue;
            sessions.menu(entry.getKey(), entry.getValue());
            menus++;
        }
        if (view.signs() != null) {
            signs.computeIfAbsent(record.name(), n -> new LinkedHashMap<>()).putAll(view.signs());
            written = writeSigns(record, level, view.signs());
        }
        if (view.display() != null) {
            boards.put(record.name(), view.display());
            placeBoard(record, level, view.display());
        }
        for (var entry : view.show().entrySet()) {
            var player = online(entry.getKey());
            var session = sessions.get(entry.getKey());
            if (player == null || session == null || session.menu() == null) continue;
            showScreen(player, session, session.target(entry.getValue().screen()), entry.getValue().notice());
            shown++;
        }
        for (var name : view.close()) {
            var player = online(name);
            if (player == null) continue;
            var session = sessions.get(name);
            if (session != null) session.answered();
            ArenaLobbyDialogs.clear(player);
        }
        for (var announce : view.announce()) {
            for (var name : announce.players()) {
                var player = online(name);
                if (player != null) title(player, Component.literal(announce.title()), Component.literal(announce.subtitle()));
            }
        }
        var out = new LinkedHashMap<String, Object>();
        out.put("name", record.name());
        out.put("menus", menus);
        out.put("shown", shown);
        out.put("signs", written);
        return out;
    }

    private ServerPlayer online(String name) {
        var player = server.getPlayerList().getPlayerByName(name);
        return player == null || fake(player) ? null : player;
    }

    private void showScreen(ServerPlayer player, ArenaLobbyMenu.Session session, String id, ArenaLobbyMenu.Line notice) {
        var screen = session.showing(id);
        if (screen == null) return;
        send(player, ArenaLobbyDialogs.screen(id, screen, notice));
    }

    private void send(ServerPlayer player, Dialog dialog) {
        String why = ArenaLobbyDialogs.invalid(player, dialog);
        if (why != null) {
            LOG.warn("lobby dialog for {} does not encode: {}", player.getName().getString(), why);
            dialog = ArenaLobbyDialogs.notice("This screen could not be shown.", "red");
        }
        ArenaLobbyDialogs.show(player, dialog);
    }

    private static void title(ServerPlayer player, Component title, Component subtitle) {
        player.connection.send(new ClientboundSetTitlesAnimationPacket(10, 70, 20));
        player.connection.send(new ClientboundSetSubtitleTextPacket(subtitle));
        player.connection.send(new ClientboundSetTitleTextPacket(title));
    }

    // ---- opening and clicks

    /** The compass, G, the Esc screen's button, /mcb menu and the help sign: the cached home, or ask Python for the menu. */
    void open(ServerPlayer player) {
        if (fake(player)) return;
        String name = player.getName().getString();
        var session = sessions.get(name);
        if (session != null && session.menu() != null) {
            showScreen(player, session, session.menu().home(), null);
            return;
        }
        send(player, ArenaLobbyDialogs.notice(ArenaLobbyMenu.LOADING, "gray"));
        sessions.of(name).loading(now());
        McbCommand.dispatch(player, "lobby", Map.of("action", "menu"));
    }

    /** An {@code mcb:menu} click: page to a cached screen, dispatch to Python, close, or open the menu. */
    void click(ServerPlayer player, Optional<Tag> payload) {
        if (fake(player)) return;
        var values = ArenaLobbyDialogs.values(payload);
        if (!values.containsKey(ArenaLobbyMenu.TOKEN_KEY)) {
            if (values.containsKey(ArenaLobbyMenu.OPEN_KEY)) open(player);
            return;
        }
        String token = values.get(ArenaLobbyMenu.TOKEN_KEY) instanceof String s ? s : null;
        String name = player.getName().getString();
        var session = sessions.get(name);
        if (session == null || session.menu() == null) {
            // no menu here (the server restarted under an open dialog, or it was dropped): start over
            if (ArenaLobbyMenu.CLOSE_TOKEN.equals(token)) ArenaLobbyDialogs.clear(player);
            else open(player);
            return;
        }
        switch (session.click(token, values, now())) {
            case ArenaLobbyMenu.Go go -> showScreen(player, session, session.target(go.screen()), null);
            case ArenaLobbyMenu.Dispatch dispatch -> McbCommand.dispatch(player, "lobby", dispatch.payload());
            case ArenaLobbyMenu.Close close -> ArenaLobbyDialogs.clear(player);
            case ArenaLobbyMenu.Open reopen -> open(player);
            case ArenaLobbyMenu.Ignored ignored -> {
                LOG.debug("lobby click from {} ignored: {}", name, ignored.why());
                // a click on something no longer there: show the home that is
                if (!ignored.why().startsWith("waiting")) showScreen(player, session, session.menu().home(), null);
            }
        }
    }

    /** Entry points for the custom click mixin, the compass, the help sign and /mcb menu (no lobby: say where the menu is). */
    public static void clicked(ServerPlayer player, Optional<Tag> payload) {
        var lobby = here();
        if (lobby != null) lobby.click(player, payload);
        else if (!fake(player)) {
            var values = ArenaLobbyDialogs.values(payload);
            if (ArenaLobbyMenu.CLOSE_TOKEN.equals(values.get(ArenaLobbyMenu.TOKEN_KEY))) ArenaLobbyDialogs.clear(player);
            else ArenaLobbyDialogs.show(player, ArenaLobbyDialogs.notice(ArenaLobbyMenu.NOT_HERE, "yellow"));
        }
    }

    public static void openFor(ServerPlayer player) {
        var lobby = here();
        if (lobby != null) lobby.open(player);
        else if (!fake(player)) ArenaLobbyDialogs.show(player, ArenaLobbyDialogs.notice(ArenaLobbyMenu.NOT_HERE, "yellow"));
    }

    /** The lobby when this server has a {@code lobby} arena, else null. */
    private static ArenaLobby here() {
        var runtime = ArenaRuntime.active();
        if (runtime == null || runtime.registry().get(ArenaLobbyStations.LOBBY_ARENA) == null) return null;
        return runtime.lobby();
    }

    public static void joined(ServerPlayer player) {
        var runtime = ArenaRuntime.active();
        if (runtime == null || fake(player)) return;
        runtime.lobby().greetings.put(player.getUUID(), runtime.lobby().now() + GREET_DELAY);
    }

    /** A player left: their menu goes with them. */
    public static void left(ServerPlayer player) {
        var runtime = ArenaRuntime.active();
        if (runtime == null) return;
        runtime.lobby().sessions.drop(player.getName().getString());
        runtime.lobby().greetings.remove(player.getUUID());
    }

    // ---- the tick

    /** Unanswered clicks, greetings, and (every second) the Game Menu compass for everyone standing in the lobby. */
    void tick(ArenaRecord lobby, ServerLevel level) {
        int now = now();
        for (var entry : sessions.all().entrySet()) {
            var session = entry.getValue();
            String due = session.due(now);
            if (due == null) continue;
            var player = online(entry.getKey());
            if (player == null) continue;
            if (due.isEmpty() || session.menu() == null) send(player, ArenaLobbyDialogs.retry());
            else showScreen(player, session, session.target(due), new ArenaLobbyMenu.Line(ArenaLobbyMenu.NO_ANSWER, "red", false));
        }
        if (!greetings.isEmpty()) {
            for (Iterator<Map.Entry<UUID, Integer>> it = greetings.entrySet().iterator(); it.hasNext(); ) {
                var entry = it.next();
                if (entry.getValue() > now) continue;
                it.remove();
                var player = server.getPlayerList().getPlayer(entry.getKey());
                if (player != null && lobby != null)
                    title(player, Component.literal("MCB Arena").withStyle(ChatFormatting.GOLD, ChatFormatting.BOLD),
                            Component.literal("Right-click the compass to play").withStyle(ChatFormatting.YELLOW));
            }
        }
        if (lobby != null && level != null && now % UPKEEP_TICKS == 0) {
            for (var player : level.players()) {
                if (fake(player) || !player.isAlive() || player.isSpectator()) continue;
                if (lobby.box().containsPoint(player.getX(), player.getY(), player.getZ())) keepCompass(player);
            }
        }
    }

    // ---- the Game Menu compass

    static ItemStack compass() {
        var stack = new ItemStack(Items.COMPASS);
        stack.set(DataComponents.CUSTOM_NAME, plain("Game Menu", ChatFormatting.GOLD));
        stack.set(DataComponents.LORE, new ItemLore(List.of(plain("Right-click to open", ChatFormatting.GRAY))));
        stack.set(DataComponents.CUSTOM_DATA, CustomData.of(mark()));
        return stack;
    }

    private static MutableComponent plain(String text, ChatFormatting color) {
        return Component.literal(text).withStyle(style -> style.withItalic(false).withColor(color));
    }

    public static boolean isMenuCompass(ItemStack stack) {
        if (stack == null || stack.isEmpty() || !stack.is(Items.COMPASS)) return false;
        var data = stack.get(DataComponents.CUSTOM_DATA);
        return data != null && data.matchedBy(MENU_ITEM_MARK);
    }

    /** One Game Menu compass, in hotbar slot 9 unless the player already carries it somewhere. */
    private static void keepCompass(ServerPlayer player) {
        var inventory = player.getInventory();
        boolean carried = isMenuCompass(player.containerMenu.getCarried());
        for (int i = 0; i < inventory.getContainerSize(); i++) {
            if (!isMenuCompass(inventory.getItem(i))) continue;
            if (carried) inventory.setItem(i, ItemStack.EMPTY);  // one is enough
            carried = true;
        }
        if (carried) return;
        var old = inventory.getItem(MENU_SLOT);
        inventory.setItem(MENU_SLOT, compass());
        if (!old.isEmpty() && !inventory.add(old)) player.drop(old, false, Prediction.SERVER_ONLY);
    }

    // ---- signs and the floating board

    private int writeSigns(ArenaRecord record, ServerLevel level, Map<String, List<ArenaLobbyMenu.Line>> text) {
        int written = 0;
        int[] origin = record.origin();
        for (var entry : text.entrySet()) {
            var station = ArenaLobbyStations.byId(entry.getKey());
            if (station == null) continue;
            var pos = new BlockPos(origin[0] + station.signX(), origin[1] + station.signY(), origin[2] + station.signZ());
            if (!(level.getBlockEntity(pos) instanceof SignBlockEntity sign)) continue;
            var lines = new ArrayList<Component>();
            for (var line : entry.getValue()) lines.add(ArenaBoard.text(line.text(), line.color(), line.bold(), false));
            while (lines.size() < SignText.LINES) lines.add(Component.empty());
            sign.setText(new SignText(lines, lines, DyeColor.BLACK, true), SignTextSlot.FRONT);
            sign.setWaxed(true);
            sign.setChanged();
            var state = level.getBlockState(pos);
            level.sendBlockUpdated(pos, state, state, Block.UPDATE_ALL);
            written++;
        }
        return written;
    }

    private void placeBoard(ArenaRecord record, ServerLevel level, List<ArenaLobbyMenu.Line> lines) {
        var keep = boardEntities.get(record.name());
        Display.TextDisplay board = null;
        for (var found : level.getEntitiesOfClass(Display.TextDisplay.class, area(record), e -> e.entityTags().contains(DISPLAY_TAG))) {
            if (board == null && (keep == null || found.getUUID().equals(keep))) board = found;
            else found.discard();
        }
        if (lines.isEmpty()) {
            if (board != null) board.discard();
            boardEntities.remove(record.name());
            return;
        }
        var text = Component.empty();
        for (int i = 0; i < lines.size(); i++) {
            var line = lines.get(i);
            if (i > 0) text.append("\n");
            text.append(ArenaBoard.text(line.text(), line.color(), line.bold(), false));
        }
        if (board == null) {
            board = new Display.TextDisplay(EntityTypes.TEXT_DISPLAY, level);
            int[] origin = record.origin();
            board.setPos(origin[0] + DISPLAY_X, origin[1] + DISPLAY_Y, origin[2] + DISPLAY_Z);
            board.addTag(DISPLAY_TAG);
            board.setBillboardConstraints(Display.BillboardConstraints.CENTER);
            board.setBackgroundColor(DISPLAY_BACKGROUND);
            board.setLineWidth(DISPLAY_LINE_WIDTH);
            board.setText(text);
            level.addFreshEntity(board);
        } else {
            board.setText(text);
        }
        boardEntities.put(record.name(), board.getUUID());
    }

    private static AABB area(ArenaRecord record) {
        var box = record.box();
        return new AABB(box.minX(), box.minY(), box.minZ(), box.maxX() + 1.0, box.maxY() + 1.0, box.maxZ() + 1.0);
    }

    /** The arena finished building: its signs and board come back (a rebuilt lobby has blank signs). */
    void built(ArenaRecord record, ServerLevel level) {
        var text = signs.get(record.name());
        if (text != null) writeSigns(record, level, text);
        var board = boards.get(record.name());
        if (board != null) placeBoard(record, level, board);
    }

    /** The arena is being removed: its board goes (the text is kept for a rebuild). */
    void removed(ArenaRecord record, ServerLevel level) {
        for (var found : level.getEntitiesOfClass(Display.TextDisplay.class, area(record), e -> e.entityTags().contains(DISPLAY_TAG)))
            found.discard();
        boardEntities.remove(record.name());
    }

    /** A board loaded from a chunk file that this run did not place: left from an earlier run or a duplicate. */
    boolean staleBoard(Entity entity) {
        return entity instanceof Display.TextDisplay && entity.entityTags().contains(DISPLAY_TAG)
                && !boardEntities.containsValue(entity.getUUID());
    }

    void close() {
        sessions.clear();
        greetings.clear();
    }
}
