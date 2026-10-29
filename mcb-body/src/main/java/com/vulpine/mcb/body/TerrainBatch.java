package com.vulpine.mcb.body;

import java.util.AbstractMap;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Captured immutable facts only: lazy palette construction runs in HTTP serialization. */
final class TerrainBatch extends AbstractMap<String,Object> {
    private List<Map<String,Object>> cells;
    private final int sampleTick;
    private volatile Map<String,Object> encoded;

    TerrainBatch(List<Map<String,Object>> cells,int sampleTick) {
        // PolicySensor owns immutable maps, properties and collision-box lists.
        this.cells=List.copyOf(cells); this.sampleTick=sampleTick;
    }

    private static int integer(Object value) {
        if(!(value instanceof Integer n)) throw new IllegalArgumentException("terrain_integer");
        return n;
    }

    private Map<String,Object> payload() {
        var result=encoded;
        if(result!=null) return result;
        synchronized(this) {
            if(encoded!=null) return encoded;
            long started=System.nanoTime();
            var palette=new ArrayList<Map<String,Object>>();
            var indices=new LinkedHashMap<Map<String,Object>,Integer>();
            var sections=new LinkedHashMap<List<Integer>,ArrayList<List<Integer>>>();
            for(var cell:cells) {
                if(!(cell.get("position") instanceof List<?> p) || p.size()!=3)
                    throw new IllegalArgumentException("terrain_position");
                int x=integer(p.get(0)),y=integer(p.get(1)),z=integer(p.get(2));
                var facts=new LinkedHashMap<>(cell);
                facts.remove("position"); facts.remove("observed_tick");
                var state=Map.copyOf(facts);
                Integer index=indices.get(state);
                if(index==null) { index=palette.size(); indices.put(state,index); palette.add(state); }
                var section=List.of(Math.floorDiv(x,16),Math.floorDiv(y,16),Math.floorDiv(z,16));
                int offset=Math.floorMod(x,16)*256+Math.floorMod(y,16)*16+Math.floorMod(z,16);
                sections.computeIfAbsent(section,k->new ArrayList<>())
                        .add(List.of(offset,index,integer(cell.get("observed_tick"))));
            }
            var parts=new ArrayList<Map<String,Object>>();
            for(var part:sections.entrySet())
                parts.add(Map.of("section",part.getKey(),"cells",List.copyOf(part.getValue())));
            encoded=Map.of("schema_version",1,"encoding","sparse_section_palette",
                    "unknown_is_air",false,"sample_tick",sampleTick,"known_cells",cells.size(),
                    "palette",List.copyOf(palette),"sections",List.copyOf(parts),
                    "encoding_ms",(System.nanoTime()-started)/1_000_000.0);
            cells=null; // Event history retains compact rows, not expanded per-cell maps.
            return encoded;
        }
    }

    @Override public Set<Map.Entry<String,Object>> entrySet() { return payload().entrySet(); }
}
