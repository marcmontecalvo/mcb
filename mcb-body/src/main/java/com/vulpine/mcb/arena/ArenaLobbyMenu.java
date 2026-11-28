package com.vulpine.mcb.arena;

import static com.vulpine.mcb.arena.ArenaRequests.bad;
import static com.vulpine.mcb.arena.ArenaRequests.bounded;
import static com.vulpine.mcb.arena.ArenaRequests.color;
import static com.vulpine.mcb.arena.ArenaRequests.flag;
import static com.vulpine.mcb.arena.ArenaRequests.name;
import static com.vulpine.mcb.arena.ArenaRequests.only;
import static com.vulpine.mcb.arena.ArenaRequests.text;
import static com.vulpine.mcb.arena.ArenaRequests.whole;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * The lobby's game menu without Minecraft (checked by ArenaLobbyMenuTest): the {@code arena_lobby_view} request
 * (docs/LOBBY_MENU.md), the click tokens a dialog sends back, the submit merge, and each player's cached menu.
 * Python owns every word and rule; the mod only draws screens, pages between them ({@code go}) and passes clicks back.
 */
public final class ArenaLobbyMenu {
    private ArenaLobbyMenu() {}

    /** The custom click id every menu button sends. */
    public static final String CLICK_ID = "mcb:menu";
    /**
     * The click payload key holding the token. Input keys match {@code [a-z_]}, so a dynamic (submit) click's input
     * values can never overwrite it.
     */
    public static final String TOKEN_KEY = "@t";
    /** The payload key the static datapack dialog (Quick Actions, the pause screen) sends to open the menu. */
    public static final String OPEN_KEY = "open";
    /** Tokens of the mod's own notices: close the dialog, or open the menu again. */
    public static final String CLOSE_TOKEN = "!close", OPEN_TOKEN = "!open";
    /** How long a click waits for Python's show before the screen comes back with {@link #NO_ANSWER}. */
    public static final int ANSWER_TICKS = 60;
    public static final String NO_ANSWER = "The game didn't answer. Try again.";
    public static final String LOADING = "Loading the game menu…";
    public static final String NOT_HERE = "The game menu is only on the arena server.";
    public static final String MENU_SCREEN = "menu_screen";

    public static final int MAX_SIGN_LINES = 4, MAX_SIGN_TEXT = 24, MAX_DISPLAY_LINES = 8, MAX_DISPLAY_TEXT = 64, MAX_SCREENS = 48,
            MAX_TITLE = 64, MAX_BODY = 12, MAX_BODY_TEXT = 200, MAX_INPUTS = 8, MAX_BUTTONS = 24, MAX_LABEL = 64, MAX_OPTIONS = 32,
            MAX_OPTION_ID = 48, MAX_TEXT_INPUT = 32, MAX_BUTTON_LABEL = 40, MAX_TOOLTIP = 200, MIN_WIDTH = 40, MAX_WIDTH = 300,
            MAX_PAYLOAD_KEYS = 16, MAX_PAYLOAD_TEXT = 64, MAX_NOTICE = 200, MAX_ANNOUNCE_TITLE = 40, MAX_ANNOUNCE_SUBTITLE = 80,
            MAX_PLAYERS = 64, MAX_ANNOUNCE = 16, MAX_SLIDER = 1_000_000, DEFAULT_COLUMNS = 2;
    static final Pattern SCREEN = Pattern.compile("[a-z0-9_]{1,32}");
    static final Pattern KEY = Pattern.compile("[a-z_]{1,32}");
    private static final Pattern INDEX = Pattern.compile("[0-9]{1,2}");

    // ---- the model

    /** One line of text: a sign line, a board line, a body line or a notice (notices are never bold). */
    public record Line(String text, String color, boolean bold) {}

    public record Option(String id, String label) {}

    /** A dialog input: its key in the submit payload and its label. */
    public sealed interface Input permits Dropdown, Toggle, Text, Slider {
        String key();
        String label();
    }

