package com.vulpine.mcb.arena;

import java.util.Map;

/** Where the arena runtime sends its events: the body bridge's event log (BodyRuntime supplies it). */
@FunctionalInterface
public interface ArenaEmit {
    void emit(String type, Map<String, Object> data);
}
