package com.vulpine.mcb.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonPrimitive;
import com.vulpine.mcb.arena.ArenaFail;
import com.vulpine.mcb.arena.ArenaLobbyMenu;
import com.vulpine.mcb.arena.ArenaLobbyMenu.Click;
import com.vulpine.mcb.arena.ArenaLobbyStations;
import com.vulpine.mcb.arena.ArenaLogic;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/** The lobby's game menu without Minecraft: arena_lobby_view parsing, click tokens, submit merging, sessions, stations. */
public final class ArenaLobbyMenuTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static JsonObject json(String text) { return JsonParser.parseString(text).getAsJsonObject(); }

    private static void rejects(Runnable run, String label) {
        try { run.run(); }
        catch (ArenaFail fail) { check(fail.reason.equals("bad_request"), label + " -> " + fail.getMessage()); return; }
        throw new AssertionError("accepted: " + label);
    }

    static final String HOME = "{\"title\":\"MCB Arena\",\"body\":[{\"text\":\"Map: Crossroads\",\"color\":\"white\",\"bold\":false}],"
            + "\"buttons\":[{\"label\":\"Join a Team\",\"go\":\"teams\"},"
            + "{\"label\":\"I'm Ready\",\"color\":\"green\",\"tooltip\":\"Ready up\",\"width\":200,\"action\":{\"action\":\"ready_toggle\"}},"
            + "{\"label\":\"Close\",\"close\":true}],\"columns\":1}";
    static final String TEAMS = "{\"title\":\"Teams\",\"buttons\":[{\"label\":\"Red Team (2/4)\",\"action\":{\"action\":\"join\",\"team\":\"red\"}}],"
            + "\"exit\":{\"label\":\"Back\",\"go\":\"home\"}}";
    static final String SETTINGS = "{\"title\":\"Game Settings\",\"inputs\":["
            + "{\"key\":\"mode\",\"kind\":\"dropdown\",\"label\":\"Mode\",\"options\":[{\"id\":\"teams\",\"label\":\"Team Battle\"},"
            + "{\"id\":\"waves\",\"label\":\"Waves\"}],\"initial\":\"waves\"},"
            + "{\"key\":\"uneven\",\"kind\":\"toggle\",\"label\":\"Uneven teams\",\"initial\":true},"
            + "{\"key\":\"name\",\"kind\":\"text\",\"label\":\"Name\",\"initial\":\"Bob\",\"max_length\":16},"
            + "{\"key\":\"lives\",\"kind\":\"slider\",\"label\":\"Lives\",\"min\":1,\"max\":9,\"step\":2,\"initial\":3}],"
            + "\"buttons\":[{\"label\":\"Save\",\"submit\":{\"action\":\"settings\",\"then\":\"home\",\"count\":2,\"host\":true}}]}";
    static final String MENU = "{\"home\":\"home\",\"screens\":{\"home\":" + HOME + ",\"teams\":" + TEAMS + ",\"settings\":" + SETTINGS + "}}";
    static final String VIEW = "{\"name\":\"lobby\",\"request_id\":\"r1\","
            + "\"signs\":{\"join_red\":{\"lines\":[{\"text\":\"RED TEAM\",\"color\":\"red\",\"bold\":true},{\"text\":\"2/4\"}]}},"
            + "\"display\":[{\"text\":\"CROSSROADS\",\"color\":\"gold\",\"bold\":true},{\"text\":\"Team Battle\"}],"
            + "\"menus\":{\"Ann\":" + MENU + ",\"Bob\":null},"
            + "\"show\":{\"Ann\":{\"screen\":\"home\",\"notice\":{\"text\":\"You joined Red\",\"color\":\"green\"}}},"
            + "\"close\":[\"Bob\"],"
            + "\"announce\":[{\"players\":[\"Ann\"],\"title\":\"Settings changed\",\"subtitle\":\"Map is now Vault: ready up again\"}]}";

    private static JsonObject view() { return json(VIEW); }

    private static JsonObject menuOf(JsonObject view) { return view.getAsJsonObject("menus").getAsJsonObject("Ann"); }

    private static JsonObject screen(JsonObject view, String id) { return menuOf(view).getAsJsonObject("screens").getAsJsonObject(id); }

    private static JsonObject settingsInput(JsonObject view, int i) {
        return screen(view, "settings").getAsJsonArray("inputs").get(i).getAsJsonObject();
    }

    private static JsonObject homeButton(JsonObject view, int i) {
        return screen(view, "home").getAsJsonArray("buttons").get(i).getAsJsonObject();
    }

    private static void refuses(String label, Consumer<JsonObject> change) {
        var request = view();
        change.accept(request);
        rejects(() -> ArenaLobbyMenu.parseView(request), label);
    }

    private static JsonArray lines(int n, int length) {
        var out = new JsonArray();
        for (int i = 0; i < n; i++) {
            var line = new JsonObject();
            line.addProperty("text", "x".repeat(length));
            out.add(line);
        }
        return out;
    }

    public static void main(String[] args) {
        protocol();
        parsing();
        refusals();
        tokens();
        submit();
        sessions();
        stations();
        System.out.println("ArenaLobbyMenuTest ok");
    }

    private static void protocol() {
        check(ArenaLogic.PROTOCOL == 7, "protocol 7 for arena_lobby_view");
        check(ArenaLogic.isOperationName("arena_lobby_view"), "arena_lobby_view is an arena op");
        check(ArenaLogic.OPS.size() == 12, "twelve ops");
    }

    private static void parsing() {
        var view = ArenaLobbyMenu.parseView(view());
        check(view.name().equals("lobby"), "name");
        var red = view.signs().get("join_red");
        check(red.size() == 2 && red.get(0).text().equals("RED TEAM") && red.get(0).color().equals("red") && red.get(0).bold(), "sign line");
        check(red.get(1).color().equals("white") && !red.get(1).bold(), "sign line defaults");
        check(view.display().size() == 2 && view.display().get(0).color().equals("gold"), "display");
        check(view.menus().containsKey("Bob") && view.menus().get("Bob") == null, "null menu drops");
        var menu = view.menus().get("Ann");
        check(menu.home().equals("home") && menu.screens().size() == 3, "menu screens");
        var home = menu.screens().get("home");
        check(home.title().equals("MCB Arena") && home.columns() == 1 && home.body().size() == 1, "home screen");
        check(home.buttons().size() == 3, "home buttons");
        var go = home.buttons().get(0);
        check(go.kind() == ArenaLobbyMenu.Kind.GO && go.target().equals("teams") && go.color().equals("white")
                && go.tooltip() == null && go.width() == 0, "go button with defaults");
        var ready = home.buttons().get(1);
        check(ready.kind() == ArenaLobbyMenu.Kind.ACTION && ready.payload().equals(Map.of("action", "ready_toggle"))
                && ready.color().equals("green") && ready.tooltip().equals("Ready up") && ready.width() == 200, "action button");
        check(home.buttons().get(2).kind() == ArenaLobbyMenu.Kind.CLOSE, "close button");
        check(home.exit().kind() == ArenaLobbyMenu.Kind.CLOSE && home.exit().label().equals("Close"), "default exit is Close");
        var teams = menu.screens().get("teams");
        check(teams.columns() == 2 && teams.exit().kind() == ArenaLobbyMenu.Kind.GO && teams.exit().target().equals("home"), "teams exit");
        var settings = menu.screens().get("settings");
        check(settings.inputs().size() == 4, "settings inputs");
        check(settings.inputs().get(0) instanceof ArenaLobbyMenu.Dropdown d && d.initial().equals("waves") && d.options().size() == 2,
                "dropdown");
        check(settings.inputs().get(1) instanceof ArenaLobbyMenu.Toggle t && t.initial(), "toggle");
        check(settings.inputs().get(2) instanceof ArenaLobbyMenu.Text t && t.initial().equals("Bob") && t.maxLength() == 16, "text");
        check(settings.inputs().get(3) instanceof ArenaLobbyMenu.Slider s && s.min() == 1 && s.max() == 9 && s.step() == 2 && s.initial() == 3,
                "slider");
        var save = settings.buttons().get(0);
        check(save.kind() == ArenaLobbyMenu.Kind.SUBMIT && save.payload().get("count").equals(2) && save.payload().get("host").equals(true),
                "submit payload keeps ints and bools");
        var show = view.show().get("Ann");
        check(show.screen().equals("home") && show.notice().text().equals("You joined Red") && show.notice().color().equals("green"), "show");
        check(view.close().equals(List.of("Bob")), "close");
        var announce = view.announce().get(0);
        check(announce.players().equals(List.of("Ann")) && announce.title().equals("Settings changed")
                && announce.subtitle().startsWith("Map is now"), "announce");

        var bare = ArenaLobbyMenu.parseView(json("{\"name\":\"lobby\"}"));
        check(bare.signs() == null && bare.display() == null && bare.menus().isEmpty() && bare.show().isEmpty()
                && bare.close().isEmpty() && bare.announce().isEmpty(), "every field but name is optional");
        var cleared = ArenaLobbyMenu.parseView(json("{\"name\":\"lobby\",\"display\":[]}"));
        check(cleared.display() != null && cleared.display().isEmpty(), "an empty display removes the board");
        var subtitleOnly = ArenaLobbyMenu.parseView(json("{\"name\":\"lobby\",\"announce\":[{\"players\":[\"Ann\"],\"subtitle\":\"Go\"}]}"));
        check(subtitleOnly.announce().get(0).title().isEmpty(), "announce title is optional when a subtitle is given");
        var minimal = ArenaLobbyMenu.parseView(json("{\"name\":\"lobby\",\"menus\":{\"Ann\":{\"home\":\"h\",\"screens\":{\"h\":"
                + "{\"title\":\"Hi\",\"inputs\":[{\"key\":\"pick\",\"kind\":\"dropdown\",\"label\":\"P\",\"options\":[{\"id\":\"a\",\"label\":\"A\"}]},"
                + "{\"key\":\"on\",\"kind\":\"toggle\",\"label\":\"On\"},{\"key\":\"txt\",\"kind\":\"text\",\"label\":\"T\"},"
                + "{\"key\":\"num\",\"kind\":\"slider\",\"label\":\"N\",\"min\":0,\"max\":4}]}}}}}"));
        var h = minimal.menus().get("Ann").screens().get("h");
        check(h.body().isEmpty() && h.buttons().isEmpty() && h.columns() == 2, "screen defaults");
        check(h.inputs().get(0) instanceof ArenaLobbyMenu.Dropdown d && d.initial().equals("a"), "dropdown initial defaults to the first");
        check(h.inputs().get(1) instanceof ArenaLobbyMenu.Toggle t && !t.initial(), "toggle defaults off");
        check(h.inputs().get(2) instanceof ArenaLobbyMenu.Text t && t.initial().isEmpty() && t.maxLength() == 32, "text defaults");
        check(h.inputs().get(3) instanceof ArenaLobbyMenu.Slider s && s.step() == 1 && s.initial() == 0, "slider defaults");
    }

    private static void refusals() {
        refuses("unknown top-level field", v -> v.addProperty("bogus", 1));
        refuses("name missing", v -> v.remove("name"));
        refuses("bad arena name", v -> v.addProperty("name", "Lobby!"));
        refuses("unknown station", v -> v.getAsJsonObject("signs").add("join_purple", json("{\"lines\":[]}")));
        refuses("sign unknown field", v -> v.getAsJsonObject("signs").getAsJsonObject("join_red").addProperty("glow", true));
        refuses("five sign lines", v -> v.getAsJsonObject("signs").getAsJsonObject("join_red").add("lines", lines(5, 3)));
        refuses("long sign line", v -> v.getAsJsonObject("signs").getAsJsonObject("join_red").add("lines", lines(1, 25)));
        refuses("bad sign colour", v -> v.getAsJsonObject("signs").getAsJsonObject("join_red").getAsJsonArray("lines").get(0)
                .getAsJsonObject().addProperty("color", "pink"));
        refuses("sign line unknown field", v -> v.getAsJsonObject("signs").getAsJsonObject("join_red").getAsJsonArray("lines").get(0)
                .getAsJsonObject().addProperty("italic", true));
        refuses("signs not an object", v -> v.add("signs", new JsonArray()));
        refuses("nine display lines", v -> v.add("display", lines(9, 3)));
        refuses("long display line", v -> v.add("display", lines(1, 65)));
        refuses("bad menu player name", v -> v.getAsJsonObject("menus").add("A n", json(MENU)));
        refuses("menu unknown field", v -> menuOf(v).addProperty("version", 1));
        refuses("home missing", v -> menuOf(v).addProperty("home", "lobby"));
        refuses("no home", v -> menuOf(v).remove("home"));
        refuses("too many screens", v -> {
            for (int i = 0; i < 46; i++) menuOf(v).getAsJsonObject("screens").add("s" + i, json("{\"title\":\"x\"}"));
        });
        refuses("bad screen id", v -> menuOf(v).getAsJsonObject("screens").add("Home", json("{\"title\":\"x\"}")));
        refuses("screen unknown field", v -> screen(v, "home").addProperty("icon", "x"));
        refuses("no title", v -> screen(v, "home").remove("title"));
        refuses("long title", v -> screen(v, "home").addProperty("title", "x".repeat(65)));
        refuses("thirteen body lines", v -> screen(v, "home").add("body", lines(13, 3)));
        refuses("long body line", v -> screen(v, "home").add("body", lines(1, 201)));
        refuses("columns 0", v -> screen(v, "home").addProperty("columns", 0));
        refuses("columns 4", v -> screen(v, "home").addProperty("columns", 4));
        refuses("too many buttons", v -> {
            var many = new JsonArray();
            for (int i = 0; i < 25; i++) many.add(json("{\"label\":\"b\",\"close\":true}"));
            screen(v, "home").add("buttons", many);
        });
        refuses("too many inputs", v -> {
            var many = new JsonArray();
            for (int i = 0; i < 9; i++) many.add(json("{\"key\":\"k" + "abcdefghi".charAt(i) + "\",\"kind\":\"toggle\",\"label\":\"x\"}"));
            screen(v, "settings").add("inputs", many);
        });
        refuses("duplicate input key", v -> settingsInput(v, 1).addProperty("key", "mode"));
        refuses("bad input key", v -> settingsInput(v, 1).addProperty("key", "Mode"));
        refuses("input key with a digit", v -> settingsInput(v, 1).addProperty("key", "mode2"));
        refuses("unknown input kind", v -> settingsInput(v, 1).addProperty("kind", "checkbox"));
        refuses("input unknown field", v -> settingsInput(v, 1).addProperty("min", 1));
        refuses("long input label", v -> settingsInput(v, 1).addProperty("label", "x".repeat(65)));
        refuses("no dropdown options", v -> settingsInput(v, 0).add("options", new JsonArray()));
        refuses("too many dropdown options", v -> {
            var many = new JsonArray();
            for (int i = 0; i < 33; i++) many.add(json("{\"id\":\"o" + i + "\",\"label\":\"x\"}"));
            settingsInput(v, 0).add("options", many);
        });
        refuses("dropdown initial not an option", v -> settingsInput(v, 0).addProperty("initial", "race"));
        refuses("duplicate dropdown option", v -> settingsInput(v, 0).getAsJsonArray("options").add(json("{\"id\":\"teams\",\"label\":\"x\"}")));
        refuses("long option id", v -> settingsInput(v, 0).getAsJsonArray("options").get(0).getAsJsonObject().addProperty("id", "x".repeat(49)));
        refuses("toggle initial not a bool", v -> settingsInput(v, 1).addProperty("initial", "yes"));
        refuses("text max_length 0", v -> settingsInput(v, 2).addProperty("max_length", 0));
        refuses("text max_length 33", v -> settingsInput(v, 2).addProperty("max_length", 33));
        refuses("text initial over max_length", v -> settingsInput(v, 2).addProperty("initial", "x".repeat(17)));
        refuses("slider min not below max", v -> settingsInput(v, 3).addProperty("min", 9));
        refuses("slider step 0", v -> settingsInput(v, 3).addProperty("step", 0));
        refuses("slider initial out of range", v -> settingsInput(v, 3).addProperty("initial", 10));
        refuses("slider fraction", v -> settingsInput(v, 3).addProperty("max", 9.5));
        refuses("slider without max", v -> settingsInput(v, 3).remove("max"));
        refuses("long button label", v -> homeButton(v, 0).addProperty("label", "x".repeat(41)));
        refuses("long tooltip", v -> homeButton(v, 0).addProperty("tooltip", "x".repeat(201)));
        refuses("narrow button", v -> homeButton(v, 0).addProperty("width", 39));
        refuses("wide button", v -> homeButton(v, 0).addProperty("width", 301));
        refuses("bad button colour", v -> homeButton(v, 0).addProperty("color", "orange"));
        refuses("button unknown field", v -> homeButton(v, 0).addProperty("icon", "x"));
        refuses("button with no kind", v -> homeButton(v, 0).remove("go"));
        refuses("button with two kinds", v -> homeButton(v, 0).addProperty("close", true));
        refuses("close false", v -> homeButton(v, 2).addProperty("close", false));
        refuses("go to a missing screen", v -> homeButton(v, 0).addProperty("go", "shop"));
        refuses("exit go to a missing screen", v -> screen(v, "teams").getAsJsonObject("exit").addProperty("go", "shop"));
        refuses("nested payload", v -> homeButton(v, 1).getAsJsonObject("action").add("args", new JsonObject()));
        refuses("payload list", v -> homeButton(v, 1).getAsJsonObject("action").add("args", new JsonArray()));
        refuses("bad payload key", v -> homeButton(v, 1).getAsJsonObject("action").addProperty("Team", "red"));
        refuses("long payload text", v -> homeButton(v, 1).getAsJsonObject("action").addProperty("team", "x".repeat(65)));
        refuses("fractional payload number", v -> homeButton(v, 1).getAsJsonObject("action").addProperty("count", 1.5));
        refuses("payload null", v -> homeButton(v, 1).getAsJsonObject("action").add("team", com.google.gson.JsonNull.INSTANCE));
        refuses("action not an object", v -> homeButton(v, 1).add("action", new JsonPrimitive("ready")));
        refuses("show unknown screen of a sent menu", v -> v.getAsJsonObject("show").getAsJsonObject("Ann").addProperty("screen", "shop"));
        refuses("show for a dropped menu", v -> v.getAsJsonObject("show").add("Bob", json("{\"screen\":\"home\"}")));
        refuses("show bad screen id", v -> v.getAsJsonObject("show").add("Cy", json("{\"screen\":\"Home!\"}")));
        refuses("show unknown field", v -> v.getAsJsonObject("show").getAsJsonObject("Ann").addProperty("menu", "x"));
        refuses("long notice", v -> v.getAsJsonObject("show").getAsJsonObject("Ann").getAsJsonObject("notice").addProperty("text", "x".repeat(201)));
        refuses("notice bold", v -> v.getAsJsonObject("show").getAsJsonObject("Ann").getAsJsonObject("notice").addProperty("bold", true));
        refuses("close not a list", v -> v.addProperty("close", "Bob"));
        refuses("close bad name", v -> v.getAsJsonArray("close").add("B b"));
        refuses("announce no players", v -> v.getAsJsonArray("announce").get(0).getAsJsonObject().add("players", new JsonArray()));
        refuses("announce long title", v -> v.getAsJsonArray("announce").get(0).getAsJsonObject().addProperty("title", "x".repeat(41)));
        refuses("announce long subtitle", v -> v.getAsJsonArray("announce").get(0).getAsJsonObject().addProperty("subtitle", "x".repeat(81)));
        refuses("announce no text", v -> {
            var a = v.getAsJsonArray("announce").get(0).getAsJsonObject();
            a.remove("title");
            a.remove("subtitle");
        });
        refuses("announce unknown field", v -> v.getAsJsonArray("announce").get(0).getAsJsonObject().addProperty("fade", 10));
    }

    private static ArenaLobbyMenu.Menu menu() { return ArenaLobbyMenu.parseView(view()).menus().get("Ann"); }

    private static void tokens() {
        var menu = menu();
        Map<String, ArenaLobbyMenu.Screen> shown = Map.of();
        check(ArenaLobbyMenu.token("home", 1).equals("home#1") && ArenaLobbyMenu.exitToken("home").equals("home#x"), "token format");
        check(ArenaLobbyMenu.resolve(shown, menu, "home#0", Map.of()).equals(new ArenaLobbyMenu.Go("teams")), "go");
        var ready = ArenaLobbyMenu.resolve(shown, menu, "home#1", Map.of());
        check(ready.equals(new ArenaLobbyMenu.Dispatch("home", Map.of("action", "ready_toggle", "menu_screen", "home"))), "action: " + ready);
        check(ArenaLobbyMenu.resolve(shown, menu, "home#2", Map.of()) instanceof ArenaLobbyMenu.Close, "close");
        check(ArenaLobbyMenu.resolve(shown, menu, "home#x", Map.of()) instanceof ArenaLobbyMenu.Close, "default exit closes");
        check(ArenaLobbyMenu.resolve(shown, menu, "teams#x", Map.of()).equals(new ArenaLobbyMenu.Go("home")), "exit go");
        var join = ArenaLobbyMenu.resolve(shown, menu, "teams#0", Map.of());
        check(join.equals(new ArenaLobbyMenu.Dispatch("teams", Map.of("action", "join", "team", "red", "menu_screen", "teams"))), "join");
        check(ArenaLobbyMenu.resolve(shown, menu, ArenaLobbyMenu.CLOSE_TOKEN, Map.of()) instanceof ArenaLobbyMenu.Close, "close token");
        check(ArenaLobbyMenu.resolve(shown, menu, ArenaLobbyMenu.OPEN_TOKEN, Map.of()) instanceof ArenaLobbyMenu.Open, "open token");
        check(ArenaLobbyMenu.resolve(shown, null, ArenaLobbyMenu.OPEN_TOKEN, Map.of()) instanceof ArenaLobbyMenu.Open, "open without a menu");
        for (var bad : List.of("home#3", "home#-1", "home#abc", "home", "nope#0", "#0", "home#", "home#01x", "Home#0", ""))
            check(ArenaLobbyMenu.resolve(shown, menu, bad, Map.of()) instanceof ArenaLobbyMenu.Ignored, "bad token " + bad);
        check(ArenaLobbyMenu.resolve(shown, null, "home#0", Map.of()) instanceof ArenaLobbyMenu.Ignored, "no menu");
        check(ArenaLobbyMenu.resolve(shown, menu, null, Map.of()) instanceof ArenaLobbyMenu.Ignored, "no token");
    }

    private static Click save(Map<String, Object> values) {
        return ArenaLobbyMenu.resolve(Map.of(), menu(), "settings#0", values);
    }

    private static Map<String, Object> values(Object mode, Object uneven, Object name, Object lives) {
        var out = new HashMap<String, Object>();
        if (mode != null) out.put("mode", mode);
        if (uneven != null) out.put("uneven", uneven);
        if (name != null) out.put("name", name);
        if (lives != null) out.put("lives", lives);
        out.put(ArenaLobbyMenu.TOKEN_KEY, "settings#0");
        out.put("stray", "ignored");
        return out;
    }

    private static void submit() {
        var click = save(values("teams", (byte) 0, "Zed", 5.0f));
        var want = new HashMap<String, Object>(Map.of("action", "settings", "then", "home", "count", 2, "host", true,
                "mode", "teams", "uneven", false, "name", "Zed", "lives", 5));
        want.put("menu_screen", "settings");
        check(click instanceof ArenaLobbyMenu.Dispatch d && d.screen().equals("settings") && d.payload().equals(want),
                "submit merges every input by kind: " + click);
        check(save(values("waves", true, "", 1.0f)) instanceof ArenaLobbyMenu.Dispatch d
                && d.payload().get("uneven").equals(true) && d.payload().get("name").equals("") && d.payload().get("lives").equals(1),
                "toggle as a bool, empty text");
        check(save(values("waves", (byte) 1, "A", 4.4f)) instanceof ArenaLobbyMenu.Dispatch d && d.payload().get("lives").equals(5)
                && d.payload().get("uneven").equals(true), "slider snaps to its step");
        check(save(values("waves", (byte) 1, "A", 12.0f)) instanceof ArenaLobbyMenu.Dispatch d && d.payload().get("lives").equals(9),
                "slider clamps to its max");
        check(save(values("waves", (byte) 1, "A", -3.0f)) instanceof ArenaLobbyMenu.Dispatch d && d.payload().get("lives").equals(1),
                "slider clamps to its min");
        check(save(values("race", (byte) 1, "A", 3.0f)) instanceof ArenaLobbyMenu.Ignored, "dropdown value not an option");
        check(save(values(null, (byte) 1, "A", 3.0f)) instanceof ArenaLobbyMenu.Ignored, "missing input value");
        check(save(values("waves", (byte) 1, "x".repeat(17), 3.0f)) instanceof ArenaLobbyMenu.Ignored, "text over max_length");
        check(save(values("waves", "yes", "A", 3.0f)) instanceof ArenaLobbyMenu.Ignored, "toggle of the wrong type");
        check(save(values("waves", (byte) 1, 7, 3.0f)) instanceof ArenaLobbyMenu.Ignored, "text of the wrong type");
        check(save(values("waves", (byte) 1, "A", "3")) instanceof ArenaLobbyMenu.Ignored, "slider of the wrong type");
        check(save(values("waves", (byte) 1, "A", Float.NaN)) instanceof ArenaLobbyMenu.Ignored, "slider NaN");
        // an action button ignores whatever values come along
        check(ArenaLobbyMenu.resolve(Map.of(), menu(), "home#1", values("x", 1, 2, 3)).equals(
                new ArenaLobbyMenu.Dispatch("home", Map.of("action", "ready_toggle", "menu_screen", "home"))), "action ignores values");
    }

    private static void sessions() {
        var sessions = new ArenaLobbyMenu.Sessions();
        check(sessions.get("Ann") == null, "no session yet");
        sessions.menu("Ann", menu());
        var ann = sessions.get("ann");
        check(ann != null && sessions.get("ANN") == ann && ann.menu() != null, "sessions keyed by lower-case name");
        check(ann.target("teams").equals("teams") && ann.target("shop").equals("home") && ann.target(null).equals("home"),
                "unknown screens fall back to home");
        var shown = ann.showing("home");
        check(shown != null && shown.title().equals("MCB Arena"), "showing returns the cached screen");
        var click = ann.click("home#1", Map.of(), 100);
        check(click instanceof ArenaLobbyMenu.Dispatch, "dispatch");
        check(ann.click("home#1", Map.of(), 110) instanceof ArenaLobbyMenu.Ignored, "a second action while waiting is ignored");
        check(ann.click("home#0", Map.of(), 110) instanceof ArenaLobbyMenu.Go, "go still works while waiting");
        check(ann.due(159) == null, "not due before 60 ticks");
        check("home".equals(ann.due(160)), "due after 60 ticks, for the clicked screen");
        check(ann.due(161) == null, "due once");
        ann.click("home#1", Map.of(), 200);
        ann.showing("home");
        check(ann.due(400) == null, "a show answers the click");
        ann.click("home#1", Map.of(), 500);
        check(ann.click("home#x", Map.of(), 501) instanceof ArenaLobbyMenu.Close && ann.due(600) == null, "closing cancels the wait");
        ann.click("home#1", Map.of(), 700);
        ann.answered();
        check(ann.due(800) == null, "a close op answers the click");

        // what the player sees wins over a newer cached menu
        var changed = view();
        var buttons = screen(changed, "home").getAsJsonArray("buttons");
        buttons.set(1, json("{\"label\":\"Leave Game\",\"action\":{\"action\":\"leave\"}}"));
        sessions.menu("Ann", ArenaLobbyMenu.parseView(changed).menus().get("Ann"));
        check(sessions.get("Ann") == ann, "a new menu keeps the session");
        var stale = ann.click("home#1", Map.of(), 1000);
        check(stale instanceof ArenaLobbyMenu.Dispatch d && d.payload().get("action").equals("ready_toggle"), "token resolves against the shown screen");
        ann.answered();
        ann.showing("home");
        var fresh = ann.click("home#1", Map.of(), 1100);
        check(fresh instanceof ArenaLobbyMenu.Dispatch d && d.payload().get("action").equals("leave"), "after re-showing, the new menu");
        ann.answered();
        check(ann.click("teams#0", Map.of(), 1200) instanceof ArenaLobbyMenu.Dispatch d2 && d2.screen().equals("teams"),
                "a screen never shown resolves against the cache");
        ann.answered();

        // loading: no menu yet
        var cy = sessions.of("Cy");
        check(cy.menu() == null && cy.target("home") == null, "no menu, no target");
        cy.loading(50);
        check(cy.due(109) == null && "".equals(cy.due(110)), "a menu that never came is due with no screen");
        cy.loading(200);
        sessions.menu("Cy", menu());
        cy.showing("home");
        check(cy.due(300) == null, "the menu arriving answers the load");

        sessions.menu("Ann", null);
        check(sessions.get("Ann") == null, "a null menu drops the session");
        sessions.of("Dee");
        sessions.drop("dee");
        check(sessions.get("Dee") == null, "drop on disconnect");
    }

    private static void stations() {
        var ids = ArenaLobbyStations.STATIONS.stream().map(ArenaLobbyStations.Station::id).toList();
        check(ids.equals(List.of("join_red", "join_blue", "join_green", "join_yellow", "ready", "help")), "station ids: " + ids);
        int[] origin = {-12, 100, -12};
        check(ArenaLobbyStations.at(null, 0, 0, 0) == null, "null origin");
        check(ArenaLobbyStations.at(origin, -12, 100, -12) == null, "not a station");
        var red = ArenaLobbyStations.byId("join_red");
        check(red.signX() == 2 && red.signY() == 3 && red.signZ() == 7 && red.buttonX() == 2 && red.buttonY() == 2 && red.buttonZ() == 7,
                "join_red cells");
        check(ArenaLobbyStations.at(origin, -10, 103, -5) == red && ArenaLobbyStations.at(origin, -10, 102, -5) == red, "sign and button");
        check(red.click().equals(Map.of("action", "join", "team", "red", "station", "join_red")), "click payload names the station");
        var ready = ArenaLobbyStations.byId("ready");
        check(ready.payload().equals(Map.of("action", "ready_toggle")) && ready.signX() == 22 && ready.buttonY() == 2, "ready");
        var help = ArenaLobbyStations.byId("help");
        check(help.help() && help.payload().isEmpty() && !help.hasButton() && help.signX() == 12 && help.signZ() == 22, "help");
        check(ArenaLobbyStations.at(origin, origin[0] - 1, origin[1] - 1, origin[2] - 1) == null, "help has no button cell");
        check(ArenaLobbyStations.at(origin, origin[0] + 12, origin[1] + 3, origin[2] + 22) == help, "help sign");
        check(ArenaLobbyStations.byId("nope") == null, "unknown id");
        for (var s : ArenaLobbyStations.STATIONS) {
            if (s.help()) continue;
            check(ArenaLobbyStations.at(origin, origin[0] + s.buttonX(), origin[1] + s.buttonY(), origin[2] + s.buttonZ()) == s,
                    "button of " + s.id());
            check(!s.isSign(s.buttonX(), s.buttonY(), s.buttonZ()) && s.isSign(s.signX(), s.signY(), s.signZ()), "sign test " + s.id());
        }
    }
}