    public record Dropdown(String key, String label, List<Option> options, String initial) implements Input {
        boolean has(String id) {
            for (var option : options) if (option.id().equals(id)) return true;
            return false;
        }
    }

    public record Toggle(String key, String label, boolean initial) implements Input {}

    public record Text(String key, String label, String initial, int maxLength) implements Input {}

    public record Slider(String key, String label, int min, int max, int step, int initial) implements Input {
        /** The nearest value on the slider's steps, inside its range. */
        int snap(double value) {
            long steps = Math.round((value - min) / step);
            long top = (long) (max - min) / step;
            return (int) (min + Math.max(0, Math.min(top, steps)) * step);
        }
    }

    public enum Kind { GO, ACTION, SUBMIT, CLOSE }

    /**
     * A button: its label, tooltip (null for none), colour, width (0: the dialog default) and what it does. {@code target}
     * is the screen a GO button shows; {@code payload} is what an ACTION or SUBMIT button dispatches (empty otherwise).
     */
    public record Button(String label, String tooltip, String color, int width, Kind kind, String target, Map<String, Object> payload) {}

    static final Button DEFAULT_EXIT = new Button("Close", null, "white", 0, Kind.CLOSE, null, Map.of());

    public record Screen(String title, List<Line> body, List<Input> inputs, List<Button> buttons, int columns, Button exit) {}

    public record Menu(String home, Map<String, Screen> screens) {}

    public record Show(String screen, Line notice) {}

    public record Announce(List<String> players, String title, String subtitle) {}

    /**
     * One {@code arena_lobby_view}. {@code signs} and {@code display} are null when the request leaves them out (an empty
     * display takes the board down); a null value in {@code menus} drops that player's menu.
     */
    public record View(String name, Map<String, List<Line>> signs, List<Line> display, Map<String, Menu> menus, Map<String, Show> show,
                       List<String> close, List<Announce> announce) {}

    // ---- parsing (strict: an unknown field anywhere is a bad_request)

    public static View parseView(JsonObject request) {
        only(request, Set.of("name", "signs", "display", "menus", "show", "close", "announce"));
        String name = name(request);
        Map<String, List<Line>> signs = request.has("signs") ? signs(request.get("signs")) : null;
        List<Line> display = request.has("display") ? lines(request.get("display"), "display", MAX_DISPLAY_LINES, MAX_DISPLAY_TEXT) : null;
        var menus = new LinkedHashMap<String, Menu>();
        var byKey = new HashMap<String, Menu>();
        if (request.has("menus")) {
            var o = object(request.get("menus"), "menus");
            if (o.size() > MAX_PLAYERS) throw bad("menus holds at most " + MAX_PLAYERS + " players");
            for (var entry : o.entrySet()) {
                String player = player(entry.getKey(), "menus");
                var menu = entry.getValue().isJsonNull() ? null : menu(entry.getValue(), "menus." + player);
                menus.put(player, menu);
                byKey.put(Sessions.key(player), menu);
            }
        }
        var show = new LinkedHashMap<String, Show>();
        if (request.has("show")) {
            var o = object(request.get("show"), "show");
            if (o.size() > MAX_PLAYERS) throw bad("show holds at most " + MAX_PLAYERS + " players");
            for (var entry : o.entrySet()) {
                String player = player(entry.getKey(), "show");
                String where = "show." + player;
                var s = show(entry.getValue(), where);
                String key = Sessions.key(player);
                if (byKey.containsKey(key)) {
                    var menu = byKey.get(key);
                    if (menu == null) throw bad(where + " shows a menu this request drops");
                    if (!menu.screens().containsKey(s.screen())) throw bad(where + ".screen " + s.screen() + " is not in the menu");
                }
                show.put(player, s);
            }
        }
        var close = request.has("close") ? names(request.get("close"), "close", 0) : List.<String>of();
        var announce = new ArrayList<Announce>();
        if (request.has("announce")) {
            var list = array(request.get("announce"), "announce");
            if (list.size() > MAX_ANNOUNCE) throw bad("announce holds at most " + MAX_ANNOUNCE + " entries");
            for (int i = 0; i < list.size(); i++) announce.add(announce(list.get(i), "announce[" + i + "]"));
        }
        return new View(name, signs, display, Collections.unmodifiableMap(menus), Collections.unmodifiableMap(show), close,
                List.copyOf(announce));
    }

