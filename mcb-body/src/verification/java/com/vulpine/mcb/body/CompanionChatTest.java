package com.vulpine.mcb.body;

/** /say and chat rules that need no server. */
public final class CompanionChatTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static boolean rejectsText(String text) {
        try { CompanionChat.requireText(text); return false; } catch (IllegalArgumentException e) { return true; }
    }

    private static boolean rejectsTarget(String to) {
        try { CompanionChat.requireTarget(to); return false; } catch (IllegalArgumentException e) { return true; }
    }

    private static com.google.gson.JsonObject json(String text) {
        return com.google.gson.JsonParser.parseString(text).getAsJsonObject();
    }

    public static void main(String[] args) {
        check(CompanionChat.requireText("hello there").equals("hello there"), "plain text accepted");
        check(CompanionChat.requireText("x".repeat(256)).length() == 256, "256 characters accepted");
        check(rejectsText(""), "empty rejected");
        check(rejectsText(null), "missing rejected");
        check(rejectsText("x".repeat(257)), "257 characters rejected");
        check(rejectsText("a\nb"), "newline rejected");
        check(rejectsText("a\u0000b"), "NUL rejected");
        check(rejectsText("a\u007fb"), "DEL rejected");
        check(rejectsText("a\u0085b"), "C1 control rejected");
        check(!rejectsText("café ☃"), "non-ASCII printable accepted");

        check(CompanionChat.requireTarget(null) == null, "no target means broadcast");
        check(CompanionChat.requireTarget("Marc_1").equals("Marc_1"), "valid name");
        check(CompanionChat.requireTarget("a".repeat(16)).length() == 16, "16-char name");
        check(rejectsTarget(""), "empty name rejected");
        check(rejectsTarget("a".repeat(17)), "17-char name rejected");
        check(rejectsTarget("bad name"), "space rejected");
        check(rejectsTarget("naïve"), "non-ASCII rejected");
        check(rejectsTarget("x\n"), "trailing newline rejected");

        check(McbCommand.reply("ready").equals("Ready."), "/mcb ready answers Ready.");
        check(McbCommand.reply("stop").equals("Stopped."), "/mcb stop answers Stopped.");
        check(McbCommand.reply("goto").equals("Sent."), "other subs answer Sent.");

        check(CompanionChat.speaker("Fennel").equals("Fennel"), "speaker is the body");
        check(CompanionChat.speaker(null).equals("MCB"), "no body: MCB");
        // Several bodies: /say names who speaks; an unknown name never speaks for another body.
        var bodies = java.util.List.of("MCBot", "MCBot2");
        check(CompanionChat.speaker("MCBot2", bodies).equals("MCBot2"), "the named body speaks");
        check(CompanionChat.speaker("mcbot2", bodies).equals("MCBot2"), "matched by name, any case, spoken as it is");
        check(CompanionChat.speaker(null, bodies).equals("MCBot"), "no name: the first body");
        check(CompanionChat.speaker("Stranger", bodies).equals("MCBot"), "an unknown name: the first body");
        check(CompanionChat.speaker("MCBot2", java.util.List.of()).equals("MCB"), "no body: MCB");
        check(CompanionChat.speaker(null, java.util.List.of()).equals("MCB"), "no body, no name: MCB");
        check(CompanionChat.requireSpeaker(null) == null && CompanionChat.requireSpeaker("MCBot2").equals("MCBot2"),
                "the speaker is optional");
        boolean badSpeaker;
        try { CompanionChat.requireSpeaker("bad name"); badSpeaker = false; } catch (IllegalArgumentException e) { badSpeaker = true; }
        check(badSpeaker, "a speaker is a player name");
        check(CompanionChat.broadcast("Fennel", "hi").equals("<Fennel> hi"), "broadcast format");
        check(CompanionChat.whisper("Fennel", "hi").equals("Fennel whispers to you: hi"), "whisper format");

        check(CompanionChat.heard(false), "real players are heard");
        check(!CompanionChat.heard(true), "fake players are not heard");

        check(CompanionChat.truncate("short").equals("short"), "short text unchanged");
        check(CompanionChat.truncate("y".repeat(300)).length() == 256, "long text cut to 256");
        String pair = "y".repeat(255) + "😀"; // the emoji straddles the limit
        check(CompanionChat.truncate(pair).equals("y".repeat(255)), "surrogate pair never split");

        var plain = BodyReset.steps(false);
        var kept = BodyReset.steps(true);
        check(plain.equals(java.util.EnumSet.allOf(BodyReset.Step.class)), "default resets everything");
        for (var step : java.util.List.of(BodyReset.Step.STOP_RIDING, BodyReset.Step.SURVIVAL_MODE,
                BodyReset.Step.ABILITIES, BodyReset.Step.NOT_INVULNERABLE, BodyReset.Step.STANDING_POSE))
            check(kept.contains(step), "keep_state still enforces " + step);
        for (var step : java.util.List.of(BodyReset.Step.CLEAR_INVENTORY, BodyReset.Step.CLEAR_EQUIPMENT,
                BodyReset.Step.CLEAR_EFFECTS, BodyReset.Step.RESET_ATTRIBUTES, BodyReset.Step.RESET_HEALTH_AND_FOOD,
                BodyReset.Step.SELECT_FIRST_SLOT))
            check(!kept.contains(step), "keep_state keeps what was saved: " + step);
        check(!BodyReset.parseKeepState(json("{}")), "absent is false");
        check(BodyReset.parseKeepState(json("{\"keep_state\":true}")), "true");
        check(!BodyReset.parseKeepState(json("{\"keep_state\":false}")), "false");
        for (var bad : new String[] {"1", "\"true\"", "null", "[]"}) {
            boolean rejected;
            try { BodyReset.parseKeepState(json("{\"keep_state\":" + bad + "}")); rejected = false; }
            catch (IllegalArgumentException e) { rejected = true; }
            check(rejected, "keep_state " + bad + " rejected");
        }
        System.out.println("CompanionChatTest ok");
    }
}
