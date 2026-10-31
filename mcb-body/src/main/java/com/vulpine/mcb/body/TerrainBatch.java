package com.vulpine.mcb.body;

import java.util.AbstractMap;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Captured immutable facts only: lazy palette construction runs in HTTP serialization. */
final class TerrainBatch extends AbstractMap<String,Object> {
    private List<Map<String,Object>> cells;
    private final int sampleTick;
    private AwarenessSensor.SectionBatch bulk; // null unless the "sections" preset is active
    private volatile Map<String,Object> encoded;

    TerrainBatch(List<Map<String,Object>> cells,int sampleTick) {
        // PolicySensor owns immutable maps, properties and collision-box lists.
        this.cells=List.copyOf(cells); this.sampleTick=sampleTick;
    }

    TerrainBatch(List<Map<String,Object>> cells,int sampleTick,AwarenessSensor.SectionBatch bulk) {
        this(cells,sampleTick); this.bulk=bulk;
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
                int index=local(state,indices,palette);
                var section=List.of(Math.floorDiv(x,16),Math.floorDiv(y,16),Math.floorDiv(z,16));
                int offset=Math.floorMod(x,16)*256+Math.floorMod(y,16)*16+Math.floorMod(z,16);
                sections.computeIfAbsent(section,k->new ArrayList<>())
                        .add(List.of(offset,index,integer(cell.get("observed_tick"))));
            }
            var parts=new ArrayList<Map<String,Object>>();
            for(var part:sections.entrySet())
                parts.add(Map.of("section",part.getKey(),"cells",List.copyOf(part.getValue())));
            var entries=new LinkedHashMap<String,Object>();
            entries.put("schema_version",1); entries.put("encoding","sparse_section_palette");
            entries.put("unknown_is_air",false); entries.put("sample_tick",sampleTick);
            entries.put("known_cells",cells.size());
            if(bulk!=null) bulkSections(entries,indices,palette);
            entries.put("palette",List.copyOf(palette)); entries.put("sections",List.copyOf(parts));
            entries.put("encoding_ms",(System.nanoTime()-started)/1_000_000.0);
            encoded=Collections.unmodifiableMap(entries);
            bulk=null; cells=null; // Event history retains compact rows, not expanded per-cell maps.
            return encoded;
        }
    }

    private static int local(Map<String,Object> state,Map<Map<String,Object>,Integer> indices,
                             List<Map<String,Object>> palette) {
        Integer index=indices.get(state);
        if(index==null) { index=palette.size(); indices.put(state,index); palette.add(state); }
        return index;
    }

    /** Optional, backward compatible: whole sections as one state (uniform) or 4096 palette indices (dense). */
    private void bulkSections(Map<String,Object> entries,Map<Map<String,Object>,Integer> indices,
                              List<Map<String,Object>> palette) {
        var dense=new ArrayList<Map<String,Object>>();
        var uniform=new ArrayList<Map<String,Object>>();
        for(var section:bulk.sections()) {
            var key=List.of(section.x(),section.y(),section.z());
            if(section.ids()==null) {
                uniform.add(Map.of("section",key,"state",local(bulk.facts().get(section.uniform()),indices,palette),
                        "tick",section.tick()));
                continue;
            }
            var states=new ArrayList<Integer>(section.ids().length);
            for(int id:section.ids()) states.add(local(bulk.facts().get(id),indices,palette));
            dense.add(Map.of("section",key,"states",states,"tick",section.tick()));
        }
        entries.put("bulk_cells",dense.size()*AwarenessSensor.SECTION_CELLS+uniform.size()*AwarenessSensor.SECTION_CELLS);
        entries.put("dense_sections",List.copyOf(dense));
        entries.put("uniform_sections",List.copyOf(uniform));
    }

    @Override public Set<Map.Entry<String,Object>> entrySet() { return payload().entrySet(); }
}