    private static Map<String, List<Line>> signs(JsonElement e) {
        var o = object(e, "signs");
        var out = new LinkedHashMap<String, List<Line>>();
        for (var entry : o.entrySet()) {
            String where = "signs." + entry.getKey();
            if (ArenaLobbyStations.byId(entry.getKey()) == null) throw bad(where + " is not a lobby station");
            var sign = object(entry.getValue(), where);
            fields(sign, Set.of("lines"), where);
            if (!sign.has("lines")) throw bad(where + ".lines must be a list of lines");
            out.put(entry.getKey(), lines(sign.get("lines"), where + ".lines", MAX_SIGN_LINES, MAX_SIGN_TEXT));
        }
        return Collections.unmodifiableMap(out);
    }

    private static List<Line> lines(JsonElement e, String where, int maxLines, int maxText) {
        var list = array(e, where);
        if (list.size() > maxLines) throw bad(where + " holds at most " + maxLines + " lines");
        var out = new ArrayList<Line>();
        for (int i = 0; i < list.size(); i++) out.add(line(list.get(i), where + "[" + i + "]", maxText));
        return List.copyOf(out);
    }

    private static Line line(JsonElement e, String where, int maxText) {
        var o = object(e, where);
        fields(o, Set.of("text", "color", "bold"), where);
        return new Line(bounded(text(o, "text"), where + ".text", maxText), color(o, where), flag(o, "bold"));
    }

    private static Menu menu(JsonElement e, String where) {
        var o = object(e, where);
        fields(o, Set.of("home", "screens"), where);
        String home = screenId(o.get("home"), where + ".home");
        var list = object(o.get("screens"), where + ".screens");
        if (list.isEmpty() || list.size() > MAX_SCREENS) throw bad(where + ".screens must hold 1.." + MAX_SCREENS + " screens");
        var screens = new LinkedHashMap<String, Screen>();
        for (var entry : list.entrySet()) {
            String id = entry.getKey();
            if (!SCREEN.matcher(id).matches()) throw bad(where + ".screens " + id + " must match " + SCREEN.pattern());
            screens.put(id, screen(entry.getValue(), where + ".screens." + id));
        }
        if (!screens.containsKey(home)) throw bad(where + ".home " + home + " is not one of its screens");
        for (var entry : screens.entrySet()) {
            var screen = entry.getValue();
            var all = new ArrayList<>(screen.buttons());
            all.add(screen.exit());
            for (var button : all) {
                if (button.kind() == Kind.GO && !screens.containsKey(button.target()))
                    throw bad(where + ".screens." + entry.getKey() + " goes to " + button.target() + ", which is not in the menu");
            }
        }
        return new Menu(home, Collections.unmodifiableMap(screens));
    }

