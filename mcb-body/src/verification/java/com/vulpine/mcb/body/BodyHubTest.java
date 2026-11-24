package com.vulpine.mcb.body;

import java.util.Map;

/** The hub's pure parts: its query parser, and the rules that keep one body's events from another's reader. */
public final class BodyHubTest {
    private static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    public static void main(String[] args) {
        check(BodyHub.query(null).isEmpty(), "no query is empty");
        check(BodyHub.query("").isEmpty(), "an empty query is empty");
        check(BodyHub.query("after=12").equals(Map.of("after", "12")), "after");
        check(BodyHub.query("after=12&body=MCBot2").equals(Map.of("after", "12", "body", "MCBot2")), "after and body");
        check(BodyHub.query("body=a%20b").get("body").equals("a b"), "percent-decoded");
        check(BodyHub.query("body=a&body=b").get("body").equals("b"), "a repeated key keeps its last value");
        check(BodyHub.query("flag").equals(Map.of("flag", "")), "a bare key has an empty value");
        check(BodyHub.RING >= 2048, "a body's ring is as long as the single ring was");
        check(BodyHub.ADMIT_PER_BODY >= 8, "each body is admitted as many requests per tick as before");
        System.out.println("BodyHubTest ok");
    }
}
