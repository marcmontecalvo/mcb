package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.ByteArrayOutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.HexFormat;
import java.util.zip.DataFormatException;
import java.util.zip.Inflater;

/** Bounded, ordered transport for a plan request that exceeds one bridge frame. */
final class PlanData {
    private static final int MAX_BYTES = 256 * 1024 * 1024;
    private static final int MAX_ENCODED = (MAX_BYTES / 3 + 1) * 4 + 1024;
    private String id, digest;
    private int encodedLength, rawLength;
    private StringBuilder encoded;

    void begin(String id, int encodedLength, int rawLength, String digest) {
        if (this.id != null || id.isEmpty() || id.length() > 64 || encodedLength < 1 || encodedLength > MAX_ENCODED
                || rawLength < 1 || rawLength > MAX_BYTES || !digest.matches("[0-9a-f]{64}"))
            throw new IllegalArgumentException("plan_data_bounds");
        this.id = id; this.encodedLength = encodedLength; this.rawLength = rawLength; this.digest = digest;
        this.encoded = new StringBuilder(Math.min(encodedLength, 16_384));
    }

    void append(String id, int offset, String data) {
        if (!id.equals(this.id) || data.isEmpty() || data.length() > 10_000 || offset != encoded.length()
                || (long)offset + data.length() > encodedLength) throw new IllegalArgumentException("plan_data_part");
        encoded.append(data);
    }

    JsonObject take(String id) {
        if (!id.equals(this.id) || encoded.length() != encodedLength) throw new IllegalArgumentException("plan_data_incomplete");
        try {
            byte[] compressed = Base64.getDecoder().decode(encoded.toString());
            var inflater = new Inflater();
            try {
                inflater.setInput(compressed);
                var bytes = new ByteArrayOutputStream(Math.min(rawLength, 16_384));
                byte[] block = new byte[8192];
                while (!inflater.finished()) {
                    int count = inflater.inflate(block);
                    if (count == 0) throw new IllegalArgumentException("plan_data_encoding");
                    if ((long)bytes.size() + count > rawLength) throw new IllegalArgumentException("plan_data_size");
                    bytes.write(block, 0, count);
                }
                if (bytes.size() != rawLength || inflater.getRemaining() != 0) throw new IllegalArgumentException("plan_data_size");
                byte[] raw = bytes.toByteArray();
                if (!HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw)).equals(digest))
                    throw new IllegalArgumentException("plan_data_digest");
                return JsonParser.parseString(new String(raw, java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();
            } finally { inflater.end(); }
        } catch (DataFormatException | NoSuchAlgorithmException error) {
            throw new IllegalArgumentException("plan_data_encoding", error);
        } finally { abort(); }
    }

    void abort() { id = null; digest = null; encoded = null; encodedLength = rawLength = 0; }
}