    private static Screen screen(JsonElement e, String where) {
        var o = object(e, where);
        fields(o, Set.of("title", "body", "inputs", "buttons", "columns", "exit"), where);
        String title = bounded(text(o, "title"), where + ".title", MAX_TITLE);
        var body = o.has("body") ? lines(o.get("body"), where + ".body", MAX_BODY, MAX_BODY_TEXT) : List.<Line>of();
        var inputs = new ArrayList<Input>();
        if (o.has("inputs")) {
            var list = array(o.get("inputs"), where + ".inputs");
            if (list.size() > MAX_INPUTS) throw bad(where + ".inputs holds at most " + MAX_INPUTS + " inputs");
            var keys = new HashSet<String>();
            for (int i = 0; i < list.size(); i++) {
                var input = input(list.get(i), where + ".inputs[" + i + "]");
                if (!keys.add(input.key())) throw bad(where + ".inputs has the key " + input.key() + " twice");
                inputs.add(input);
            }
        }
        var buttons = new ArrayList<Button>();
        if (o.has("buttons")) {
            var list = array(o.get("buttons"), where + ".buttons");
            if (list.size() > MAX_BUTTONS) throw bad(where + ".buttons holds at most " + MAX_BUTTONS + " buttons");
            for (int i = 0; i < list.size(); i++) buttons.add(button(list.get(i), where + ".buttons[" + i + "]"));
        }
        int columns = o.has("columns") ? whole(o.get("columns"), where + ".columns", 1, 3) : DEFAULT_COLUMNS;
        var exit = o.has("exit") ? button(o.get("exit"), where + ".exit") : DEFAULT_EXIT;
        return new Screen(title, body, List.copyOf(inputs), List.copyOf(buttons), columns, exit);
    }

    private static Input input(JsonElement e, String where) {
        var o = object(e, where);
        String kind = o.has("kind") ? text(o, "kind") : "";
        var extra = switch (kind) {
            case "dropdown" -> Set.of("options", "initial");
            case "toggle" -> Set.of("initial");
            case "text" -> Set.of("initial", "max_length");
            case "slider" -> Set.of("min", "max", "step", "initial");
            default -> throw bad(where + ".kind must be dropdown, toggle, text or slider");
        };
        var allowed = new HashSet<>(extra);
        allowed.addAll(Set.of("key", "kind", "label"));
        fields(o, allowed, where);
        String key = text(o, "key");
        if (!KEY.matcher(key).matches()) throw bad(where + ".key must match " + KEY.pattern());
        String label = bounded(text(o, "label"), where + ".label", MAX_LABEL);
        return switch (kind) {
            case "dropdown" -> {
                var list = array(o.get("options"), where + ".options");
                if (list.isEmpty() || list.size() > MAX_OPTIONS) throw bad(where + ".options must hold 1.." + MAX_OPTIONS + " options");
                var options = new ArrayList<Option>();
                var ids = new HashSet<String>();
                for (int i = 0; i < list.size(); i++) {
                    String at = where + ".options[" + i + "]";
                    var option = object(list.get(i), at);
                    fields(option, Set.of("id", "label"), at);
                    String id = bounded(text(option, "id"), at + ".id", MAX_OPTION_ID);
                    if (id.isEmpty() || !ids.add(id)) throw bad(at + ".id must be a distinct, non-empty id");
                    options.add(new Option(id, bounded(text(option, "label"), at + ".label", MAX_LABEL)));
                }
                String initial = o.has("initial") ? text(o, "initial") : options.get(0).id();
                if (!ids.contains(initial)) throw bad(where + ".initial must be one of its option ids");
                yield new Dropdown(key, label, List.copyOf(options), initial);
            }
            case "toggle" -> new Toggle(key, label, flag(o, "initial"));
            case "text" -> {
                int max = o.has("max_length") ? whole(o.get("max_length"), where + ".max_length", 1, MAX_TEXT_INPUT) : MAX_TEXT_INPUT;
                String initial = o.has("initial") ? bounded(text(o, "initial"), where + ".initial", max) : "";
                yield new Text(key, label, initial, max);
            }
            default -> {
                int min = whole(o.get("min"), where + ".min", -MAX_SLIDER, MAX_SLIDER);
                int max = whole(o.get("max"), where + ".max", -MAX_SLIDER, MAX_SLIDER);
                if (min >= max) throw bad(where + ".min must be below max");
                int step = o.has("step") ? whole(o.get("step"), where + ".step", 1, max - min) : 1;
                int initial = o.has("initial") ? whole(o.get("initial"), where + ".initial", min, max) : min;
                yield new Slider(key, label, min, max, step, initial);
            }
        };
    }

