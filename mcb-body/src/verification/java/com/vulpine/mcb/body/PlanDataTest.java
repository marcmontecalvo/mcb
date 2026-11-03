package com.vulpine.mcb.body;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.HexFormat;
import java.util.zip.Deflater;

public final class PlanDataTest {
    public static void main(String[] args) throws Exception {
        byte[] raw = ("{\"plan_id\":\"large\",\"data\":\"" + "a".repeat(20_000) + "\"}").getBytes(StandardCharsets.UTF_8);
        var compressor = new Deflater(); compressor.setInput(raw); compressor.finish();
        byte[] buffer = new byte[raw.length]; int n = compressor.deflate(buffer); compressor.end();
        String encoded = Base64.getEncoder().encodeToString(java.util.Arrays.copyOf(buffer, n));
        String sha = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
        var transfer = new PlanData(); transfer.begin("large", encoded.length(), raw.length, sha);
        try { transfer.append("large", 1, encoded); throw new AssertionError("offset accepted"); }
        catch (IllegalArgumentException expected) { }
        transfer.append("large", 0, encoded);
        if (!transfer.take("large").get("plan_id").getAsString().equals("large")) throw new AssertionError();
        transfer.begin("large", encoded.length(), raw.length, "0".repeat(64)); transfer.append("large", 0, encoded);
        try { transfer.take("large"); throw new AssertionError("digest accepted"); }
        catch (IllegalArgumentException expected) { }
        transfer.begin("large", encoded.length(), 1, sha); transfer.append("large", 0, encoded);
        try { transfer.take("large"); throw new AssertionError("decompression limit accepted"); }
        catch (IllegalArgumentException expected) { }
        try { transfer.begin("large", -1, 1, sha); throw new AssertionError("negative encoded size accepted"); }
        catch (IllegalArgumentException expected) { }
        transfer.begin("large", encoded.length(), raw.length, sha);
        try { transfer.take("large"); throw new AssertionError("incomplete request accepted"); }
        catch (IllegalArgumentException expected) { transfer.abort(); }
        transfer.begin("next", encoded.length(), raw.length, sha); transfer.abort();
        System.out.println("PlanData: ordered request checks passed");
    }
}
