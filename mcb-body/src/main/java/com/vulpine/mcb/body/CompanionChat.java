package com.vulpine.mcb.body;

import java.util.regex.Pattern;

/** Pure rules for companion chat: what /say may send, who it may whisper to, and which chat lines the bot hears. */
final class CompanionChat {
    static final int MAX_TEXT = 256;
    static final String DEFAULT_SPEAKER = "MCB";
    private static final Pattern PLAYER_NAME = Pattern.compile("[A-Za-z0-9_]{1,16}");

    private CompanionChat() {}

    /** Text for /say and /mcb ask: 1 to 256 characters, no control characters. Throws IllegalArgumentException. */
    static String requireText(String text) {
        if (text == null || text.isEmpty() || text.length() > MAX_TEXT) throw new IllegalArgumentException("invalid_text");
        if (text.codePoints().anyMatch(Character::isISOControl)) throw new IllegalArgumentException("invalid_text");
        return text;
    }

    /** Optional whisper target; null or absent means broadcast. Throws IllegalArgumentException for a bad name. */
    static String requireTarget(String to) {
        if (to == null) return null;
        if (!PLAYER_NAME.matcher(to).matches()) throw new IllegalArgumentException("invalid_to");
        return to;
    }

    /** The body's name, or MCB when no body is spawned. */
    static String speaker(String bodyName) {
        return bodyName == null || bodyName.isEmpty() ? DEFAULT_SPEAKER : bodyName;
    }

    /**
     * Who speaks when several bodies may: the requested one if a body of that name exists (any case, spoken as the body
     * spells it), else the first body, else MCB. A name that matches no body never makes up a speaker.
     */
    static String speaker(String requested, java.util.List<String> bodyNames) {
        if (requested != null) {
            for (var name : bodyNames) if (name != null && name.equalsIgnoreCase(requested)) return name;
        }
        return speaker(bodyNames.isEmpty() ? null : bodyNames.get(0));
    }

    /** Optional {@code /say} speaker; null or absent means the default body. Throws IllegalArgumentException for a bad name. */
    static String requireSpeaker(String name) {
        if (name == null) return null;
        if (!PLAYER_NAME.matcher(name).matches()) throw new IllegalArgumentException("invalid_speaker");
        return name;
    }

    static String whisper(String speaker, String text) { return speaker + " whispers to you: " + text; }

    static String broadcast(String speaker, String text) { return "<" + speaker + "> " + text; }

    /** The bot never hears Carpet fake players (itself or any other body). */
    static boolean heard(boolean senderIsFakePlayer) { return !senderIsFakePlayer; }

    /** A chat line cut to the event limit, never splitting a surrogate pair. */
    static String truncate(String text) {
        if (text.length() <= MAX_TEXT) return text;
        int end = MAX_TEXT;
        if (Character.isHighSurrogate(text.charAt(end - 1))) end--;
        return text.substring(0, end);
    }
}