    private static Button button(JsonElement e, String where) {
        var o = object(e, where);
        fields(o, Set.of("label", "tooltip", "color", "width", "go", "action", "submit", "close"), where);
        String label = bounded(text(o, "label"), where + ".label", MAX_BUTTON_LABEL);
        String tooltip = o.has("tooltip") ? bounded(text(o, "tooltip"), where + ".tooltip", MAX_TOOLTIP) : null;
        String color = color(o, where);
        int width = o.has("width") ? whole(o.get("width"), where + ".width", MIN_WIDTH, MAX_WIDTH) : 0;
        int kinds = (o.has("go") ? 1 : 0) + (o.has("action") ? 1 : 0) + (o.has("submit") ? 1 : 0) + (o.has("close") ? 1 : 0);
        if (kinds != 1) throw bad(where + " must have exactly one of go, action, submit or close");
        if (o.has("go")) return new Button(label, tooltip, color, width, Kind.GO, screenId(o.get("go"), where + ".go"), Map.of());
        if (o.has("close")) {
            if (!flag(o, "close")) throw bad(where + ".close must be true");
            return new Button(label, tooltip, color, width, Kind.CLOSE, null, Map.of());
        }
        var kind = o.has("action") ? Kind.ACTION : Kind.SUBMIT;
        String field = kind == Kind.ACTION ? "action" : "submit";
        return new Button(label, tooltip, color, width, kind, null, payload(o.get(field), where + "." + field));
    }

    /** A flat payload: keys matching {@link #KEY}, values text (at most 64), whole numbers or true/false. */
    private static Map<String, Object> payload(JsonElement e, String where) {
        var o = object(e, where);
        if (o.size() > MAX_PAYLOAD_KEYS) throw bad(where + " holds at most " + MAX_PAYLOAD_KEYS + " keys");
        var out = new LinkedHashMap<String, Object>();
        for (var entry : o.entrySet()) {
            String at = where + "." + entry.getKey();
            if (!KEY.matcher(entry.getKey()).matches()) throw bad(at + ": keys must match " + KEY.pattern());
            var value = entry.getValue();
            if (!value.isJsonPrimitive()) throw bad(at + " must be text, a whole number or true/false");
            var p = value.getAsJsonPrimitive();
            if (p.isString()) out.put(entry.getKey(), bounded(p.getAsString(), at, MAX_PAYLOAD_TEXT));
            else if (p.isBoolean()) out.put(entry.getKey(), p.getAsBoolean());
            else out.put(entry.getKey(), whole(value, at, Integer.MIN_VALUE, Integer.MAX_VALUE));
        }
        return Collections.unmodifiableMap(out);
    }

    private static Show show(JsonElement e, String where) {
        var o = object(e, where);
        fields(o, Set.of("screen", "notice"), where);
        String screen = screenId(o.get("screen"), where + ".screen");
        Line notice = null;
        if (o.has("notice")) {
            String at = where + ".notice";
            var n = object(o.get("notice"), at);
            fields(n, Set.of("text", "color"), at);
            notice = new Line(bounded(text(n, "text"), at + ".text", MAX_NOTICE), color(n, at), false);
        }
        return new Show(screen, notice);
    }

    private static Announce announce(JsonElement e, String where) {
        var o = object(e, where);
        fields(o, Set.of("players", "title", "subtitle"), where);
        var players = names(o.get("players"), where + ".players", 1);
        if (!o.has("title") && !o.has("subtitle")) throw bad(where + " needs a title or a subtitle");
        String title = o.has("title") ? bounded(text(o, "title"), where + ".title", MAX_ANNOUNCE_TITLE) : "";
        String subtitle = o.has("subtitle") ? bounded(text(o, "subtitle"), where + ".subtitle", MAX_ANNOUNCE_SUBTITLE) : "";
        return new Announce(players, title, subtitle);
    }

    private static List<String> names(JsonElement e, String where, int least) {
        var list = array(e, where);
        if (list.size() < least || list.size() > MAX_PLAYERS) throw bad(where + " must hold " + least + ".." + MAX_PLAYERS + " player names");
        var out = new ArrayList<String>();
        for (var n : list) {
            if (!n.isJsonPrimitive() || !n.getAsJsonPrimitive().isString()) throw bad(where + " holds a bad player name");
            out.add(player(n.getAsString(), where));
        }
        return List.copyOf(out);
    }

    private static String player(String name, String where) {
        if (!ArenaRequests.PLAYER.matcher(name).matches()) throw bad(where + " holds a bad player name " + name);
        return name;
    }

    private static String screenId(JsonElement e, String where) {
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString() || !SCREEN.matcher(e.getAsString()).matches())
            throw bad(where + " must be a screen id matching " + SCREEN.pattern());
        return e.getAsString();
    }

    private static JsonObject object(JsonElement e, String where) {
        if (e == null || !e.isJsonObject()) throw bad(where + " must be an object");
        return e.getAsJsonObject();
    }

    private static JsonArray array(JsonElement e, String where) {
        if (e == null || !e.isJsonArray()) throw bad(where + " must be a list");
        return e.getAsJsonArray();
    }

    private static void fields(JsonObject o, Set<String> allowed, String where) {
        for (var key : o.keySet()) if (!allowed.contains(key)) throw bad(where + " has unknown field " + key);
    }

    // ---- clicks

    public static String token(String screen, int index) { return screen + "#" + index; }

    public static String exitToken(String screen) { return screen + "#x"; }

    /** What a click asks for: show a screen, dispatch a payload to Python, close the dialog, open the menu, or nothing. */
    public sealed interface Click permits Go, Dispatch, Close, Open, Ignored {}

    public record Go(String screen) implements Click {}

    /** {@code payload} already carries {@link #MENU_SCREEN} (and a submit's input values). */
    public record Dispatch(String screen, Map<String, Object> payload) implements Click {}

    public record Close() implements Click {}

    public record Open() implements Click {}

    public record Ignored(String why) implements Click {}

    /**
     * Resolves a click token ({@code <screen>#<button index>}, {@code <screen>#x} for the exit button, or one of the mod's
     * own tokens) against the screen the player was shown ({@code shown}) or, for one never shown, the cached menu. The
     * payload always comes from the cache, never from the client; a submit takes the input values the client sent.
     */
    public static Click resolve(Map<String, Screen> shown, Menu menu, String token, Map<String, Object> values) {
        if (token == null) return new Ignored("no token");
        if (token.equals(OPEN_TOKEN)) return new Open();
        if (token.equals(CLOSE_TOKEN)) return new Close();
        int hash = token.lastIndexOf('#');
        if (hash <= 0) return new Ignored("bad token " + token);
        String id = token.substring(0, hash), index = token.substring(hash + 1);
        if (!SCREEN.matcher(id).matches()) return new Ignored("bad token " + token);
        var screen = shown.get(id);
        if (screen == null && menu != null) screen = menu.screens().get(id);
        if (screen == null) return new Ignored("unknown screen " + id);
        Button button;
        if (index.equals("x")) button = screen.exit();
        else {
            if (!INDEX.matcher(index).matches()) return new Ignored("bad token " + token);
            int i = Integer.parseInt(index);
            if (i >= screen.buttons().size()) return new Ignored("no button " + token);
            button = screen.buttons().get(i);
        }
        return switch (button.kind()) {
            case GO -> new Go(button.target());
            case CLOSE -> new Close();
            case ACTION -> new Dispatch(id, withScreen(new LinkedHashMap<>(button.payload()), id));
            case SUBMIT -> {
                var merged = merge(screen, button.payload(), values);
                yield merged == null ? new Ignored("bad input values for " + token) : new Dispatch(id, withScreen(merged, id));
            }
        };
    }

    private static Map<String, Object> withScreen(LinkedHashMap<String, Object> payload, String screen) {
        payload.put(MENU_SCREEN, screen);
        return Collections.unmodifiableMap(payload);
    }

    /**
     * The submit payload with each input's value under its key: a dropdown's option id, a toggle's bool, a text's string,
     * a slider's int (snapped to its steps). Null when a value is missing or not what the input allows.
     */
    static LinkedHashMap<String, Object> merge(Screen screen, Map<String, Object> payload, Map<String, Object> values) {
        var out = new LinkedHashMap<String, Object>(payload);
        for (var input : screen.inputs()) {
            Object raw = values == null ? null : values.get(input.key());
            Object value = switch (input) {
                case Dropdown d -> raw instanceof String s && d.has(s) ? s : null;
                case Toggle t -> raw instanceof Boolean b ? b
                        : raw instanceof Number n && (n instanceof Byte || n instanceof Short || n instanceof Integer || n instanceof Long)
                        ? n.longValue() != 0 : null;
                case Text t -> raw instanceof String s && s.length() <= t.maxLength() ? s : null;
                case Slider s -> raw instanceof Number n && Double.isFinite(n.doubleValue()) ? s.snap(n.doubleValue()) : null;
            };
            if (value == null) return null;
            out.put(input.key(), value);
        }
        return out;
    }

    // ---- each player's menu

    /** One player's cached menu, the screens as last shown to them, and the click waiting for Python's answer. */
    public static final class Session {
        private Menu menu;
        private final Map<String, Screen> shown = new HashMap<>();
        /** The screen whose click waits for an answer ("" for a menu asked for and not yet come), else null. */
        private String pending;
        private int deadline;

        public Menu menu() { return menu; }

        /** The screen to show for {@code id}: itself when the menu has it, else the menu's home; null without a menu. */
        public String target(String id) {
            if (menu == null) return null;
            return id != null && menu.screens().containsKey(id) ? id : menu.home();
        }

        /** The cached screen {@code id}, remembered as what the player now sees; a showing answers any waiting click. */
        public Screen showing(String id) {
            pending = null;
            if (menu == null) return null;
            var screen = menu.screens().get(id);
            if (screen != null) shown.put(id, screen);
            return screen;
        }

        /** A click at tick {@code now}: a dispatch starts the wait for an answer (a second one while waiting is ignored). */
        public Click click(String token, Map<String, Object> values, int now) {
            var click = resolve(shown, menu, token, values);
            if (click instanceof Dispatch d) {
                if (pending != null && now < deadline) return new Ignored("waiting for an answer");
                pending = d.screen();
                deadline = now + ANSWER_TICKS;
            } else if (click instanceof Close) {
                pending = null;
            }
            return click;
        }

        /** The menu was asked for at tick {@code now}. */
        public void loading(int now) {
            pending = "";
            deadline = now + ANSWER_TICKS;
        }

        /** Python answered some other way (a close): nothing waits any more. */
        public void answered() { pending = null; }

        /** The screen to show again with {@link #NO_ANSWER} ("" for none) once the wait has run out, once; else null. */
        public String due(int now) {
            if (pending == null || now < deadline) return null;
            var out = pending;
            pending = null;
            return out;
        }
    }

    /** Sessions by lower-case player name. */
    public static final class Sessions {
        private final Map<String, Session> byName = new HashMap<>();

        static String key(String name) { return name.toLowerCase(Locale.ROOT); }

        public Session get(String name) { return byName.get(key(name)); }

        public Session of(String name) { return byName.computeIfAbsent(key(name), k -> new Session()); }

        /** Replaces the player's cached menu whole; null drops the player's session. */
        public void menu(String name, Menu menu) {
            if (menu == null) byName.remove(key(name));
            else of(name).menu = menu;
        }

        public void drop(String name) { byName.remove(key(name)); }

        /** Every session by lower-case name (the tick looks for clicks that went unanswered). */
        public Map<String, Session> all() { return Collections.unmodifiableMap(byName); }

        public void clear() { byName.clear(); }
    }
}
